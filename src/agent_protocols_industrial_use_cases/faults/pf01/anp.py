"""PF-01 baseline: a rogue ANP endpoint has its own valid DID."""

from __future__ import annotations

import json
from contextlib import AsyncExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

import aiohttp
from agent_protocols.anp import (
    ANPClient,
    DIDWbaAuthHeader,
    E1Identity,
    create_e1_identity,
    create_http_authenticator,
    create_signature_verifier,
    VerifiedAgent,
)
from aiohttp import web

from agent_protocols_industrial_use_cases.domain.executor import ExecutorService
from agent_protocols_industrial_use_cases.domain.models import Bid, TransportRequest
from agent_protocols_industrial_use_cases.domain.workflow import EventLayer, StepId, WorkflowEvents
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
from agent_protocols_industrial_use_cases.faults.pf01.registry import substitute_endpoint
from agent_protocols_industrial_use_cases.adapters.anp import (
    ANPAdapter,
    create_executor_app,
    create_executor_identity,
)
from agent_protocols_industrial_use_cases.runtime.config import ProtocolName


async def run_anp_substitution(
    manifest: FaultManifest, events: WorkflowEvents
) -> FaultObservation:
    if (
        manifest.fault is not FaultId.PF01
        or manifest.protocol is not ProtocolName.ANP
    ):
        raise ValueError("PF-01 ANP runner received the wrong manifest")
    executor_id = "executor-01"
    genuine_service, _ = build_executor_fixture(executor_id=executor_id, events=events)
    rogue_service, _ = build_executor_fixture(
        executor_id=executor_id, events=events, request_marker="rogue-endpoint"
    )

    async with AsyncExitStack() as stack:
        directory = Path(stack.enter_context(TemporaryDirectory(prefix="uc003-anp-pf01-")))
        tls = create_local_tls(directory)
        genuine_listener = reserved_listener()
        rogue_listener = reserved_listener()
        genuine_origin = f"https://localhost:{genuine_listener.getsockname()[1]}"
        rogue_origin = f"https://localhost:{rogue_listener.getsockname()[1]}"
        genuine_identity = create_executor_identity(executor_id, genuine_origin)
        rogue_identity = create_executor_identity(executor_id, rogue_origin)
        caller = create_e1_identity(
            "localhost", path_segments=["agents", "welding-cell"]
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

        def app_for(
            service: ExecutorService, identity: E1Identity, origin: str
        ) -> web.Application:
            verifier = create_signature_verifier(
                jwt_private_key=jwt_private,
                jwt_public_key=jwt_public,
                did_resolver=resolve_caller,
            )
            return create_executor_app(
                service,
                origin=origin,
                identity=identity,
                verifier=verifier,
                authorize_did=lambda did: did == caller.did,
            )

        await stack.enter_async_context(
            anp_server(
                genuine_listener,
                lambda origin: app_for(genuine_service, genuine_identity, origin),
                tls,
            )
        )
        await stack.enter_async_context(
            anp_server(
                rogue_listener,
                lambda origin: app_for(rogue_service, rogue_identity, origin),
                tls,
            )
        )
        registry = substitute_endpoint(
            manifest, events, genuine_url=genuine_origin, rogue_url=rogue_origin,
            approved_identity=genuine_identity.did,
        )
        verified_identity: str | None = None
        admission = "allowed"

        def admit_executor(admitted_executor_id: str, agent: VerifiedAgent) -> None:
            nonlocal verified_identity, admission
            verified_identity = agent.did
            if (
                manifest.round is FaultRound.SAFEGUARD
                and verified_identity != genuine_identity.did
            ):
                admission = "denied"
                events.failed(
                    StepId.REQUEST_BIDS,
                    actor="welding-cell",
                    peer=admitted_executor_id,
                    layer=EventLayer.EXTERNAL_CONTROL,
                    event_type="fleet.identity.denied",
                    details={
                        "verified_identity": verified_identity,
                        "approved_identity": genuine_identity.did,
                    },
                )
                raise PermissionError("Executor DID is not approved for executor-01")
            if manifest.round is FaultRound.SAFEGUARD:
                admission = "allowed"
                events.completed(
                    StepId.REQUEST_BIDS,
                    actor="welding-cell",
                    peer=admitted_executor_id,
                    layer=EventLayer.EXTERNAL_CONTROL,
                    event_type="fleet.identity.allowed",
                    details={
                        "verified_identity": verified_identity,
                        "approved_identity": genuine_identity.did,
                    },
                )

        connector = aiohttp.TCPConnector(ssl=tls.client)
        session = await stack.enter_async_context(aiohttp.ClientSession(connector=connector))

        def client_factory(
            _executor_id: str, supplied_authenticator: DIDWbaAuthHeader
        ) -> ANPClient:
            return ANPClient(supplied_authenticator, session=session)

        bid: Bid | None = None
        try:
            async with ANPAdapter(
                {name: url.removeprefix("https://") for name, url in registry.items()},
                authenticators={executor_id: authenticator},
                events=events,
                client_factory=client_factory,
                agent_admission=(
                    admit_executor if manifest.round is FaultRound.SAFEGUARD else None
                ),
            ) as adapter:
                bid = await adapter.request_bid(
                    executor_id,
                    TransportRequest(
                        manifest.task_id, "part-42", 1, "zone-a", "welding-cell", "urgent"
                    ),
                )
        except PermissionError:
            if admission != "denied":
                raise
        rogue_bid_reached = isinstance(bid, Bid)

    return FaultObservation(
        injection_count=1,
        caller_outcome=(
            "identity_rejected"
            if admission == "denied"
            else "rogue_bid_received" if rogue_bid_reached else "no_bid"
        ),
        first_detection_layer="external_control" if admission == "denied" else None,
        first_detection_event="fleet.identity.denied" if admission == "denied" else None,
        business_dispatches=rogue_service.business_dispatches,
        delivery_count=0,
        details={
            "endpoint_swap": True,
            "claimed_identity": executor_id,
            "verified_identity": verified_identity or rogue_identity.did,
            "approved_identity": genuine_identity.did,
            "admission_decision": admission,
            "rogue_bid_reached": rogue_bid_reached,
            "rogue_bid_requests": rogue_service.bid_requests,
            "genuine_endpoint_running": True,
            "rogue_endpoint_running": True,
            "identity_verification": "did_link_verified",
        },
    )
