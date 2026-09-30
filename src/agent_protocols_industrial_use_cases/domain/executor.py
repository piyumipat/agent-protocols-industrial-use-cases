"""Protocol-independent Executor Agent behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from .ledger import TaskLedger
from .models import (
    Award,
    Bid,
    BidResponse,
    DeliveryResult,
    DeliveryStatus,
    ExecutorStatus,
    NoBid,
    TransportRequest,
)
from .workflow import EventLayer, StepId, WorkflowEvents


class ExecutorStatusPort(Protocol):
    async def get_status(self, executor_id: str) -> ExecutorStatus: ...


class BidDerivationPolicy(Protocol):
    def __call__(
        self, request: TransportRequest, status: ExecutorStatus, /
    ) -> Bid: ...


class DeliverySimulator(Protocol):
    async def deliver(self, award: Award) -> None: ...


class ExecutorService:
    def __init__(
        self,
        *,
        executor_id: str,
        status_port: ExecutorStatusPort,
        bid_policy: BidDerivationPolicy,
        delivery_simulator: DeliverySimulator,
        ledger: TaskLedger,
        events: WorkflowEvents,
    ) -> None:
        self.executor_id = executor_id
        self.status_port = status_port
        self.bid_policy = bid_policy
        self.delivery_simulator = delivery_simulator
        self.ledger = ledger
        self.events = events

    async def assess_and_bid(self, request: TransportRequest) -> BidResponse:
        self.events.started(
            StepId.ASSESS_EXECUTOR_STATUS,
            actor=self.executor_id,
            peer="executor-status",
        )
        try:
            status = await self.status_port.get_status(self.executor_id)
        except Exception as error:
            self.events.failed(
                StepId.ASSESS_EXECUTOR_STATUS,
                actor=self.executor_id,
                peer="executor-status",
                details={"error": type(error).__name__},
            )
            raise
        self.events.completed(
            StepId.ASSESS_EXECUTOR_STATUS,
            actor=self.executor_id,
            peer="executor-status",
            layer=EventLayer.MCP,
            event_type="mcp.executor_status.returned",
            details={"available": status.available},
        )

        self.events.started(
            StepId.SUBMIT_AND_COLLECT_BIDS,
            actor=self.executor_id,
            peer="welding-cell",
        )
        if not status.available:
            response: BidResponse = NoBid(request.task_id, self.executor_id, "unavailable")
            event_type = "bid.no_bid"
        else:
            response = self.bid_policy(request, status)
            event_type = "bid.submitted"
        self.events.completed(
            StepId.SUBMIT_AND_COLLECT_BIDS,
            actor=self.executor_id,
            peer="welding-cell",
            event_type=event_type,
        )
        return response

    async def accept_award(self, award: Award) -> DeliveryResult:
        if award.executor_id != self.executor_id:
            raise ValueError("award addressed to a different executor")

        self.events.started(
            StepId.EXECUTE_DELIVERY,
            actor=self.executor_id,
            peer="welding-cell",
        )
        if not self.ledger.accept(award.task_id, self.executor_id):
            self.events.failed(
                StepId.EXECUTE_DELIVERY,
                actor=self.executor_id,
                peer="welding-cell",
                layer=EventLayer.EXTERNAL_CONTROL,
                event_type="task_ledger.duplicate_rejected",
            )
            return DeliveryResult(
                task_id=award.task_id,
                executor_id=self.executor_id,
                status=DeliveryStatus.DUPLICATE_REJECTED,
            )

        started_at = datetime.now(UTC)
        await self.delivery_simulator.deliver(award)
        completed_at = datetime.now(UTC)
        self.ledger.complete(award.task_id, self.executor_id)
        result = DeliveryResult(
            task_id=award.task_id,
            executor_id=self.executor_id,
            status=DeliveryStatus.COMPLETED,
            started_at=started_at,
            completed_at=completed_at,
        )
        self.events.completed(
            StepId.EXECUTE_DELIVERY,
            actor=self.executor_id,
            peer="welding-cell",
            event_type="delivery.completed",
        )
        return result
