"""PF-06 ANP baseline: crash after acceptance and inspect recovery options."""

from __future__ import annotations

import json
from contextlib import AsyncExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

import aiohttp
from agent_protocols.anp import (
    ANPClient,
    DIDWbaAuthHeader,
    create_e1_identity,
    create_http_authenticator,
)

from agent_protocols_industrial_use_cases.domain.models import Award, Bid, TransportRequest
from agent_protocols_industrial_use_cases.domain.workflow import WorkflowEvents
from agent_protocols_industrial_use_cases.faults.common.anp_loopback import (
    create_jwt_keys,
    create_local_tls,
)
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
from agent_protocols_industrial_use_cases.adapters.anp import (
    ANPAdapter,
    create_executor_identity,
)
from agent_protocols_industrial_use_cases.runtime.config import ProtocolName


async def run_anp_crash(
    manifest: FaultManifest, events: WorkflowEvents, directory: Path
) -> FaultObservation:
    if (
        manifest.fault is not FaultId.PF06
        or manifest.protocol is not ProtocolName.ANP
        or manifest.round is not FaultRound.BASELINE
    ):
        raise ValueError("PF-06 ANP runner requires a baseline manifest")
    audit_path, delivery_path = prepare_audits(directory)
    executor_id = "executor-01"
    async with AsyncExitStack() as stack:
        tls_dir = Path(stack.enter_context(TemporaryDirectory(prefix="uc003-anp-pf06-")))
        tls = create_local_tls(tls_dir)
        port = free_port()
        origin = f"https://localhost:{port}"
        identity = create_executor_identity(executor_id, origin)
        caller = create_e1_identity(
            "localhost", port=port, path_segments=["agents", "welding-cell"]
        )
        caller_document = tls_dir / "caller-did.json"
        caller_key = tls_dir / "caller-key.pem"
        caller_document.write_text(json.dumps(caller.document), encoding="utf-8")
        caller_key.write_bytes(caller.private_key_pem)
        authenticator = create_http_authenticator(caller_document, caller_key)
        jwt_private, jwt_public = create_jwt_keys()
        config = WorkerConfig(
            protocol=manifest.protocol,
            run_id=manifest.run_id,
            task_id=manifest.task_id,
            port=port,
            acceptance_audit=audit_path,
            crash_on_acceptance=True,
            tls_cert=tls.certificate_path,
            tls_key=tls.key_path,
            anp_identity=identity,
            caller_did=caller.did,
            caller_document=caller.document,
            jwt_private=jwt_private,
            jwt_public=jwt_public,
        )
        worker = ExecutorProcess(config)
        try:
            await worker.start(verify=tls.client)
            connector = aiohttp.TCPConnector(ssl=tls.client)
            session = await stack.enter_async_context(
                aiohttp.ClientSession(connector=connector)
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
                        manifest.task_id, "part-42", 1,
                        "zone-a", "welding-cell", "urgent",
                    ),
                )
                if type(bid) is not Bid:
                    raise RuntimeError("original bid did not succeed")
                try:
                    await adapter.award(
                        executor_id, Award(manifest.task_id, executor_id)
                    )
                except Exception as error:  # noqa: BLE001 - process closes transport
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
                anp_identity=config.anp_identity,
                caller_did=config.caller_did,
                caller_document=config.caller_document,
                jwt_private=config.jwt_private,
                jwt_public=config.jwt_public,
            )
        )
        try:
            await recovery.start(verify=tls.client)
            async with ANPAdapter(
                {executor_id: origin.removeprefix("https://")},
                authenticators={executor_id: authenticator},
                events=events,
                client_factory=client_factory,
            ):
                pass
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
            "acknowledgement": False,
            "status_lookup": "no_award_status_method",
            "recovery_state": "unknown_paused",
            "restart_observed": True,
            "blind_retry": False,
        },
    )
