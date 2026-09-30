"""PF-01 baseline: a rogue HTTPS Agora endpoint claims executor-01."""

from __future__ import annotations

import asyncio
import hashlib
import ssl
from contextlib import AsyncExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import httpx
from agent_protocols.agora import AgoraClient
from starlette.applications import Starlette

from agent_protocols_industrial_use_cases.domain.models import Bid, TransportRequest
from agent_protocols_industrial_use_cases.domain.workflow import EventLayer, StepId, WorkflowEvents
from agent_protocols_industrial_use_cases.faults.common.anp_loopback import create_local_tls
from agent_protocols_industrial_use_cases.faults.common.evidence import FaultObservation
from agent_protocols_industrial_use_cases.faults.common.fixture import build_executor_fixture
from agent_protocols_industrial_use_cases.faults.common.loopback import https_loopback_server
from agent_protocols_industrial_use_cases.faults.common.manifest import (
    FaultId,
    FaultManifest,
    FaultRound,
)
from agent_protocols_industrial_use_cases.faults.pf01.registry import substitute_endpoint
from agent_protocols_industrial_use_cases.adapters.agora import (
    TRANSPORT_PROTOCOL,
    AgoraAdapter,
    create_executor_app,
)
from agent_protocols_industrial_use_cases.runtime.config import ProtocolName


async def run_agora_substitution(
    manifest: FaultManifest, events: WorkflowEvents
) -> FaultObservation:
    if (
        manifest.fault is not FaultId.PF01
        or manifest.protocol is not ProtocolName.AGORA
    ):
        raise ValueError("PF-01 Agora runner received the wrong manifest")
    executor_id = "executor-01"
    genuine_service, _ = build_executor_fixture(executor_id=executor_id, events=events)
    rogue_service, _ = build_executor_fixture(
        executor_id=executor_id, events=events, request_marker="rogue-endpoint"
    )

    def genuine_app(_base_url: str) -> Starlette:
        return create_executor_app(genuine_service, protocol=TRANSPORT_PROTOCOL)

    def rogue_app(_base_url: str) -> Starlette:
        return create_executor_app(rogue_service, protocol=TRANSPORT_PROTOCOL)

    async with AsyncExitStack() as stack:
        directory = Path(stack.enter_context(TemporaryDirectory(prefix="uc003-agora-pf01-")))
        genuine_dir = directory / "genuine"
        rogue_dir = directory / "rogue"
        genuine_dir.mkdir()
        rogue_dir.mkdir()
        genuine_tls = create_local_tls(genuine_dir)
        rogue_tls = create_local_tls(rogue_dir)
        approved_certificate_hash = _certificate_hash(genuine_tls.certificate_path)
        rogue_certificate_hash = _certificate_hash(rogue_tls.certificate_path)
        trust = ssl.create_default_context(cafile=str(genuine_tls.certificate_path))
        trust.load_verify_locations(cafile=str(rogue_tls.certificate_path))
        genuine_url = await stack.enter_async_context(
            https_loopback_server(genuine_app, genuine_tls)
        )
        rogue_url = await stack.enter_async_context(
            https_loopback_server(rogue_app, rogue_tls)
        )
        registry = substitute_endpoint(
            manifest, events, genuine_url=f"{genuine_url}/agora",
            rogue_url=f"{rogue_url}/agora", approved_identity=approved_certificate_hash,
        )
        port = urlsplit(rogue_url).port
        if port is None:
            raise ValueError("rogue HTTPS URL has no port")
        reader, writer = await asyncio.open_connection(
            "localhost", port, ssl=trust, server_hostname="localhost"
        )
        del reader
        try:
            transport_ssl = writer.get_extra_info("ssl_object")
            presented_der = transport_ssl.getpeercert(binary_form=True)
            if not presented_der:
                raise ValueError("rogue endpoint presented no certificate")
            verified_certificate_hash = hashlib.sha256(presented_der).hexdigest()
            if verified_certificate_hash != rogue_certificate_hash:
                raise ValueError("presented certificate differs from rogue fixture")
        finally:
            writer.close()
            await writer.wait_closed()
        events.completed(
            StepId.REQUEST_BIDS,
            actor="welding-cell",
            peer=executor_id,
            layer=EventLayer.EXTERNAL_CONTROL,
            event_type="agora.https.certificate_verified",
            details={"certificate_sha256": verified_certificate_hash},
        )
        admission = "allowed"
        if (
            manifest.round is FaultRound.SAFEGUARD
            and verified_certificate_hash != approved_certificate_hash
        ):
            admission = "denied"
            events.failed(
                StepId.REQUEST_BIDS,
                actor="welding-cell",
                peer=executor_id,
                layer=EventLayer.EXTERNAL_CONTROL,
                event_type="fleet.identity.denied",
                details={
                    "verified_identity": verified_certificate_hash,
                    "approved_identity": approved_certificate_hash,
                },
            )
        elif manifest.round is FaultRound.SAFEGUARD:
            events.completed(
                StepId.REQUEST_BIDS,
                actor="welding-cell",
                peer=executor_id,
                layer=EventLayer.EXTERNAL_CONTROL,
                event_type="fleet.identity.allowed",
                details={
                    "verified_identity": verified_certificate_hash,
                    "approved_identity": approved_certificate_hash,
                },
            )
        http = await stack.enter_async_context(httpx.AsyncClient(verify=trust, timeout=10))

        def client_factory(_executor_id: str, endpoint: str) -> AgoraClient:
            return AgoraClient(endpoint, http_client=http)

        bid: Bid | None = None
        if admission != "denied":
            async with AgoraAdapter(
                registry,
                events=events,
                client_factory=client_factory,
            ) as adapter:
                bid = await adapter.request_bid(
                    executor_id,
                    TransportRequest(
                        manifest.task_id, "part-42", 1, "zone-a", "welding-cell", "urgent"
                    ),
                )
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
            "verified_identity": verified_certificate_hash,
            "approved_identity": approved_certificate_hash,
            "admission_decision": admission,
            "rogue_bid_reached": rogue_bid_reached,
            "rogue_bid_requests": rogue_service.bid_requests,
            "genuine_endpoint_running": True,
            "rogue_endpoint_running": True,
            "identity_verification": "tls_certificate",
        },
    )


def _certificate_hash(path: Path) -> str:
    pem = path.read_text(encoding="ascii")
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
