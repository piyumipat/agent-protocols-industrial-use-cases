from __future__ import annotations

from contextlib import AsyncExitStack

import httpx
from agent_protocols.a2a import A2AClient

from agent_protocols_industrial_use_cases.domain.models import (
    Award,
    Bid,
    DeliveryStatus,
    NoBid,
    TransportRequest,
)
from agent_protocols_industrial_use_cases.domain.workflow import InMemoryEventSink, WorkflowEvents
from agent_protocols_industrial_use_cases.adapters.a2a import A2AAdapter, create_executor_app
from tests.support import build_executor


async def test_a2a_adapter_exchanges_bid_no_bid_award_and_duplicate() -> None:
    sink = InMemoryEventSink()
    events = WorkflowEvents(
        run_id="run-a2a",
        task_id="task-001",
        protocol="a2a",
        sink=sink,
    )
    available, simulator = build_executor(
        "executor-01",
        events=events,
        eta_seconds=8,
        energy_cost=4,
    )
    unavailable, _ = build_executor(
        "executor-02",
        events=events,
        available=False,
    )
    endpoints = {
        "executor-01": "http://executor-01.test",
        "executor-02": "http://executor-02.test",
    }
    apps = {
        executor_id: create_executor_app(
            service,
            rpc_url=f"{endpoints[executor_id]}/a2a",
        )
        for executor_id, service in {
            "executor-01": available,
            "executor-02": unavailable,
        }.items()
    }

    async with AsyncExitStack() as stack:
        http_clients = {
            executor_id: await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=apps[executor_id]),
                    base_url=endpoint,
                )
            )
            for executor_id, endpoint in endpoints.items()
        }

        def client_factory(executor_id: str, endpoint: str) -> A2AClient:
            return A2AClient(endpoint, http_client=http_clients[executor_id])

        request = TransportRequest(
            "task-001",
            "part-42",
            1,
            "zone-a",
            "welding-cell",
            "urgent",
        )
        async with A2AAdapter(
            endpoints,
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
    event_types = [event.event_type for event in sink.events]
    assert event_types.count("a2a.agent_card.fetched") == 2
    assert "a2a.bid_response.received" in event_types
    delivery_events = [
        event for event in sink.events if event.event_type == "a2a.delivery.received"
    ]
    assert delivery_events[0].details["a2a_task_id"]

