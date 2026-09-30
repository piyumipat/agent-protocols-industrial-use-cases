from __future__ import annotations

from dataclasses import dataclass

from agent_protocols_industrial_use_cases.domain.executor import ExecutorService
from agent_protocols_industrial_use_cases.domain.ledger import TaskLedger
from agent_protocols_industrial_use_cases.domain.models import (
    Award,
    Bid,
    ExecutorStatus,
    TransportRequest,
)
from agent_protocols_industrial_use_cases.domain.workflow import WorkflowEvents


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


def build_executor(
    executor_id: str,
    *,
    events: WorkflowEvents,
    available: bool = True,
    eta_seconds: float = 10,
    energy_cost: float = 5,
) -> tuple[ExecutorService, CountingDeliverySimulator]:
    status = ExecutorStatus(executor_id, available, "aisle-1", 80)
    simulator = CountingDeliverySimulator()

    def derive_bid(request: TransportRequest, _: ExecutorStatus) -> Bid:
        return Bid(
            request.task_id,
            executor_id,
            eta_seconds,
            energy_cost,
        )

    return (
        ExecutorService(
            executor_id=executor_id,
            status_port=FixedStatusPort(status),
            bid_policy=derive_bid,
            delivery_simulator=simulator,
            ledger=TaskLedger(),
            events=events,
        ),
        simulator,
    )

