"""A2A crash must follow durable acceptance and precede delivery."""

from __future__ import annotations

from pathlib import Path

from agent_protocols_industrial_use_cases.faults.common.manifest import (
    INJECTION_BOUNDARY,
    FaultId,
    FaultManifest,
    FaultRound,
)
from agent_protocols_industrial_use_cases.faults.run import run_fault
from agent_protocols_industrial_use_cases.runtime.config import ProtocolName


async def test_a2a_crash_after_acceptance_leaves_unknown_outcome(tmp_path: Path) -> None:
    manifest = FaultManifest(
        schema_version=1,
        run_id="pf06-a2a-baseline-r01-a01",
        task_id="fault-task-001",
        fault=FaultId.PF06,
        protocol=ProtocolName.A2A,
        round=FaultRound.BASELINE,
        repetition=1,
        attempt=1,
        specification_version="A2A 1.0.1",
        reference_kit_version="0.2.0",
        reference_kit_revision="published-v0.2.0",
        use_case_revision="development",
        injection_boundary=INJECTION_BOUNDARY[FaultId.PF06],
        enabled_controls=("task_ledger", "correlated_event_recording"),
    )
    _, observation = await run_fault(manifest, tmp_path)
    assert observation.details["acceptance_durable"] is True
    assert observation.details["restart_observed"] is True
    assert observation.details["blind_retry"] is False
    assert observation.details["recovery_state"] == "unknown_paused"
    assert observation.delivery_count == 0


async def test_a2a_task_id_reaches_caller_before_crash(tmp_path: Path) -> None:
    manifest = FaultManifest(
        schema_version=1,
        run_id="pf06-a2a-task-id-probe-r01",
        task_id="fault-task-001",
        fault=FaultId.PF06,
        protocol=ProtocolName.A2A,
        round=FaultRound.BASELINE,
        repetition=1,
        attempt=1,
        specification_version="A2A 1.0.1",
        reference_kit_version="0.2.0",
        reference_kit_revision="published-v0.2.0",
        use_case_revision="development",
        injection_boundary=INJECTION_BOUNDARY[FaultId.PF06],
        enabled_controls=("task_ledger", "correlated_event_recording"),
        parameters={"task_id_delivery_barrier": True},
    )
    _, observation = await run_fault(manifest, tmp_path)
    assert observation.details["task_id_delivered_before_crash"] is True
    assert observation.details["caller_known_task_id"] is not None
    assert observation.details["status_lookup"] != "not_attempted_no_task_id"
    assert observation.details["recovery_state"] == "unknown_paused"
    assert observation.delivery_count == 0
