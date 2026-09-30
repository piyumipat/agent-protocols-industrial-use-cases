from __future__ import annotations

import asyncio
import socket

import uvicorn

from agent_protocols_industrial_use_cases.domain.models import Award, Bid, TransportRequest
from agent_protocols_industrial_use_cases.domain.workflow import InMemoryEventSink, WorkflowEvents
from agent_protocols_industrial_use_cases.adapters.agora import (
    TRANSPORT_PROTOCOL,
    AgoraAdapter,
    create_executor_app,
)
from tests.support import build_executor


async def test_agora_adapter_uses_real_http_and_paper_protocol_proposal() -> None:
    sink = InMemoryEventSink()
    events = WorkflowEvents(
        run_id="run-agora-http",
        task_id="task-agora-http",
        protocol="agora",
        sink=sink,
    )
    executor, simulator = build_executor(
        "executor-http", events=events, eta_seconds=7, energy_cost=3
    )
    app = create_executor_app(
        executor,
        protocol=TRANSPORT_PROTOCOL,
        pre_shared=False,
        allow_proposals=True,
        proposal_policy=lambda document, purpose: (
            document.hash == TRANSPORT_PROTOCOL.hash
            and purpose == "UC-003 transport bidding and award"
        ),
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}/agora"
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="error", lifespan="on")
    )
    server_task = asyncio.create_task(server.serve(sockets=[listener]))

    try:
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(0.01)
        request = TransportRequest(
            "task-agora-http",
            "part-42",
            1,
            "zone-a",
            "welding-cell",
            "urgent",
        )
        async with AgoraAdapter(
            {"executor-http": base_url},
            events=events,
            negotiate=True,
            require_https=False,
        ) as adapter:
            bid = await adapter.request_bid("executor-http", request)
            delivery = await adapter.award(
                "executor-http", Award("task-agora-http", "executor-http")
            )
    finally:
        server.should_exit = True
        async with asyncio.timeout(5):
            await server_task
        listener.close()

    assert bid == Bid("task-agora-http", "executor-http", 7, 3)
    assert delivery.executor_id == "executor-http"
    assert simulator.count == 1
    event_types = [event.event_type for event in sink.events]
    assert "agora.wellknown.fetched" in event_types
    assert "agora.protocol_document.negotiated" in event_types
