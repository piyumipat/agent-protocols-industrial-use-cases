"""PF-01: substitute a validly signed rogue A2A Executor endpoint."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AsyncExitStack

from agent_protocols.a2a import (
    A2AClient,
    AgentCard,
    ResolvedCardKey,
    VerifiedAgentCard,
    verify_agent_card,
)
from cryptography.hazmat.primitives.asymmetric import ec
from starlette.applications import Starlette

from agent_protocols_industrial_use_cases.domain.models import Bid, TransportRequest
from agent_protocols_industrial_use_cases.domain.workflow import EventLayer, StepId, WorkflowEvents
from agent_protocols_industrial_use_cases.faults.common.evidence import FaultObservation
from agent_protocols_industrial_use_cases.faults.common.fixture import build_executor_fixture
from agent_protocols_industrial_use_cases.faults.common.loopback import loopback_server
from agent_protocols_industrial_use_cases.faults.common.manifest import (
    FaultId,
    FaultManifest,
    FaultRound,
)
from agent_protocols_industrial_use_cases.faults.pf01.registry import substitute_endpoint
from agent_protocols_industrial_use_cases.adapters.a2a import A2AAdapter, create_executor_app
from agent_protocols_industrial_use_cases.runtime.config import ProtocolName


async def run_a2a_substitution(
    manifest: FaultManifest, events: WorkflowEvents
) -> FaultObservation:
    if manifest.fault is not FaultId.PF01 or manifest.protocol is not ProtocolName.A2A:
        raise ValueError("PF-01 A2A runner received the wrong manifest")
    executor_id = "executor-01"
    approved_kid = "plant/executor-01/genuine"
    rogue_kid = "rogue/executor-01"
    keys = {
        approved_kid: ec.generate_private_key(ec.SECP256R1()),
        rogue_kid: ec.generate_private_key(ec.SECP256R1()),
    }
    genuine_service, _ = build_executor_fixture(executor_id=executor_id, events=events)
    rogue_service, _ = build_executor_fixture(
        executor_id=executor_id, events=events, request_marker="rogue-endpoint"
    )

    def genuine_app(base_url: str) -> Starlette:
        return create_executor_app(
            genuine_service,
            rpc_url=f"{base_url}/a2a",
            card_signing_key=keys[approved_kid],
            card_signing_kid=approved_kid,
        )

    def rogue_app(base_url: str) -> Starlette:
        return create_executor_app(
            rogue_service,
            rpc_url=f"{base_url}/a2a",
            card_signing_key=keys[rogue_kid],
            card_signing_kid=rogue_kid,
        )

    verified_kid: str | None = None
    admission = "not_checked"

    def key_source(kid: str, jku: str | None) -> ResolvedCardKey | None:
        if jku is not None or kid not in keys:
            return None
        return ResolvedCardKey(keys[kid].public_key(), kid, "test-key-directory")

    def card_verifier(card: AgentCard | Mapping[str, object]) -> VerifiedAgentCard:
        nonlocal verified_kid, admission
        verified = verify_agent_card(card, key_source=key_source)
        verified_kid = verified.key_id
        events.completed(
            StepId.REQUEST_BIDS,
            actor="welding-cell",
            peer=executor_id,
            layer=EventLayer.PROTOCOL_NATIVE,
            event_type="a2a.agent_card.signature_verified",
            details={"key_id": verified.key_id},
        )
        if manifest.round is FaultRound.SAFEGUARD and verified.key_id != approved_kid:
            admission = "denied"
            events.failed(
                StepId.REQUEST_BIDS,
                actor="welding-cell",
                peer=executor_id,
                layer=EventLayer.EXTERNAL_CONTROL,
                event_type="fleet.identity.denied",
                    details={
                        "verified_identity": verified.key_id,
                        "approved_identity": approved_kid,
                    },
            )
            raise PermissionError("card signer is not approved for executor-01")
        admission = "allowed"
        return verified

    def client_factory(_executor_id: str, endpoint: str) -> A2AClient:
        return A2AClient(
            endpoint,
            streaming=True,
            accepted_output_modes=["application/json"],
            card_verifier=card_verifier,
        )

    async with AsyncExitStack() as stack:
        genuine_url = await stack.enter_async_context(loopback_server(genuine_app))
        rogue_url = await stack.enter_async_context(loopback_server(rogue_app))
        registry = substitute_endpoint(
            manifest, events, genuine_url=genuine_url, rogue_url=rogue_url,
            approved_identity=approved_kid,
        )
        rogue_bid_reached = False
        try:
            async with A2AAdapter(
                registry, events=events, client_factory=client_factory
            ) as adapter:
                response = await adapter.request_bid(
                    executor_id,
                    TransportRequest(
                        manifest.task_id, "part-42", 1, "zone-a", "welding-cell", "urgent"
                    ),
                )
                rogue_bid_reached = isinstance(response, Bid)
        except PermissionError:
            if admission != "denied":
                raise

    return FaultObservation(
        injection_count=1,
        caller_outcome=(
            "identity_rejected"
            if admission == "denied"
            else "rogue_bid_received" if rogue_bid_reached else "endpoint_rejected"
        ),
        first_detection_layer="external_control" if admission == "denied" else None,
        first_detection_event="fleet.identity.denied" if admission == "denied" else None,
        business_dispatches=rogue_service.business_dispatches,
        delivery_count=0,
        details={
            "endpoint_swap": True,
            "claimed_identity": executor_id,
            "verified_identity": verified_kid,
            "approved_identity": approved_kid,
            "admission_decision": admission,
            "rogue_bid_reached": rogue_bid_reached,
            "rogue_bid_requests": rogue_service.bid_requests,
            "genuine_endpoint_running": True,
            "rogue_endpoint_running": True,
            "identity_verification": "valid_signature" if verified_kid else "failed",
        },
    )
