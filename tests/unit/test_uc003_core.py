from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass

from agent_protocols_industrial_use_cases.application.coordinator import WeldingCellCoordinator
from agent_protocols_industrial_use_cases.domain.executor import ExecutorService
from agent_protocols_industrial_use_cases.domain.ledger import TaskLedger
from agent_protocols_industrial_use_cases.domain.models import (
    Award,
    Bid,
    BidResponse,
    CoordinationOutcome,
    CoordinationStatus,
    DeliveryResult,
    DeliveryStatus,
    ExecutorStatus,
    InventoryItem,
    MaterialNeed,
    TransportRequest,
)
from agent_protocols_industrial_use_cases.domain.workflow import (
    EventStatus,
    InMemoryEventSink,
    StepId,
    WorkflowEvents,
)


class FakeInventory:
    async def locate_part(self, part_id: str) -> InventoryItem:
        return InventoryItem(part_id=part_id, available_quantity=10, source_zone="zone-a")


class RecordingOutcomeStore:
    def __init__(self) -> None:
        self.outcomes: list[CoordinationOutcome] = []

    async def record_outcome(self, outcome: CoordinationOutcome) -> None:
        self.outcomes.append(outcome)


@dataclass
class FixedStatusPort:
    status: ExecutorStatus

    async def get_status(self, executor_id: str) -> ExecutorStatus:
        if executor_id != self.status.executor_id:
            raise ValueError("unexpected executor")
        return self.status


class CountingDeliverySimulator:
    def __init__(self) -> None:
        self.count = 0

    async def deliver(self, award: Award) -> None:
        self.count += 1


class InProcessInterAgentPort:
    def __init__(
        self,
        executors: dict[str, ExecutorService],
        delays: dict[str, float] | None = None,
    ) -> None:
        self.executors = executors
        self.delays = delays or {}
        self.awards: list[Award] = []

    async def request_bid(
        self, executor_id: str, request: TransportRequest
    ) -> BidResponse:
        delay = self.delays.get(executor_id, 0)
        if delay:
            await asyncio.sleep(delay)
        return await self.executors[executor_id].assess_and_bid(request)

    async def award(self, executor_id: str, award: Award) -> DeliveryResult:
        self.awards.append(award)
        return await self.executors[executor_id].accept_award(award)


