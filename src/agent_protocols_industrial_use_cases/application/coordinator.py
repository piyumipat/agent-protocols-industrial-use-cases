"""Protocol-independent Welding Cell coordination workflow."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from functools import partial
from typing import Protocol

from agent_protocols_industrial_use_cases.domain.models import (
    Award,
    Bid,
    BidResponse,
    CoordinationOutcome,
    CoordinationStatus,
    DeliveryResult,
    DeliveryStatus,
    InventoryItem,
    MaterialNeed,
    NoBid,
    TransportRequest,
)
from agent_protocols_industrial_use_cases.domain.policies import select_winner
from agent_protocols_industrial_use_cases.domain.workflow import StepId, WorkflowEvents


class InventoryPort(Protocol):
    async def locate_part(self, part_id: str) -> InventoryItem: ...


class OutcomePort(Protocol):
    async def record_outcome(self, outcome: CoordinationOutcome) -> None: ...


class InterAgentPort(Protocol):
    async def request_bid(self, executor_id: str, request: TransportRequest) -> BidResponse: ...

    async def award(self, executor_id: str, award: Award) -> DeliveryResult: ...


class WeldingCellCoordinator:
    def __init__(
        self,
        *,
        inventory: InventoryPort,
        outcomes: OutcomePort,
        inter_agent: InterAgentPort,
        events: WorkflowEvents,
        response_deadline_seconds: float,
    ) -> None:
        if response_deadline_seconds <= 0:
            raise ValueError("response_deadline_seconds must be positive")
        self.inventory = inventory
        self.outcomes = outcomes
        self.inter_agent = inter_agent
        self.events = events
        self.response_deadline_seconds = response_deadline_seconds
        self._late_tasks: set[asyncio.Task[BidResponse]] = set()

    async def coordinate(
        self,
        need: MaterialNeed,
        executor_ids: Sequence[str],
    ) -> CoordinationOutcome:
        request = await self.confirm_material_need(need)
        bids, no_bids, timed_out = await self.request_and_collect_bids(request, executor_ids)
        winner = self.select_one_winner(bids)

        if winner is None:
            outcome = CoordinationOutcome(
                task_id=need.task_id,
                status=CoordinationStatus.NO_AWARD,
                winner_id=None,
                bids=tuple(bids),
                no_bids=tuple(no_bids),
                timed_out_executor_ids=tuple(timed_out),
                delivery=None,
            )
        else:
            delivery = await self.award_task(winner)
            if delivery.status is not DeliveryStatus.COMPLETED:
                raise RuntimeError("the selected Executor did not complete the awarded task")
            outcome = CoordinationOutcome(
                task_id=need.task_id,
                status=CoordinationStatus.COMPLETED,
                winner_id=winner.executor_id,
                bids=tuple(bids),
                no_bids=tuple(no_bids),
                timed_out_executor_ids=tuple(timed_out),
                delivery=delivery,
            )

        await self.record_outcome(outcome)
        return outcome

    async def confirm_material_need(self, need: MaterialNeed) -> TransportRequest:
        step = StepId.CONFIRM_MATERIAL_NEED
        self.events.started(step, actor="welding-cell", peer="inventory")
        try:
            item = await self.inventory.locate_part(need.part_id)
            if item.available_quantity < need.quantity:
                raise ValueError("insufficient inventory")
            request = TransportRequest(
                task_id=need.task_id,
                part_id=need.part_id,
                quantity=need.quantity,
                source_zone=item.source_zone,
                destination=need.destination,
                urgency=need.urgency,
            )
        except Exception as error:
            self.events.failed(
                step,
                actor="welding-cell",
                peer="inventory",
                details={"error": type(error).__name__},
            )
            raise
        self.events.completed(
            step,
            actor="welding-cell",
            peer="inventory",
            details={"source_zone": item.source_zone},
        )
        return request

    async def request_and_collect_bids(
        self,
        request: TransportRequest,
        executor_ids: Sequence[str],
    ) -> tuple[list[Bid], list[NoBid], list[str]]:
        if not executor_ids:
            raise ValueError("at least one executor is required")
        step = StepId.REQUEST_BIDS
        self.events.started(
            step,
            actor="welding-cell",
            details={"executor_count": len(executor_ids)},
        )
        tasks = {
            asyncio.create_task(self.inter_agent.request_bid(executor_id, request)): executor_id
            for executor_id in executor_ids
        }
        done, pending = await asyncio.wait(
            tasks,
            timeout=self.response_deadline_seconds,
        )

        bids: list[Bid] = []
        no_bids: list[NoBid] = []
        for task in done:
            response = task.result()
            if isinstance(response, Bid):
                bids.append(response)
            else:
                no_bids.append(response)

        timed_out = sorted(tasks[task] for task in pending)
        for task in pending:
            executor_id = tasks[task]
            self._late_tasks.add(task)
            task.add_done_callback(partial(self._record_late_response, executor_id=executor_id))
            self.events.failed(
                StepId.SUBMIT_AND_COLLECT_BIDS,
                actor="welding-cell",
                peer=executor_id,
                event_type="bid.timeout",
            )

        self.events.completed(
            step,
            actor="welding-cell",
            details={
                "bid_count": len(bids),
                "no_bid_count": len(no_bids),
                "timeout_count": len(timed_out),
            },
        )
        return bids, no_bids, timed_out

    def select_one_winner(self, bids: Sequence[Bid]) -> Bid | None:
        step = StepId.SELECT_WINNER
        self.events.started(step, actor="welding-cell")
        winner = select_winner(bids)
        self.events.completed(
            step,
            actor="welding-cell",
            details={"winner_id": None if winner is None else winner.executor_id},
        )
        return winner

    async def award_task(self, winner: Bid) -> DeliveryResult:
        step = StepId.AWARD_TASK
        self.events.started(step, actor="welding-cell", peer=winner.executor_id)
        award = Award(task_id=winner.task_id, executor_id=winner.executor_id)
        try:
            result = await self.inter_agent.award(winner.executor_id, award)
        except Exception as error:
            self.events.failed(
                step,
                actor="welding-cell",
                peer=winner.executor_id,
                details={"error": type(error).__name__},
            )
            raise
        self.events.completed(step, actor="welding-cell", peer=winner.executor_id)
        return result

    async def record_outcome(self, outcome: CoordinationOutcome) -> None:
        step = StepId.RECORD_OUTCOME
        self.events.started(step, actor="welding-cell", peer="outcome-store")
        try:
            await self.outcomes.record_outcome(outcome)
        except Exception as error:
            self.events.failed(
                step,
                actor="welding-cell",
                peer="outcome-store",
                details={"error": type(error).__name__},
            )
            raise
        self.events.completed(
            step,
            actor="welding-cell",
            peer="outcome-store",
            details={"coordination_status": outcome.status.value},
        )

    async def drain_late_responses(self) -> None:
        """Wait for timed-out transport calls so their late evidence is complete."""

        if self._late_tasks:
            await asyncio.gather(*tuple(self._late_tasks), return_exceptions=True)

    def _record_late_response(
        self,
        task: asyncio.Task[BidResponse],
        executor_id: str,
    ) -> None:
        self._late_tasks.discard(task)
        try:
            response = task.result()
        except Exception as error:  # noqa: BLE001 - evidence must retain adapter failures
            self.events.failed(
                StepId.SUBMIT_AND_COLLECT_BIDS,
                actor="welding-cell",
                peer=executor_id,
                event_type="bid.late_failed",
                details={"error": type(error).__name__},
            )
            return
        self.events.completed(
            StepId.SUBMIT_AND_COLLECT_BIDS,
            actor="welding-cell",
            peer=executor_id,
            event_type="bid.late",
            details={"response_type": type(response).__name__},
        )
