"""PF-02: replay an A2A award after its first completed delivery."""

from __future__ import annotations

import hashlib

import httpx
from agent_protocols.a2a import A2AClient, MessageIdempotencyOptions
from starlette.applications import Starlette

from agent_protocols_industrial_use_cases.domain.models import Award, Bid, TransportRequest
from agent_protocols_industrial_use_cases.domain.workflow import EventLayer, StepId, WorkflowEvents
from agent_protocols_industrial_use_cases.faults.common.evidence import FaultObservation
from agent_protocols_industrial_use_cases.faults.common.fixture import build_executor_fixture
from agent_protocols_industrial_use_cases.faults.common.http_capture import CapturedRequest
from agent_protocols_industrial_use_cases.faults.common.loopback import loopback_server
from agent_protocols_industrial_use_cases.faults.common.manifest import FaultId, FaultManifest
from agent_protocols_industrial_use_cases.faults.pf02.evidence import (
    mark_replay_injected,
    record_replay_completed,
)
from agent_protocols_industrial_use_cases.adapters.a2a import A2AAdapter, create_executor_app
from agent_protocols_industrial_use_cases.runtime.config import ProtocolName


async def run_a2a_replay(manifest: FaultManifest, events: WorkflowEvents) -> FaultObservation:
    """Exercise the v0.2.0 messageId cache through a real loopback endpoint."""

    if manifest.fault is not FaultId.PF02 or manifest.protocol is not ProtocolName.A2A:
        raise ValueError("PF-02 A2A runner received the wrong manifest")
    executor_id = "executor-01"
    service, delivery = build_executor_fixture(executor_id=executor_id, events=events)
    requests: list[CapturedRequest] = []
    responses: list[bytes] = []
    idempotency_options = MessageIdempotencyOptions()

    async def capture_request(request: httpx.Request) -> None:
        requests.append(await CapturedRequest.from_httpx(request))

    async def capture_response(response: httpx.Response) -> None:
        responses.append(await response.aread())

    def app_factory(base_url: str) -> Starlette:
        return create_executor_app(
            service,
            rpc_url=f"{base_url}/a2a",
            message_idempotency=idempotency_options,
        )

    async with loopback_server(app_factory) as base_url, httpx.AsyncClient(
        event_hooks={"request": [capture_request], "response": [capture_response]},
        timeout=10,
    ) as client:
        def client_factory(_executor_id: str, endpoint: str) -> A2AClient:
            return A2AClient(
                endpoint,
                streaming=True,
                accepted_output_modes=["application/json"],
                http_client=client,
            )

        async with A2AAdapter(
            {executor_id: base_url}, events=events, client_factory=client_factory
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
            original = await adapter.award(
                executor_id, Award(manifest.task_id, executor_id)
            )
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
            event_hooks={"request": [capture_replay]}, timeout=10
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

    if service.business_dispatches == 1:
        events.completed(
            StepId.AWARD_TASK,
            actor="fault-observer",
            peer=executor_id,
            layer=EventLayer.PROTOCOL_NATIVE,
            event_type="a2a.message_id.cache_inferred",
            details={"basis": "exact_replay_without_second_business_dispatch"},
        )
    detected_by = (
        "a2a.message_id.cache_inferred"
        if service.business_dispatches == 1
        else "task_ledger.duplicate_rejected"
        if delivery.count == 1
        else None
    )
    detector_layer = (
        "protocol_native" if service.business_dispatches == 1 else
        "external_control" if delivery.count == 1 else None
    )
    return FaultObservation(
        injection_count=1,
        caller_outcome=f"http_{replay_response.status_code}",
        first_detection_layer=detector_layer,
        first_detection_event=detected_by,
        business_dispatches=service.business_dispatches,
        delivery_count=delivery.count,
        details={
            "original_request_sha256": captured.sha256(),
            "replay_request_sha256": replayed.sha256(),
            "first_response_sha256": hashlib.sha256(original_response).hexdigest(),
            "replay_response_sha256": replay_response_hash,
            "authentication_mode": "anonymous",
            "ledger_decision": "original_only" if service.business_dispatches == 1 else "duplicate_rejected",
            "original_status": original.status.value,
            "replay_http_status": replay_response.status_code,
            "request_method": captured.method,
            "request_url": captured.url,
            "credential_valid": None,
            "idempotency_retention_seconds": idempotency_options.retention_seconds,
            "idempotency_scope": "process_local",
            "request_headers": [
                [name.decode("latin-1"), value.decode("latin-1")]
                for name, value in captured.headers
                if name.lower() != b"authorization"
            ],
        },
    )
