"""PF-06 Agora baseline: process crash after acceptance, then restart."""

from __future__ import annotations

from contextlib import AsyncExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from agent_protocols.agora import AgoraClient

from agent_protocols_industrial_use_cases.domain.models import Award, Bid, TransportRequest
from agent_protocols_industrial_use_cases.domain.workflow import WorkflowEvents
from agent_protocols_industrial_use_cases.faults.common.anp_loopback import create_local_tls
from agent_protocols_industrial_use_cases.faults.common.evidence import FaultObservation
from agent_protocols_industrial_use_cases.faults.common.manifest import (
    FaultId,
    FaultManifest,
    FaultRound,
)
from agent_protocols_industrial_use_cases.faults.pf06.audit import (
    prepare_audits,
    record_crash,
    verify_crash_audit,
)
from agent_protocols_industrial_use_cases.faults.pf06.process import (
    ExecutorProcess,
    WorkerConfig,
    free_port,
)
from agent_protocols_industrial_use_cases.adapters.agora import TRANSPORT_PROTOCOL
from agent_protocols_industrial_use_cases.adapters.agora.payloads import decode_body
from agent_protocols_industrial_use_cases.adapters.payloads import (
    decode_bid_response,
    encode_award,
    encode_bid_request,
)
from agent_protocols_industrial_use_cases.runtime.config import ProtocolName


async def run_agora_crash(
    manifest: FaultManifest, events: WorkflowEvents, directory: Path
) -> FaultObservation:
    if (
        manifest.fault is not FaultId.PF06
        or manifest.protocol is not ProtocolName.AGORA
        or manifest.round is not FaultRound.BASELINE
    ):
        raise ValueError("PF-06 Agora runner requires a baseline manifest")
    audit_path, delivery_path = prepare_audits(directory)
    async with AsyncExitStack() as stack:
        tls_dir = Path(
            stack.enter_context(TemporaryDirectory(prefix="uc003-agora-pf06-"))
        )
        tls = create_local_tls(tls_dir)
        config = WorkerConfig(
            protocol=manifest.protocol,
            run_id=manifest.run_id,
            task_id=manifest.task_id,
            port=free_port(),
            acceptance_audit=audit_path,
            crash_on_acceptance=True,
            tls_cert=tls.certificate_path,
            tls_key=tls.key_path,
        )
        worker = ExecutorProcess(config)
        conversation_id: str | None = None
        try:
            await worker.start(verify=tls.client)
            async with (
                httpx.AsyncClient(verify=tls.client, timeout=10) as http,
                AgoraClient(f"{config.origin}/agora", http_client=http) as client,
            ):
                bid_response = await client.exchange(
                    encode_bid_request(
                        TransportRequest(
                            manifest.task_id,
                            "part-42",
                            1,
                            "zone-a",
                            "welding-cell",
                            "urgent",
                        )
                    ),
                    protocol=TRANSPORT_PROTOCOL,
                    multiround=True,
                )
                bid = decode_bid_response(decode_body(bid_response.body))
                conversation_id = bid_response.conversation_id
                if type(bid) is not Bid or conversation_id is None:
                    raise RuntimeError("original bid did not establish a conversation")
                try:
                    await client.continue_conversation(
                        conversation_id,
                        encode_award(Award(manifest.task_id, "executor-01")),
                    )
                except httpx.HTTPError as error:
                    caller_failure = type(error).__name__
                else:
                    caller_failure = "response_without_completion"
            exit_code = await worker.wait_for_crash()
        finally:
            await worker.close()
        verify_crash_audit(manifest, audit_path, delivery_path)
        record_crash(manifest, events, exit_code)
        recovery = ExecutorProcess(
            WorkerConfig(
                protocol=config.protocol,
                run_id=config.run_id,
                task_id=config.task_id,
                port=config.port,
                acceptance_audit=audit_path,
                crash_on_acceptance=False,
                tls_cert=config.tls_cert,
                tls_key=config.tls_key,
            )
        )
        try:
            await recovery.start(verify=tls.client)
            async with (
                httpx.AsyncClient(verify=tls.client, timeout=10) as http,
                AgoraClient(f"{config.origin}/agora", http_client=http) as client,
            ):
                discovered = await client.discover()
                if TRANSPORT_PROTOCOL.hash not in discovered:
                    raise RuntimeError(
                        "restarted Agora endpoint did not advertise UC-003"
                    )
        finally:
            await recovery.close()
    return FaultObservation(
        injection_count=1,
        caller_outcome=f"award_unknown:{caller_failure}",
        first_detection_layer="external_control",
        first_detection_event="executor.process_stopped",
        business_dispatches=1,
        delivery_count=0,
        details={
            "acceptance_audit_path": str(audit_path),
            "delivery_audit_path": str(delivery_path),
            "acceptance_durable": True,
            "stop_observed": True,
            "process_exit_code": exit_code,
            "caller_known_task_id": manifest.task_id,
            "conversation_id": conversation_id,
            "acknowledgement": False,
            "status_lookup": "no_award_status_method",
            "recovery_state": "unknown_paused",
            "restart_observed": True,
            "blind_retry": False,
        },
    )
