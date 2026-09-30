"""PF-02: replay an ANP bearer-token award request over HTTPS."""

from __future__ import annotations

import hashlib
import json
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import aiohttp
from agent_protocols.anp import (
    ANPClient,
    DIDWbaAuthHeader,
    create_e1_identity,
    create_http_authenticator,
    create_signature_verifier,
)
from aiohttp import web

from agent_protocols_industrial_use_cases.domain.models import Award, Bid, TransportRequest
from agent_protocols_industrial_use_cases.domain.workflow import WorkflowEvents
from agent_protocols_industrial_use_cases.faults.common.anp_loopback import (
    anp_server,
    create_jwt_keys,
    create_local_tls,
    reserved_listener,
)
from agent_protocols_industrial_use_cases.faults.common.evidence import FaultObservation
from agent_protocols_industrial_use_cases.faults.common.fixture import build_executor_fixture
from agent_protocols_industrial_use_cases.faults.common.manifest import (
    FaultId,
    FaultManifest,
    FaultRound,
)
from agent_protocols_industrial_use_cases.faults.pf02.evidence import (
    mark_replay_injected,
    record_replay_completed,
)
from agent_protocols_industrial_use_cases.adapters.anp import ANPAdapter, create_executor_app
from agent_protocols_industrial_use_cases.runtime.config import ProtocolName


@dataclass(slots=True)
class CapturedAnpRequest:
    method: str
    url: str
    headers: tuple[tuple[str, str], ...]
    body_parts: list[bytes] = field(default_factory=list)
    response_status: int | None = None
    response_body: bytes | None = None

    @property
    def body(self) -> bytes:
        return b"".join(self.body_parts)

    def sha256(self) -> str:
        digest = hashlib.sha256()
        digest.update(self.method.encode() + b"\0" + self.url.encode() + b"\0")
        for name, value in self.headers:
            digest.update(name.encode() + b":" + value.encode() + b"\n")
        digest.update(b"\0" + self.body)
        return digest.hexdigest()


