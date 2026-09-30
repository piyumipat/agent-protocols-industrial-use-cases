from __future__ import annotations

from types import TracebackType
from typing import Self, cast

from agent_protocols.anp import (
    ANPClient,
    DIDWbaAuthHeader,
    TextMethod,
    VerifiedAgent,
    create_agent_description,
    create_e1_identity,
)

from agent_protocols_industrial_use_cases.domain.executor import ExecutorService
from agent_protocols_industrial_use_cases.domain.models import (
    Award,
    Bid,
    DeliveryStatus,
    NoBid,
    TransportRequest,
)
from agent_protocols_industrial_use_cases.domain.workflow import InMemoryEventSink, WorkflowEvents
from agent_protocols_industrial_use_cases.adapters.anp import TRANSPORT_METHOD, ANPAdapter
from agent_protocols_industrial_use_cases.adapters.anp.payloads import decode_text, encode_text
from agent_protocols_industrial_use_cases.adapters.payloads import (
    decode_award,
    decode_bid_request,
    encode_bid_response,
    encode_delivery,
)
from tests.support import build_executor


class StubANPClient(ANPClient):
    def __init__(self, agent: VerifiedAgent, service: ExecutorService) -> None:
        self.agent = agent
        self.service = service
        self.calls = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        pass

    async def discover(self, domain: str) -> list[VerifiedAgent]:
        assert domain in self.agent.description_url
        return [self.agent]

    async def call_text(
        self,
        agent: VerifiedAgent,
        method: TextMethod,
        value: str,
        *,
        force_new_signature: bool = False,
    ) -> str:
        del force_new_signature
        assert agent == self.agent
        assert method == TRANSPORT_METHOD
        self.calls += 1
        payload = decode_text(value)
        if payload.get("operation") == "request_bid":
            response = await self.service.assess_and_bid(decode_bid_request(payload))
            return encode_text(encode_bid_response(response))
        result = await self.service.accept_award(decode_award(payload))
        return encode_text(encode_delivery(result))


def verified_agent(executor_id: str) -> VerifiedAgent:
    origin = f"https://{executor_id}.test"
    description_url = f"{origin}/agents/{executor_id}/description.json"
    identity = create_e1_identity(
        f"{executor_id}.test",
        path_segments=["agents", executor_id],
        agent_description_url=description_url,
    )
    description = create_agent_description(
        url=description_url,
        name=executor_id,
        did=identity.did,
    )
    return VerifiedAgent(description_url, description, identity.document)


async def test_anp_adapter_exchanges_bid_no_bid_award_and_duplicate() -> None:
    sink = InMemoryEventSink()
    events = WorkflowEvents(
        run_id="run-anp",
        task_id="task-001",
        protocol="anp",
        sink=sink,
    )
    available, simulator = build_executor(
        "executor-01", events=events, eta_seconds=8, energy_cost=4
    )
    unavailable, _ = build_executor("executor-02", events=events, available=False)
    services = {"executor-01": available, "executor-02": unavailable}
    clients = {
        executor_id: StubANPClient(verified_agent(executor_id), service)
        for executor_id, service in services.items()
    }

    def client_factory(
        executor_id: str, authenticator: DIDWbaAuthHeader
    ) -> ANPClient:
        del authenticator
        return clients[executor_id]

    placeholder_auth = cast(DIDWbaAuthHeader, object())
    request = TransportRequest(
        "task-001", "part-42", 1, "zone-a", "welding-cell", "urgent"
    )
    async with ANPAdapter(
        {
            "executor-01": "executor-01.test",
            "executor-02": "executor-02.test",
        },
        authenticators={
            "executor-01": placeholder_auth,
            "executor-02": placeholder_auth,
        },
        events=events,
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
    assert clients["executor-01"].calls == 3
    event_types = [event.event_type for event in sink.events]
    assert event_types.count("anp.discovery.completed") == 2
    assert event_types.count("anp.did.verified") == 2
    assert "anp.bid_response.received" in event_types
    assert "anp.delivery.received" in event_types