class UC003CoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.need = MaterialNeed(
            task_id="task-001",
            part_id="part-42",
            quantity=1,
            destination="welding-cell",
            urgency="urgent",
        )
        self.sink = InMemoryEventSink()
        self.events = WorkflowEvents(
            run_id="run-001",
            task_id=self.need.task_id,
            protocol="test",
            sink=self.sink,
        )

    def make_executor(
        self,
        executor_id: str,
        *,
        available: bool = True,
        eta_seconds: float = 10,
        energy_cost: float = 5,
    ) -> tuple[ExecutorService, CountingDeliverySimulator]:
        status = ExecutorStatus(
            executor_id=executor_id,
            available=available,
            position="aisle-1",
            energy_level=80,
        )
        simulator = CountingDeliverySimulator()

        def derive_bid(request: TransportRequest, _: ExecutorStatus) -> Bid:
            return Bid(
                task_id=request.task_id,
                executor_id=executor_id,
                eta_seconds=eta_seconds,
                energy_cost=energy_cost,
            )

        return (
            ExecutorService(
                executor_id=executor_id,
                status_port=FixedStatusPort(status),
                bid_policy=derive_bid,
                delivery_simulator=simulator,
                ledger=TaskLedger(),
                events=self.events,
            ),
            simulator,
        )

    def make_coordinator(
        self,
        executors: dict[str, ExecutorService],
        *,
        deadline: float = 0.1,
        delays: dict[str, float] | None = None,
    ) -> tuple[WeldingCellCoordinator, RecordingOutcomeStore, InProcessInterAgentPort]:
        outcome_store = RecordingOutcomeStore()
        transport = InProcessInterAgentPort(executors, delays)
        coordinator = WeldingCellCoordinator(
            inventory=FakeInventory(),
            outcomes=outcome_store,
            inter_agent=transport,
            events=self.events,
            response_deadline_seconds=deadline,
        )
        return coordinator, outcome_store, transport

    async def test_success_completes_all_eight_steps_and_persists_outcome(self) -> None:
        executor_a, simulator_a = self.make_executor("executor-a", eta_seconds=8)
        executor_b, simulator_b = self.make_executor("executor-b", eta_seconds=12)
        coordinator, store, transport = self.make_coordinator(
            {"executor-a": executor_a, "executor-b": executor_b}
        )

        outcome = await coordinator.coordinate(self.need, ["executor-a", "executor-b"])

        self.assertEqual(outcome.status, CoordinationStatus.COMPLETED)
        self.assertEqual(outcome.winner_id, "executor-a")
        self.assertEqual(store.outcomes, [outcome])
        self.assertEqual(transport.awards, [Award("task-001", "executor-a")])
        self.assertEqual(simulator_a.count, 1)
        self.assertEqual(simulator_b.count, 0)
        completed_steps = {
            event.step_id
            for event in self.sink.events
            if event.status is EventStatus.COMPLETED
        }
        self.assertEqual(completed_steps, set(StepId))

    async def test_no_valid_bid_records_no_award(self) -> None:
        executor_a, simulator_a = self.make_executor("executor-a", available=False)
        executor_b, simulator_b = self.make_executor("executor-b", available=False)
        coordinator, store, transport = self.make_coordinator(
            {"executor-a": executor_a, "executor-b": executor_b}
        )

        outcome = await coordinator.coordinate(self.need, ["executor-a", "executor-b"])

        self.assertEqual(outcome.status, CoordinationStatus.NO_AWARD)
        self.assertIsNone(outcome.winner_id)
        self.assertEqual(len(outcome.no_bids), 2)
        self.assertEqual(store.outcomes, [outcome])
        self.assertEqual(transport.awards, [])
        self.assertEqual(simulator_a.count + simulator_b.count, 0)

    async def test_tied_bids_use_lexicographically_lowest_executor_id(self) -> None:
        executor_b, _ = self.make_executor("executor-b", eta_seconds=8, energy_cost=4)
        executor_a, simulator_a = self.make_executor(
            "executor-a", eta_seconds=8, energy_cost=4
        )
        coordinator, _, _ = self.make_coordinator(
            {"executor-b": executor_b, "executor-a": executor_a}
        )

        outcome = await coordinator.coordinate(self.need, ["executor-b", "executor-a"])

        self.assertEqual(outcome.winner_id, "executor-a")
        self.assertEqual(simulator_a.count, 1)

    async def test_late_bid_is_recorded_but_excluded_from_selection(self) -> None:
        on_time, on_time_simulator = self.make_executor("executor-on-time", eta_seconds=20)
        late, late_simulator = self.make_executor("executor-late", eta_seconds=1)
        coordinator, _, _ = self.make_coordinator(
            {"executor-on-time": on_time, "executor-late": late},
            deadline=0.01,
            delays={"executor-late": 0.03},
        )

        outcome = await coordinator.coordinate(
            self.need,
            ["executor-on-time", "executor-late"],
        )
        await coordinator.drain_late_responses()

        self.assertEqual(outcome.winner_id, "executor-on-time")
        self.assertEqual(outcome.timed_out_executor_ids, ("executor-late",))
        self.assertEqual(on_time_simulator.count, 1)
        self.assertEqual(late_simulator.count, 0)
        late_events = [
            event
            for event in self.sink.events
            if event.event_type == "bid.late" and event.peer == "executor-late"
        ]
        self.assertEqual(len(late_events), 1)

    async def test_duplicate_award_does_not_execute_second_delivery(self) -> None:
        executor, simulator = self.make_executor("executor-a")
        award = Award(task_id=self.need.task_id, executor_id="executor-a")

        first = await executor.accept_award(award)
        replay = await executor.accept_award(award)

        self.assertEqual(first.status, DeliveryStatus.COMPLETED)
        self.assertEqual(replay.status, DeliveryStatus.DUPLICATE_REJECTED)
        self.assertEqual(simulator.count, 1)
        self.assertTrue(
            any(event.event_type == "task_ledger.duplicate_rejected" for event in self.sink.events)
        )


if __name__ == "__main__":
    unittest.main()
