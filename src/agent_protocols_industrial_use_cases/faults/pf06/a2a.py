"""PF-06 A2A baseline: crash after durable acceptance, then query task state."""

from __future__ import annotations

import multiprocessing
from pathlib import Path

from agent_protocols.a2a import A2AClient, Role, new_data_message

from agent_protocols_industrial_use_cases.domain.models import Award, Bid, TransportRequest
from agent_protocols_industrial_use_cases.domain.workflow import WorkflowEvents
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
from agent_protocols_industrial_use_cases.adapters.a2a.payloads import (
    decode_bid_response,
    encode_award,
    encode_bid_request,
    message_payload,
)
from agent_protocols_industrial_use_cases.runtime.config import ProtocolName


async def run_a2a_crash(
    manifest: FaultManifest, events: WorkflowEvents, directory: Path
) -> FaultObservation:
    if (
        manifest.fault is not FaultId.PF06
        or manifest.protocol is not ProtocolName.A2A
        or manifest.round is not FaultRound.BASELINE
    ):
        raise ValueError("PF-06 A2A runner requires a baseline manifest")
    audit_path, delivery_path = prepare_audits(directory)
    task_id_delivery_barrier = (
        manifest.parameters.get("task_id_delivery_barrier") is True
    )
    crash_release_event = (
        multiprocessing.get_context("spawn").Event()
        if task_id_delivery_barrier
        else None
    )
    config = WorkerConfig(
        protocol=manifest.protocol,
        run_id=manifest.run_id,
        task_id=manifest.task_id,
        port=free_port(),
        acceptance_audit=audit_path,
        crash_on_acceptance=True,
        crash_release_event=crash_release_event,
    )
    worker = ExecutorProcess(config)
    known_task_id: str | None = None
    acknowledgement = False
    task_id_delivered_before_crash = False
    try:
        await worker.start()
        async with A2AClient(
            config.origin, streaming=True, accepted_output_modes=["application/json"]
        ) as client:
            request = TransportRequest(
                manifest.task_id, "part-42", 1, "zone-a", "welding-cell", "urgent"
            )
            bid: Bid | None = None
            async for response in client.send_message(
                new_data_message(encode_bid_request(request), role=Role.ROLE_USER)
            ):
                if response.HasField("message"):
                    decoded = decode_bid_response(message_payload(response.message))
                    if isinstance(decoded, Bid):
                        bid = decoded
            if bid is None:
                raise RuntimeError("original bid did not succeed")
            try:
                async for response in client.send_message(
                    new_data_message(
                        encode_award(Award(manifest.task_id, "executor-01")),
                        role=Role.ROLE_USER,
                    )
                ):
                    if response.HasField("task"):
                        known_task_id = response.task.id
                    if response.HasField("status_update"):
                        known_task_id = response.status_update.task_id
                        acknowledgement = True
                    if task_id_delivery_barrier and known_task_id is not None:
                        task_id_delivered_before_crash = True
                        assert crash_release_event is not None
                        crash_release_event.set()
            except Exception as error:  # noqa: BLE001 - transport closes on process exit
                caller_failure = type(error).__name__
            else:
                caller_failure = "stream_ended_without_completion"
        exit_code = await worker.wait_for_crash()
    finally:
        await worker.close()
    verify_crash_audit(manifest, audit_path, delivery_path)
    record_crash(manifest, events, exit_code)
    recovery_config = WorkerConfig(
        protocol=config.protocol,
        run_id=config.run_id,
        task_id=config.task_id,
        port=config.port,
        acceptance_audit=audit_path,
        crash_on_acceptance=False,
    )
    restarted = ExecutorProcess(recovery_config)
    lookup_result = "not_attempted_no_task_id"
    try:
        await restarted.start()
        if known_task_id is not None:
            async with A2AClient(config.origin) as client:
                try:
                    task = await client.get_task(known_task_id)
                    lookup_result = str(task.status.state)
                except Exception as error:  # noqa: BLE001 - record lookup failure
                    lookup_result = type(error).__name__
    finally:
        await restarted.close()
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
            "caller_known_task_id": known_task_id,
            "acknowledgement": acknowledgement,
            "task_id_delivered_before_crash": task_id_delivered_before_crash,
            "status_lookup": lookup_result,
            "recovery_state": "unknown_paused",
            "restart_observed": True,
            "blind_retry": False,
        },
    )
