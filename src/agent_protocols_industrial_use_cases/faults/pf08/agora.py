"""PF-08 baseline: valid Agora caller denied award by user-side policy."""

from __future__ import annotations

from contextlib import AsyncExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from agent_protocols.agora import AgoraClient
from starlette.applications import Starlette

from agent_protocols_industrial_use_cases.domain.models import Award, Bid, TransportRequest
from agent_protocols_industrial_use_cases.domain.workflow import WorkflowEvents
from agent_protocols_industrial_use_cases.faults.common.anp_loopback import create_local_tls
from agent_protocols_industrial_use_cases.faults.common.evidence import FaultObservation
from agent_protocols_industrial_use_cases.faults.common.fixture import build_executor_fixture
from agent_protocols_industrial_use_cases.faults.common.loopback import https_loopback_server
from agent_protocols_industrial_use_cases.faults.common.manifest import (
    FaultId,
    FaultManifest,
    FaultRound,
)
from agent_protocols_industrial_use_cases.faults.pf08.policy import BidOnlyGateway, BidOnlyPolicy
from agent_protocols_industrial_use_cases.adapters.agora import (
    TRANSPORT_PROTOCOL,
    AgoraAdapter,
    create_executor_app,
)
from agent_protocols_industrial_use_cases.runtime.config import ProtocolName


async def run_agora_bid_only(manifest: FaultManifest, events: WorkflowEvents) -> FaultObservation:
    if (
        manifest.fault is not FaultId.PF08
        or manifest.protocol is not ProtocolName.AGORA
        or manifest.round is not FaultRound.BASELINE
    ):
        raise ValueError("PF-08 Agora runner requires a baseline manifest")
    executor_id = "executor-01"
    service, delivery = build_executor_fixture(executor_id=executor_id, events=events)
    policy = BidOnlyPolicy(events)

    def app_factory(_base_url: str) -> Starlette:
        app = create_executor_app(service, protocol=TRANSPORT_PROTOCOL)
        app.add_middleware(BidOnlyGateway, policy=policy)
        return app

    async with AsyncExitStack() as stack:
        directory = Path(stack.enter_context(TemporaryDirectory(prefix="uc003-agora-pf08-")))
        tls = create_local_tls(directory)
        base_url = await stack.enter_async_context(https_loopback_server(app_factory, tls))
        http = await stack.enter_async_context(
            httpx.AsyncClient(
                verify=tls.client,
                headers={"Authorization": f"Bearer {policy.credential()}"},
                timeout=10,
            )
        )

        def client_factory(_executor_id: str, endpoint: str) -> AgoraClient:
            return AgoraClient(endpoint, http_client=http)

        async with AgoraAdapter(
            {executor_id: f"{base_url}/agora"},
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
            "verified_caller": policy.caller,
            "verified_requests": policy.verified_requests,
            "bid_allowed": policy.bid_allowed,
            "bid_policy_decision": policy.bid_decision,
            "award_policy_decision": policy.award_decision,
            "bid_response": bid_response,
            "award_response": award_response,
            "award_dispatched": service.business_dispatches > 0,
            "policy_entry": {policy.caller: ["request_bid"]},
            "credential_type": "signed_jwt",
        },
    )
