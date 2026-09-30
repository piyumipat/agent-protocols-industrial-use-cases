from __future__ import annotations

import asyncio
import socket

import uvicorn

from agent_protocols_industrial_use_cases.domain.models import Award, Bid, TransportRequest
from agent_protocols_industrial_use_cases.domain.workflow import InMemoryEventSink, WorkflowEvents
from agent_protocols_industrial_use_cases.adapters.a2a import A2AAdapter, create_executor_app
from tests.support import build_executor


async def test_a2a_adapter_uses_real_jsonrpc_http_transport() -> None:
    sink = InMemoryEventSink()
    events = WorkflowEvents(
        run_id="run-http",
        task_id="task-http",
        protocol="a2a",
        sink=sink,
    )
    executor, simulator = build_executor(
        "executor-http",
        events=events,
        eta_seconds=7,
        energy_cost=3,
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    app = create_executor_app(executor, rpc_url=f"{base_url}/a2a")
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="error", lifespan="on")
    )
    server_task = asyncio.create_task(server.serve(sockets=[listener]))

    try:
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(0.01)
        request = TransportRequest(
            "task-http",
            "part-42",
            1,
            "zone-a",
            "welding-cell",
            "urgent",
        )
        async with A2AAdapter(
            {"executor-http": base_url},
            events=events,
        ) as adapter:
            bid = await adapter.request_bid("executor-http", request)
            delivery = await adapter.award(
                "executor-http",
                Award("task-http", "executor-http"),
            )
    finally:
        server.should_exit = True
        async with asyncio.timeout(5):
            await server_task
        listener.close()

    assert bid == Bid("task-http", "executor-http", 7, 3)
    assert delivery.executor_id == "executor-http"
    assert simulator.count == 1

