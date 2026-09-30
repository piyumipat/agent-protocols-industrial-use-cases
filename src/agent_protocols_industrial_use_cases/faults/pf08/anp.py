"""PF-08 baseline: DID-authenticated ANP caller denied award permission."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

import aiohttp
from agent_protocols.anp import (
    ANPClient,
    DIDWbaAuthHeader,
    DidWbaVerifierError,
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
from agent_protocols_industrial_use_cases.faults.pf08.policy import BidOnlyPolicy
from agent_protocols_industrial_use_cases.adapters.anp import ANPAdapter, create_executor_app
from agent_protocols_industrial_use_cases.runtime.config import ProtocolName


async def run_anp_bid_only(manifest: FaultManifest, events: WorkflowEvents) -> FaultObservation:
    if (
        manifest.fault is not FaultId.PF08
        or manifest.protocol is not ProtocolName.ANP
        or manifest.round is not FaultRound.BASELINE
    ):
        raise ValueError("PF-08 ANP runner requires a baseline manifest")
    executor_id = "executor-01"
    service, delivery = build_executor_fixture(executor_id=executor_id, events=events)

    async with AsyncExitStack() as stack:
        directory = Path(stack.enter_context(TemporaryDirectory(prefix="uc003-anp-pf08-")))
        tls = create_local_tls(directory)
        listener = reserved_listener()
        port = listener.getsockname()[1]
        origin = f"https://localhost:{port}"
        caller = create_e1_identity(
            "localhost", port=port, path_segments=["agents", "observer-01"]
        )
        policy = BidOnlyPolicy(events, caller=caller.did)
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

        @web.middleware
        async def action_gate(
            request: web.Request,
            handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
        ) -> web.StreamResponse:
            if request.method != "POST" or not request.path.endswith("/rpc"):
                return await handler(request)
            body = await request.read()
            try:
                authenticated = await verifier.verify_request(
                    "POST", str(request.url), dict(request.headers), body
                )
            except DidWbaVerifierError as error:
                return web.json_response({"error": "Authentication failed"}, status=error.status_code)
            verified_did = str(authenticated["did"])
            policy.verified_requests += 1
            try:
                envelope = json.loads(body)
                payload = json.loads(envelope["params"]["payload"])
                operation = payload["operation"]
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                return web.json_response({"error": "Invalid action"}, status=400)
            if not isinstance(operation, str) or operation not in {"request_bid", "award"}:
                return web.json_response({"error": "Invalid action"}, status=400)
            if not policy.authorize(verified_did, operation):
                return web.json_response({"error": "Award not permitted"}, status=403)
            return await handler(request)

        def app_factory(selected_origin: str) -> web.Application:
            app = create_executor_app(
                service,
                origin=selected_origin,
                verifier=verifier,
                authorize_did=lambda did: did == caller.did,
            )
            app.middlewares.append(action_gate)
            return app

        await stack.enter_async_context(anp_server(listener, app_factory, tls))
        connector = aiohttp.TCPConnector(ssl=tls.client)
        session = await stack.enter_async_context(aiohttp.ClientSession(connector=connector))

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
            try:
                bid = await adapter.request_bid(
                    executor_id,
                    TransportRequest(
                        manifest.task_id, "part-42", 1, "zone-a", "welding-cell", "urgent"
                    ),
                )
                bid_response = "bid_returned" if type(bid) is Bid else "unexpected_response"
            except Exception as error:
                if policy.bid_decision is None:
                    raise
                bid_response = f"error:{type(error).__name__}"
            if policy.bid_decision is None:
                raise RuntimeError("bid permission decision was not observed")

            try:
                await adapter.award(executor_id, Award(manifest.task_id, executor_id))
                award_response = "accepted"
            except Exception as error:
                if policy.award_decision is None:
                    raise
                award_response = f"error:{type(error).__name__}"
            if policy.award_decision is None or policy.verified_requests != 2:
                raise RuntimeError("authenticated award permission decision was not observed")

    return FaultObservation(
        injection_count=1,
        caller_outcome=f"bid_{policy.bid_decision};award_{policy.award_decision}",
        first_detection_layer=(
            "external_control"
            if policy.bid_decision == "denied" or policy.award_decision == "denied"
            else None
        ),
        first_detection_event=(
            "policy.bid.denied" if policy.bid_decision == "denied"
            else "policy.award.denied" if policy.award_decision == "denied"
            else None
        ),
        business_dispatches=service.business_dispatches,
        delivery_count=delivery.count,
        details={
            "verified_caller": caller.did,
            "verified_requests": policy.verified_requests,
            "bid_allowed": policy.bid_allowed,
            "bid_policy_decision": policy.bid_decision,
            "award_policy_decision": policy.award_decision,
            "bid_response": bid_response,
            "award_response": award_response,
            "award_dispatched": service.business_dispatches > 0,
            "policy_entry": {caller.did: ["request_bid"]},
            "credential_type": "did_wba_bearer",
        },
    )
