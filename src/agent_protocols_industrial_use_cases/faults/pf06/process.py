"""Separate Executor process that stops after durable award acceptance."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import socket
import ssl
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any

import httpx
import uvicorn
from agent_protocols.anp import E1Identity, create_signature_verifier
from aiohttp import web

from agent_protocols_industrial_use_cases.domain.executor import ExecutorService
from agent_protocols_industrial_use_cases.domain.ledger import TaskLedger
from agent_protocols_industrial_use_cases.domain.models import (
    Award,
    Bid,
    ExecutorStatus,
    TransportRequest,
)
from agent_protocols_industrial_use_cases.domain.workflow import InMemoryEventSink, WorkflowEvents
from agent_protocols_industrial_use_cases.faults.common.fixture import FixedStatusPort
from agent_protocols_industrial_use_cases.adapters.a2a import create_executor_app as a2a_app
from agent_protocols_industrial_use_cases.adapters.agora import (
    TRANSPORT_PROTOCOL,
)
from agent_protocols_industrial_use_cases.adapters.agora import (
    create_executor_app as agora_app,
)
from agent_protocols_industrial_use_cases.adapters.anp import create_executor_app as anp_app
from agent_protocols_industrial_use_cases.runtime.config import ProtocolName

CRASH_EXIT_CODE = 86


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    protocol: ProtocolName
    run_id: str
    task_id: str
    port: int
    acceptance_audit: Path
    crash_on_acceptance: bool
    tls_cert: Path | None = None
    tls_key: Path | None = None
    anp_identity: E1Identity | None = None
    crash_release_event: Any | None = None
    caller_did: str | None = None
    caller_document: dict[str, object] | None = None
    jwt_private: str | None = None
    jwt_public: str | None = None

    @property
    def origin(self) -> str:
        scheme = "http" if self.protocol is ProtocolName.A2A else "https"
        host = "127.0.0.1" if self.protocol is ProtocolName.A2A else "localhost"
        return f"{scheme}://{host}:{self.port}"


class CrashDelivery:
    def __init__(self, config: WorkerConfig) -> None:
        self.config = config

    async def deliver(self, award: Award) -> None:
        with self.config.acceptance_audit.open("x", encoding="utf-8") as audit:
            json.dump(
                {
                    "run_id": self.config.run_id,
                    "task_id": award.task_id,
                    "executor_id": award.executor_id,
                    "state": "accepted_before_delivery",
                },
                audit,
            )
            audit.write("\n")
            audit.flush()
            os.fsync(audit.fileno())
        if self.config.crash_release_event is not None:
            released = await asyncio.to_thread(self.config.crash_release_event.wait, 10)
            if not released:
                raise TimeoutError("caller did not receive the A2A Task ID before crash")
        os._exit(CRASH_EXIT_CODE)


class IdleDelivery:
    async def deliver(self, award: Award) -> None:
        raise RuntimeError(f"recovery process must not receive a new award: {award.task_id}")


def _serve_worker(config: WorkerConfig) -> None:
    executor_id = "executor-01"
    events = WorkflowEvents(
        run_id=config.run_id,
        task_id=config.task_id,
        protocol=config.protocol.value,
        sink=InMemoryEventSink(),
    )

    def derive_bid(request: TransportRequest, status: ExecutorStatus) -> Bid:
        del status
        return Bid(request.task_id, executor_id, 7.0, 3.0)

    service = ExecutorService(
        executor_id=executor_id,
        status_port=FixedStatusPort(ExecutorStatus(executor_id, True, "aisle-1", 80)),
        bid_policy=derive_bid,
        delivery_simulator=(
            CrashDelivery(config) if config.crash_on_acceptance else IdleDelivery()
        ),
        ledger=TaskLedger(),
        events=events,
    )
    if config.protocol is ProtocolName.A2A:
        app = a2a_app(service, rpc_url=f"{config.origin}/a2a")
        uvicorn.run(app, host="127.0.0.1", port=config.port, log_level="critical")
        return
    if config.tls_cert is None or config.tls_key is None:
        raise ValueError("HTTPS workers need a certificate and key")
    if config.protocol is ProtocolName.AGORA:
        app = agora_app(service, protocol=TRANSPORT_PROTOCOL)
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=config.port,
            log_level="critical",
            ssl_certfile=str(config.tls_cert),
            ssl_keyfile=str(config.tls_key),
        )
        return
    if (
        config.anp_identity is None
        or config.caller_did is None
        or config.caller_document is None
        or config.jwt_private is None
        or config.jwt_public is None
    ):
        raise ValueError("ANP worker credentials are incomplete")

    async def resolve_caller(did: str) -> dict[str, object]:
        if did != config.caller_did or config.caller_document is None:
            raise ValueError("unknown caller DID")
        return config.caller_document

    verifier = create_signature_verifier(
        jwt_private_key=config.jwt_private,
        jwt_public_key=config.jwt_public,
        did_resolver=resolve_caller,
    )
    application = anp_app(
        service,
        origin=config.origin,
        identity=config.anp_identity,
        verifier=verifier,
        authorize_did=lambda did: did == config.caller_did,
    )
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(config.tls_cert, config.tls_key)
    web.run_app(
        application,
        host="127.0.0.1",
        port=config.port,
        ssl_context=context,
        handle_signals=False,
        print=None,
    )


def free_port() -> int:
    listener = socket.socket()
    try:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])
    finally:
        listener.close()


class ExecutorProcess:
    def __init__(self, config: WorkerConfig) -> None:
        self.config = config
        self.process = multiprocessing.get_context("spawn").Process(
            target=_serve_worker, args=(config,), daemon=True
        )

    async def start(self, *, verify: ssl.SSLContext | bool = True) -> None:
        self.process.start()
        if self.config.protocol is ProtocolName.A2A:
            path = "/.well-known/agent-card.json"
        elif self.config.protocol is ProtocolName.ANP:
            path = "/.well-known/agent-descriptions"
        else:
            path = "/agora/wellknown"
        deadline = monotonic() + 10
        async with httpx.AsyncClient(verify=verify, timeout=1) as client:
            while monotonic() < deadline:
                if self.process.exitcode is not None:
                    raise RuntimeError(
                        f"Executor process exited before readiness: {self.process.exitcode}"
                    )
                try:
                    response = await client.get(f"{self.config.origin}{path}")
                    if response.status_code == 200:
                        return
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    pass
                await asyncio.sleep(0.05)
        raise TimeoutError("Executor process did not become ready")

    async def wait_for_crash(self) -> int:
        await asyncio.to_thread(self.process.join, 5)
        if self.process.exitcode != CRASH_EXIT_CODE:
            raise RuntimeError(f"Executor exited at wrong boundary: {self.process.exitcode}")
        return self.process.exitcode

    async def close(self) -> None:
        if self.process.is_alive():
            self.process.terminate()
            await asyncio.to_thread(self.process.join, 5)
        if self.process.is_alive():
            self.process.kill()
            await asyncio.to_thread(self.process.join, 5)
