"""PF-02: replay an Agora award with its original conversation identifier."""

from __future__ import annotations

import hashlib
from contextlib import AsyncExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import httpx
from agent_protocols.agora import AgoraClient
from starlette.applications import Starlette

from agent_protocols_industrial_use_cases.domain.models import Award, Bid, TransportRequest
from agent_protocols_industrial_use_cases.domain.workflow import WorkflowEvents
from agent_protocols_industrial_use_cases.faults.common.anp_loopback import create_local_tls
from agent_protocols_industrial_use_cases.faults.common.evidence import FaultObservation
from agent_protocols_industrial_use_cases.faults.common.fixture import build_executor_fixture
from agent_protocols_industrial_use_cases.faults.common.http_capture import CapturedRequest
from agent_protocols_industrial_use_cases.faults.common.loopback import https_loopback_server
from agent_protocols_industrial_use_cases.faults.common.manifest import FaultId, FaultManifest
from agent_protocols_industrial_use_cases.faults.pf02.evidence import (
    mark_replay_injected,
    record_replay_completed,
)
from agent_protocols_industrial_use_cases.adapters.agora import (
    TRANSPORT_PROTOCOL,
    AgoraAdapter,
    create_executor_app,
)
from agent_protocols_industrial_use_cases.runtime.config import ProtocolName


async def run_agora_replay(manifest: FaultManifest, events: WorkflowEvents) -> FaultObservation:
    if manifest.fault is not FaultId.PF02 or manifest.protocol is not ProtocolName.AGORA:
        raise ValueError("PF-02 Agora runner received the wrong manifest")
    executor_id = "executor-01"
    service, delivery = build_executor_fixture(executor_id=executor_id, events=events)
    requests: list[CapturedRequest] = []
    responses: list[bytes] = []

    async def capture_request(request: httpx.Request) -> None:
        requests.append(await CapturedRequest.from_httpx(request))

    async def capture_response(response: httpx.Response) -> None:
        responses.append(await response.aread())

    def app_factory(_base_url: str) -> Starlette:
        return create_executor_app(service, protocol=TRANSPORT_PROTOCOL)

    async with AsyncExitStack() as stack:
        directory = Path(stack.enter_context(TemporaryDirectory(prefix="uc003-agora-pf02-")))
        tls = create_local_tls(directory)
        base_url = await stack.enter_async_context(
            https_loopback_server(app_factory, tls)
        )
        client = await stack.enter_async_context(
            httpx.AsyncClient(
                event_hooks={"request": [capture_request], "response": [capture_response]},
                verify=tls.client,
                timeout=10,
            )
        )
        def client_factory(_executor_id: str, endpoint: str) -> AgoraClient:
            return AgoraClient(endpoint, require_https=True, http_client=client)

        async with AgoraAdapter(
            {executor_id: f"{base_url}/agora"},
            events=events,
            require_https=True,
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
            award_requests = requests[before_award:]
            if len(award_requests) != 1:
                raise RuntimeError("award did not produce exactly one HTTP request")
            captured = award_requests[0]
            original_response = responses[-1]

        mark_replay_injected(
            events,
            executor_id=executor_id,
            boundary=manifest.injection_boundary,
            method=captured.method,
            url=captured.url,
            original_request_sha256=captured.sha256(),
        )
        replay_request = captured.to_httpx()
        replay_requests: list[CapturedRequest] = []

        async def capture_replay(request: httpx.Request) -> None:
            replay_requests.append(await CapturedRequest.from_httpx(request))

        async with httpx.AsyncClient(
            event_hooks={"request": [capture_replay]},
            verify=tls.client,
            timeout=10,
        ) as replay_client:
            replay_response = await replay_client.send(replay_request)
            replay_body = await replay_response.aread()
        replayed = replay_requests[0]
        replay_request_hash = replayed.sha256()
        replay_response_hash = hashlib.sha256(replay_body).hexdigest()
        record_replay_completed(
            events,
            executor_id=executor_id,
            method=replayed.method,
            url=replayed.url,
            replay_request_sha256=replay_request_hash,
            replay_response_sha256=replay_response_hash,
            replay_http_status=replay_response.status_code,
            request_matches_original=replay_request_hash == captured.sha256(),
            credential_valid=None,
        )

    return FaultObservation(
        injection_count=1,
        caller_outcome=f"http_{replay_response.status_code}",
        first_detection_layer="external_control" if delivery.count == 1 else None,
        first_detection_event=(
            "task_ledger.duplicate_rejected" if delivery.count == 1 else None
        ),
        business_dispatches=service.business_dispatches,
        delivery_count=delivery.count,
        details={
            "original_request_sha256": captured.sha256(),
            "replay_request_sha256": replay_request_hash,
            "first_response_sha256": hashlib.sha256(original_response).hexdigest(),
            "replay_response_sha256": replay_response_hash,
            "authentication_mode": "none",
            "ledger_decision": "duplicate_rejected" if service.business_dispatches == 2 else "not_reached",
            "original_status": original.status.value,
            "replay_http_status": replay_response.status_code,
            "request_method": captured.method,
            "request_url": captured.url,
            "conversation_id_reused": "/conversations/" in captured.url,
            "credential_valid": None,
            "transport_https": urlsplit(captured.url).scheme == "https",
        },
    )
