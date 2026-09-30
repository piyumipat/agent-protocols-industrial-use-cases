"""End-to-end smoke runs for the UC-003 pilot manifests."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import ssl
import sys
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiohttp
import uvicorn
from agent_protocols.anp import (
    ANPClient,
    DIDWbaAuthHeader,
    create_e1_identity,
    create_http_authenticator,
    create_signature_verifier,
)
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from mcp import StdioServerParameters
from starlette.applications import Starlette

from agent_protocols_industrial_use_cases.application.coordinator import WeldingCellCoordinator
from agent_protocols_industrial_use_cases.domain.executor import ExecutorService
from agent_protocols_industrial_use_cases.domain.ledger import TaskLedger
from agent_protocols_industrial_use_cases.domain.models import (
    Award,
    Bid,
    CoordinationOutcome,
    ExecutorStatus,
    MaterialNeed,
    TransportRequest,
)
from agent_protocols_industrial_use_cases.domain.workflow import WorkflowEvents
from agent_protocols_industrial_use_cases.tools.mcp.clients import (
    ExecutorStatusMCPClient,
    InventoryMCPClient,
    OutcomeMCPClient,
)
from agent_protocols_industrial_use_cases.adapters.a2a import (
    create_executor_app as create_a2a_app,
)
from agent_protocols_industrial_use_cases.adapters.agora import (
    TRANSPORT_PROTOCOL,
)
from agent_protocols_industrial_use_cases.adapters.agora import (
    create_executor_app as create_agora_app,
)
from agent_protocols_industrial_use_cases.adapters.anp import (
    create_executor_app as create_anp_app,
)

from agent_protocols_industrial_use_cases.application.protocol_selection import create_inter_agent_adapter
from .config import (
    ProtocolMode,
    ProtocolName,
    RunManifest,
    load_manifest,
)
from agent_protocols_industrial_use_cases.evidence.artifacts import RunArtifacts
from agent_protocols_industrial_use_cases.evidence.validation import validate_run_evidence


class SmokeConfigurationError(ValueError):
    """A manifest is not one of the supported smoke scenarios."""


@dataclass(frozen=True, slots=True)
class SmokeRunResult:
    """Terminal result and evidence location from one smoke scenario."""

    manifest: RunManifest
    outcome: CoordinationOutcome
    outcomes: tuple[CoordinationOutcome, ...]
    artifact_directory: Path


class _DeliverySimulator:
    def __init__(self) -> None:
        self.count = 0

    async def deliver(self, award: Award) -> None:
        del award
        self.count += 1


@dataclass(slots=True)
class _UvicornHandle:
    server: uvicorn.Server
    task: asyncio.Task[None]
    listener: socket.socket

    async def stop(self) -> None:
        self.server.should_exit = True
        try:
            async with asyncio.timeout(5):
                await self.task
        finally:
            self.listener.close()


@dataclass(slots=True)
class _AioHttpHandle:
    runner: web.AppRunner

    async def stop(self) -> None:
        await self.runner.cleanup()


@dataclass(frozen=True, slots=True)
class _ANPRuntime:
    authenticators: Mapping[str, DIDWbaAuthHeader]
    client_factory: Callable[[str, DIDWbaAuthHeader], ANPClient]


async def run_smoke_scenario(manifest_path: Path, results_root: Path) -> SmokeRunResult:
    """Run one supported pilot scenario and persist its complete evidence set.

    The smoke runner deliberately owns MCP subprocesses through the reference-kit
    ``StdioServerParameters`` transport. Protocol Executor servers run over real
    loopback HTTP sockets and are stopped before the MCP subprocesses are closed.
    """

    manifest = load_manifest(manifest_path)
    return await run_smoke_manifest(
        manifest,
        results_root,
        repo_root=manifest_path.resolve().parents[2],
        run_kind="measured",
    )


async def run_smoke_manifest(
    manifest: RunManifest,
    results_root: Path,
    *,
    repo_root: Path,
    run_kind: str = "measured",
) -> SmokeRunResult:
    """Run a validated manifest, including all configured rounds."""

    _validate_smoke_manifest(manifest)
    runtime_manifest, listeners = _runtime_manifest(manifest)

    with RunArtifacts(results_root, runtime_manifest) as artifacts:
        events = WorkflowEvents(
            run_id=runtime_manifest.run_id,
            task_id=runtime_manifest.task_id,
            protocol=runtime_manifest.protocol.value,
            sink=artifacts.event_sink,
        )
        async with AsyncExitStack() as stack:
            inventory = await stack.enter_async_context(
                InventoryMCPClient(
                    _stdio_target(
                        repo_root,
                        "agent_protocols_industrial_use_cases.tools.mcp.inventory_server",
                        "--fixture",
                        _fixture_path(repo_root, runtime_manifest.inventory_fixture),
                    ),
                    events=events,
                )
            )
            status_clients: dict[str, ExecutorStatusMCPClient] = {}
            for executor_id in runtime_manifest.executor_ids:
                status_clients[executor_id] = await stack.enter_async_context(
                    ExecutorStatusMCPClient(
                        _stdio_target(
                            repo_root,
                            "agent_protocols_industrial_use_cases.tools.mcp.status_server",
                            "--fixture",
                            _fixture_path(repo_root, runtime_manifest.executor_fixture),
                        ),
                        events=events,
                    )
                )

            services = _build_services(runtime_manifest, status_clients, events)
            anp_runtime: _ANPRuntime | None = None
            if runtime_manifest.protocol is ProtocolName.ANP:
                anp_runtime = await _start_anp_servers(
                    runtime_manifest,
                    services,
                    listeners,
                    stack,
                )
            else:
                handles = await _start_protocol_servers(runtime_manifest, services, listeners)
                for handle in handles:
                    await stack.enter_async_context(_AsyncCallback(handle.stop))

            adapter = create_inter_agent_adapter(
                runtime_manifest,
                events=events,
                anp_authenticators=(
                    None if anp_runtime is None else anp_runtime.authenticators
                ),
                anp_client_factory=(
                    None if anp_runtime is None else anp_runtime.client_factory
                ),
            )
            outcomes: list[CoordinationOutcome] = []
            async with adapter:
                for round_number in range(1, runtime_manifest.rounds + 1):
                    round_task_id = _round_task_id(runtime_manifest.task_id, round_number)
                    events.task_id = round_task_id
                    outcome_filename = (
                        "mcp-outcome.json"
                        if runtime_manifest.rounds == 1
                        else f"mcp-outcome-round-{round_number:02d}.json"
                    )
                    async with OutcomeMCPClient(
                        _stdio_target(
                            repo_root,
                            "agent_protocols_industrial_use_cases.tools.mcp.outcome_server",
                            "--output",
                            artifacts.directory / outcome_filename,
                        ),
                        events=events,
                    ) as outcome_store:
                        coordinator = WeldingCellCoordinator(
                            inventory=inventory,
                            outcomes=outcome_store,
                            inter_agent=adapter,
                            events=events,
                            response_deadline_seconds=(
                                runtime_manifest.response_deadline_seconds
                            ),
                        )
                        outcome = await coordinator.coordinate(
                            MaterialNeed(
                                task_id=round_task_id,
                                part_id="part-42",
                                quantity=1,
                                destination="welding-cell",
                                urgency="urgent",
                            ),
                            runtime_manifest.executor_ids,
                        )
                        await coordinator.drain_late_responses()
                    outcomes.append(outcome)
                    artifacts.write_round_outcome(round_number, outcome)

            outcome = outcomes[-1]
            artifacts.write_outcome(outcome)
            artifacts.write_outcomes(outcomes)
            artifacts.write_protocol_summary(
                {
                    "smoke": True,
                    "run_kind": run_kind,
                    "rounds": runtime_manifest.rounds,
                    "endpoint_source": runtime_manifest.endpoint_source,
                    "protocol_mode": runtime_manifest.protocol_mode,
                    "introduced_executor_id": runtime_manifest.introduced_executor_id,
                    "real_mcp_stdio": True,
                    "real_loopback_http": True,
                    "expected_winner": "executor-01",
                }
            )
            validation = validate_run_evidence(artifacts.directory)
            artifacts.write_validation(validation.to_dict())
            validation.require_valid()

    return SmokeRunResult(runtime_manifest, outcome, tuple(outcomes), artifacts.directory)


def _round_task_id(task_id: str, round_number: int) -> str:
    return task_id if round_number == 1 else f"{task_id}-r{round_number:02d}"


def _validate_smoke_manifest(manifest: RunManifest) -> None:
    if manifest.protocol is ProtocolName.A2A:
        if manifest.protocol_mode is not ProtocolMode.NATIVE:
            raise SmokeConfigurationError("A2A smoke requires native mode")
        return
    if manifest.protocol is ProtocolName.ANP:
        if manifest.protocol_mode is not ProtocolMode.NATIVE:
            raise SmokeConfigurationError("ANP smoke requires native mode")
        return
    if manifest.protocol is ProtocolName.AGORA:
        if manifest.protocol_mode not in {ProtocolMode.PRE_SHARED, ProtocolMode.PROPOSAL}:
            raise SmokeConfigurationError("Agora smoke requires pre-shared or proposal mode")
        return
    raise SmokeConfigurationError(
        "unsupported smoke protocol"
    )


def _runtime_manifest(manifest: RunManifest) -> tuple[RunManifest, list[socket.socket]]:
    listeners = [_listener() for _ in manifest.executor_ids]
    endpoint_path = "/agora" if manifest.protocol is ProtocolName.AGORA else ""
    endpoint_scheme = "https" if manifest.protocol is ProtocolName.ANP else "http"
    endpoints = {
        executor_id: (
            f"{endpoint_scheme}://localhost:{listener.getsockname()[1]}{endpoint_path}"
            if manifest.protocol is ProtocolName.ANP
            else f"{endpoint_scheme}://127.0.0.1:{listener.getsockname()[1]}{endpoint_path}"
        )
        for executor_id, listener in zip(manifest.executor_ids, listeners, strict=True)
    }
    service_ports = dict(manifest.service_ports)
    service_ports.update(
        {
            executor_id: listener.getsockname()[1]
            for executor_id, listener in zip(manifest.executor_ids, listeners, strict=True)
        }
    )
    return replace(manifest, endpoints=endpoints, service_ports=service_ports), listeners


def _listener() -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    return listener


async def _start_anp_servers(
    manifest: RunManifest,
    services: dict[str, ExecutorService],
    listeners: list[socket.socket],
    stack: AsyncExitStack,
) -> _ANPRuntime:
    ports = [int(listener.getsockname()[1]) for listener in listeners]
    for listener in listeners:
        listener.close()
    temporary_directory = tempfile.TemporaryDirectory(prefix="uc003-anp-")
    stack.callback(temporary_directory.cleanup)
    material_directory = Path(temporary_directory.name)
    server_ssl, client_ssl = _write_tls_material(material_directory)

    caller = create_e1_identity(
        "localhost",
        port=443,
        path_segments=["agents", "welding-cell"],
    )
    caller_document_path = material_directory / "caller-did.json"
    caller_key_path = material_directory / "caller-key.pem"
    caller_document_path.write_text(json.dumps(caller.document), encoding="utf-8")
    caller_key_path.write_bytes(caller.private_key_pem)
    authenticator = create_http_authenticator(caller_document_path, caller_key_path)

    async def resolve_caller(did: str) -> dict[str, object]:
        if did != caller.did:
            raise ValueError("unknown caller DID")
        return caller.document

    jwt_private, jwt_public = _jwt_keys()
    verifier = create_signature_verifier(
        jwt_private_key=jwt_private,
        jwt_public_key=jwt_public,
        did_resolver=resolve_caller,
    )
    handles: list[_AioHttpHandle] = []
    def authorize_did(did: str) -> bool:
        return did == caller.did

    for executor_id, port in zip(manifest.executor_ids, ports, strict=True):
        origin = manifest.endpoints[executor_id]
        app = create_anp_app(
            services[executor_id],
            origin=origin,
            verifier=verifier,
            authorize_did=authorize_did,
        )
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(
            runner,
            "localhost",
            port,
            ssl_context=server_ssl,
        )
        try:
            await site.start()
        except BaseException:
            await runner.cleanup()
            await asyncio.gather(*(handle.stop() for handle in handles), return_exceptions=True)
            raise
        handle = _AioHttpHandle(runner)
        handles.append(handle)
        await stack.enter_async_context(_AsyncCallback(handle.stop))

    session = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=client_ssl),
    )
    await stack.enter_async_context(session)

    def client_factory(executor_id: str, supplied: DIDWbaAuthHeader) -> ANPClient:
        del executor_id
        return ANPClient(supplied, session=session)

    return _ANPRuntime(
        authenticators={executor_id: authenticator for executor_id in manifest.executor_ids},
        client_factory=client_factory,
    )


def _write_tls_material(directory: Path) -> tuple[ssl.SSLContext, ssl.SSLContext]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / "localhost.crt"
    key_path = directory / "localhost.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_context.load_cert_chain(cert_path, key_path)
    client_context = ssl.create_default_context(cafile=str(cert_path))
    return server_context, client_context


def _jwt_keys() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_key = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_key.decode(), public_key.decode()


async def _start_protocol_servers(
    manifest: RunManifest,
    services: dict[str, ExecutorService],
    listeners: list[socket.socket],
) -> list[_UvicornHandle]:
    handles: list[_UvicornHandle] = []
    try:
        for executor_id, listener in zip(manifest.executor_ids, listeners, strict=True):
            service = services[executor_id]
            if manifest.protocol is ProtocolName.A2A:
                app = create_a2a_app(service, rpc_url=manifest.endpoints[executor_id] + "/a2a")
            else:
                app = create_agora_app(
                    service,
                    protocol=TRANSPORT_PROTOCOL,
                    pre_shared=manifest.protocol_mode is ProtocolMode.PRE_SHARED,
                    allow_proposals=manifest.protocol_mode is ProtocolMode.PROPOSAL,
                    proposal_policy=lambda document, purpose: (
                        document.hash == TRANSPORT_PROTOCOL.hash
                        and purpose == "UC-003 transport bidding and award"
                    ),
                )
            handles.append(await _start_server(app, listener))
        return handles
    except BaseException:
        await asyncio.gather(*(handle.stop() for handle in handles), return_exceptions=True)
        for listener in listeners[len(handles) :]:
            listener.close()
        raise


async def _start_server(app: Starlette, listener: socket.socket) -> _UvicornHandle:
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    task.result()
                await asyncio.sleep(0.01)
    except BaseException:
        server.should_exit = True
        listener.close()
        await task
        raise
    return _UvicornHandle(server, task, listener)


class _AsyncCallback:
    def __init__(self, callback: Callable[[], Awaitable[None]]) -> None:
        self._callback = callback

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        await self._callback()


def _build_services(
    manifest: RunManifest,
    status_clients: dict[str, ExecutorStatusMCPClient],
    events: WorkflowEvents,
) -> dict[str, ExecutorService]:
    eta_by_executor = {"executor-01": 7.0, "executor-02": 9.0, "executor-03": 20.0}
    energy_by_executor = {"executor-01": 3.0, "executor-02": 4.0, "executor-03": 8.0}
    services: dict[str, ExecutorService] = {}
    for executor_id in manifest.executor_ids:
        eta = eta_by_executor.get(executor_id, 10.0)
        energy = energy_by_executor.get(executor_id, 5.0)

        def derive_bid(
            request: TransportRequest,
            status: ExecutorStatus,
            *,
            executor_id: str = executor_id,
            eta: float = eta,
            energy: float = energy,
        ) -> Bid:
            return Bid(request.task_id, executor_id, eta, energy + (100 - status.energy_level) / 100)

        services[executor_id] = ExecutorService(
            executor_id=executor_id,
            status_port=status_clients[executor_id],
            bid_policy=derive_bid,
            delivery_simulator=_DeliverySimulator(),
            ledger=TaskLedger(),
            events=events,
        )
    return services


def _stdio_target(
    repo_root: Path,
    module: str,
    option: str,
    value: Path,
) -> StdioServerParameters:
    environment = dict(os.environ)
    source_path = str(repo_root / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (source_path, environment.get("PYTHONPATH", "")) if part
    )
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", module, option, str(value)],
        env=environment,
        cwd=repo_root,
    )


def _fixture_path(repo_root: Path, fixture: str) -> Path:
    path = Path(fixture)
    return path if path.is_absolute() else repo_root / path