async def run_anp_replay(manifest: FaultManifest, events: WorkflowEvents) -> FaultObservation:
    if (
        manifest.fault is not FaultId.PF02
        or manifest.protocol is not ProtocolName.ANP
        or manifest.round is not FaultRound.BASELINE
    ):
        raise ValueError("PF-02 ANP runner requires a baseline manifest")
    executor_id = "executor-01"
    service, delivery = build_executor_fixture(executor_id=executor_id, events=events)
    requests: list[CapturedAnpRequest] = []
    trace = aiohttp.TraceConfig()

    async def on_start(_session: aiohttp.ClientSession, context: Any, params: Any) -> None:
        context.captured = CapturedAnpRequest(
            params.method,
            str(params.url),
            tuple((str(k), str(v)) for k, v in params.headers.items()),
        )

    async def on_chunk(_session: aiohttp.ClientSession, context: Any, params: Any) -> None:
        context.captured.body_parts.append(bytes(params.chunk))

    async def on_end(_session: aiohttp.ClientSession, context: Any, params: Any) -> None:
        captured: CapturedAnpRequest = context.captured
        captured.response_status = params.response.status
        captured.response_body = await params.response.read()
        requests.append(captured)

    trace.on_request_start.append(on_start)
    trace.on_request_chunk_sent.append(on_chunk)
    trace.on_request_end.append(on_end)

    async with AsyncExitStack() as stack:
        directory = Path(stack.enter_context(TemporaryDirectory(prefix="uc003-anp-pf02-")))
        tls = create_local_tls(directory)
        listener = reserved_listener()
        port = listener.getsockname()[1]
        origin = f"https://localhost:{port}"
        caller = create_e1_identity(
            "localhost", port=port, path_segments=["agents", "welding-cell"]
        )
        caller_document = directory / "caller-did.json"
        caller_key = directory / "caller-key.pem"
        caller_document.write_text(json.dumps(caller.document), encoding="utf-8")
        caller_key.write_bytes(caller.private_key_pem)
        authenticator = create_http_authenticator(caller_document, caller_key)
        jwt_private, jwt_public = create_jwt_keys()

        async def resolve_caller(did: str) -> dict[str, object]:
            if did != caller.did:
                raise ValueError("unknown caller DID")
            return caller.document

        verifier = create_signature_verifier(
            jwt_private_key=jwt_private,
            jwt_public_key=jwt_public,
            did_resolver=resolve_caller,
        )

        def app_factory(selected_origin: str) -> web.Application:
            return create_executor_app(
                service,
                origin=selected_origin,
                verifier=verifier,
                authorize_did=lambda did: did == caller.did,
            )

        await stack.enter_async_context(anp_server(listener, app_factory, tls))
        connector = aiohttp.TCPConnector(ssl=tls.client)
        session = await stack.enter_async_context(
            aiohttp.ClientSession(connector=connector, trace_configs=[trace])
        )

        def client_factory(
            _executor_id: str, supplied_authenticator: DIDWbaAuthHeader
        ) -> ANPClient:
            return ANPClient(supplied_authenticator, session=session)

        async with ANPAdapter(
            {executor_id: origin.removeprefix("https://")},
            authenticators={executor_id: authenticator},
            events=events,
            client_factory=client_factory,
        ) as adapter:
            bid = await adapter.request_bid(
                executor_id,
                TransportRequest(
                    manifest.task_id, "part-42", 1, "zone-a", "welding-cell", "urgent"
                ),
            )
            if type(bid) is not Bid:
                raise RuntimeError("original bid did not succeed")
            before_award = len(requests)
            original = await adapter.award(executor_id, Award(manifest.task_id, executor_id))
            if delivery.count != 1 or service.business_dispatches != 1:
                raise RuntimeError("original award did not complete exactly once")
            award_requests = [
                request for request in requests[before_award:] if request.method == "POST"
            ]
            if len(award_requests) != 1:
                raise RuntimeError("award did not produce exactly one POST")
            captured = award_requests[0]
            authorization = dict(captured.headers).get("Authorization", "")
            if not authorization.lower().startswith("bearer "):
                raise RuntimeError("ANP award did not use the expected bearer token")

        mark_replay_injected(
            events,
            executor_id=executor_id,
            boundary=manifest.injection_boundary,
            method=captured.method,
            url=captured.url,
            original_request_sha256=captured.sha256(),
        )
        async with session.request(
            captured.method,
            captured.url,
            data=captured.body,
            headers=list(captured.headers),
            allow_redirects=False,
        ) as replay_response:
            replay_status = replay_response.status
            replay_body = await replay_response.read()
        replayed = requests[-1]
        replay_request_hash = replayed.sha256()
        replay_response_hash = hashlib.sha256(replay_body).hexdigest()
        replay_credential_valid = (
            replay_status < 400
            and authorization == dict(replayed.headers).get("Authorization")
        )
        record_replay_completed(
            events,
            executor_id=executor_id,
            method=replayed.method,
            url=replayed.url,
            replay_request_sha256=replay_request_hash,
            replay_response_sha256=replay_response_hash,
            replay_http_status=replay_status,
            request_matches_original=replay_request_hash == captured.sha256(),
            credential_valid=replay_credential_valid,
        )

    return FaultObservation(
        injection_count=1,
        caller_outcome=f"http_{replay_status}",
        first_detection_layer="external_control" if delivery.count == 1 else None,
        first_detection_event=(
            "task_ledger.duplicate_rejected" if delivery.count == 1 else None
        ),
        business_dispatches=service.business_dispatches,
        delivery_count=delivery.count,
        details={
            "original_request_sha256": captured.sha256(),
            "replay_request_sha256": replay_request_hash,
            "first_response_sha256": hashlib.sha256(
                captured.response_body or b""
            ).hexdigest(),
            "replay_response_sha256": replay_response_hash,
            "authentication_mode": "bearer_token",
            "ledger_decision": "duplicate_rejected" if service.business_dispatches == 2 else "not_reached",
            "original_status": original.status.value,
            "replay_http_status": replay_status,
            "request_method": captured.method,
            "request_url": captured.url,
            "bearer_token_reused": authorization == dict(replayed.headers).get("Authorization"),
            "credential_valid": replay_credential_valid,
        },
    )
