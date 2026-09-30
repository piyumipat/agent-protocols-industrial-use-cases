"""Fresh UC-003 Executor fixture for one fault run."""

from __future__ import annotations

from dataclasses import dataclass

from agent_protocols_industrial_use_cases.domain.executor import (
    BidDerivationPolicy,
    ExecutorService,
    ExecutorStatusPort,
)
from agent_protocols_industrial_use_cases.domain.ledger import TaskLedger
from agent_protocols_industrial_use_cases.domain.models import (
    Award,
    Bid,
    BidResponse,
    DeliveryResult,
    ExecutorStatus,
    TransportRequest,
)
from agent_protocols_industrial_use_cases.domain.workflow import EventLayer, StepId, WorkflowEvents


@dataclass(slots=True)
class FixedStatusPort:
    status: ExecutorStatus

    async def get_status(self, executor_id: str) -> ExecutorStatus:
        if executor_id != self.status.executor_id:
            raise ValueError("status requested for another Executor")
        return self.status


class CountingDelivery:
    def __init__(self) -> None:
        self.count = 0

    async def deliver(self, award: Award) -> None:
        del award
        self.count += 1


class CountingExecutorService(ExecutorService):
    def __init__(
        self,
        *,
        executor_id: str,
        status_port: ExecutorStatusPort,
        bid_policy: BidDerivationPolicy,
        delivery: CountingDelivery,
        ledger: TaskLedger,
        events: WorkflowEvents,
        request_marker: str | None = None,
    ) -> None:
        super().__init__(
            executor_id=executor_id,
            status_port=status_port,
            bid_policy=bid_policy,
            delivery_simulator=delivery,
            ledger=ledger,
            events=events,
        )
        self.business_dispatches = 0
        self.request_marker = request_marker
        self.bid_requests = 0

    async def assess_and_bid(self, request: TransportRequest) -> BidResponse:
        self.bid_requests += 1
        if self.request_marker is not None:
            self.events.completed(
                StepId.REQUEST_BIDS,
                actor=self.request_marker,
                peer="welding-cell",
                layer=EventLayer.APPLICATION,
                event_type="pf01.rogue_bid_request.received",
            )
        return await super().assess_and_bid(request)

    async def accept_award(self, award: Award) -> DeliveryResult:
        self.business_dispatches += 1
        return await super().accept_award(award)


def build_executor_fixture(
    *, executor_id: str, events: WorkflowEvents, request_marker: str | None = None
) -> tuple[CountingExecutorService, CountingDelivery]:
    """Keep UC-003's ledger and workflow; use a fixed available status fixture."""

    delivery = CountingDelivery()

    def derive_bid(request: TransportRequest, status: ExecutorStatus) -> Bid:
        del status
        return Bid(request.task_id, executor_id, 7.0, 3.0)

    service = CountingExecutorService(
        executor_id=executor_id,
        status_port=FixedStatusPort(ExecutorStatus(executor_id, True, "aisle-1", 80)),
        bid_policy=derive_bid,
        delivery=delivery,
        ledger=TaskLedger(),
        events=events,
        request_marker=request_marker,
    )
    return service, delivery
