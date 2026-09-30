from __future__ import annotations

from types import TracebackType
from typing import Self

from agent_protocols.agora import AgoraClient, ProtocolDocument
from agent_protocols.agora.messages import AgoraResponse

from agent_protocols_industrial_use_cases.domain.executor import ExecutorService
from agent_protocols_industrial_use_cases.domain.models import (
    Award,
    Bid,
    DeliveryStatus,
    NoBid,
    TransportRequest,
)
from agent_protocols_industrial_use_cases.domain.workflow import InMemoryEventSink, WorkflowEvents
from agent_protocols_industrial_use_cases.adapters.agora import (
    TRANSPORT_PROTOCOL,
    AgoraAdapter,
)
from agent_protocols_industrial_use_cases.adapters.agora.payloads import decode_body
from agent_protocols_industrial_use_cases.adapters.payloads import (
    decode_award,
    decode_bid_request,
    encode_bid_response,
    encode_delivery,
)
from tests.support import build_executor


class StubAgoraClient(AgoraClient):
    def __init__(self, service: ExecutorService, *, shared: bool) -> None:
        self.service = service
        self.shared = shared
        self.calls = 0
        self.conversation_id = "conversation-001"

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        pass

    async def discover(self) -> dict[str, tuple[str, ...]]:
        if not self.shared:
            return {}
        return {TRANSPORT_PROTOCOL.hash: (TRANSPORT_PROTOCOL.source,)}

    async def exchange(
        self,
        body: str | dict[str, object],
        *,
        protocol: ProtocolDocument | None = None,
        provide_source: bool = False,
        multiround: bool = False,
    ) -> AgoraResponse:
        del provide_source
        self.calls += 1
        if isinstance(body, dict) and body.get("type"):
            self.shared = True
            return AgoraResponse(
                status="success",
                body={"accepted": True, "protocolHash": TRANSPORT_PROTOCOL.hash},
            )
        if protocol != TRANSPORT_PROTOCOL:
            return AgoraResponse(status="failure", error="Unsupported protocol")
        payload = decode_body(body)
        if payload.get("operation") == "request_bid":
            response = await self.service.assess_and_bid(decode_bid_request(payload))
            return AgoraResponse(
                status="success",
                body=encode_bid_response(response),
                conversation_id=self.conversation_id if multiround else None,
            )
        result = await self.service.accept_award(decode_award(payload))
        return AgoraResponse(
            status="success",
            body=encode_delivery(result),
            conversation_id=self.conversation_id if multiround else None,
        )

    async def continue_conversation(
        self, conversation_id: str, body: str | dict[str, object]
    ) -> AgoraResponse:
        assert conversation_id == self.conversation_id
        payload = decode_body(body)
        result = await self.service.accept_award(decode_award(payload))
        return AgoraResponse(status="success", body=encode_delivery(result))


async def test_agora_adapter_covers_shared_and_paper_proposal_paths() -> None:
    sink = InMemoryEventSink()
    events = WorkflowEvents(
        run_id="run-agora",
        task_id="task-001",
        protocol="agora",
        sink=sink,
    )
    available, simulator = build_executor(
        "executor-01", events=events, eta_seconds=8, energy_cost=4
    )
    unavailable, _ = build_executor("executor-02", events=events, available=False)
    clients = {
        "executor-01": StubAgoraClient(available, shared=True),
        "executor-02": StubAgoraClient(unavailable, shared=False),
    }

    def client_factory(executor_id: str, endpoint: str) -> AgoraClient:
        del endpoint
        return clients[executor_id]

    request = TransportRequest(
        "task-001", "part-42", 1, "zone-a", "welding-cell", "urgent"
    )
    async with AgoraAdapter(
        {"executor-01": "https://executor-01.test", "executor-02": "https://executor-02.test"},
        events=events,
        negotiate=True,
        client_factory=client_factory,
    ) as adapter:
        bid = await adapter.request_bid("executor-01", request)
        no_bid = await adapter.request_bid("executor-02", request)
        award = Award("task-001", "executor-01")
        delivery = await adapter.award("executor-01", award)
        replay = await adapter.award("executor-01", award)

    assert bid == Bid("task-001", "executor-01", 8, 4)
    assert no_bid == NoBid("task-001", "executor-02", "unavailable")
    assert delivery.status is DeliveryStatus.COMPLETED
    assert replay.status is DeliveryStatus.DUPLICATE_REJECTED
    assert simulator.count == 1
    assert clients["executor-01"].calls == 1
    assert clients["executor-02"].calls == 2
    event_types = [event.event_type for event in sink.events]
    assert event_types.count("agora.protocol_document.shared") == 1
    assert event_types.count("agora.protocol_document.negotiated") == 1
