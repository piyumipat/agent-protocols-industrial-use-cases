"""ANP restart works, but the profile has no award-status method."""

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


async def test_anp_crash_after_acceptance_leaves_unknown_outcome(tmp_path: Path) -> None:
    manifest = FaultManifest(
        schema_version=1,
        run_id="pf06-anp-baseline-r01-a01",
        task_id="fault-task-001",
        fault=FaultId.PF06,
        protocol=ProtocolName.ANP,
        round=FaultRound.BASELINE,
        repetition=1,
        attempt=1,
        specification_version="ANP 1.1",
        reference_kit_version="0.2.0",
        reference_kit_revision="published-v0.2.0",
        use_case_revision="development",
        injection_boundary=INJECTION_BOUNDARY[FaultId.PF06],
        enabled_controls=("task_ledger", "correlated_event_recording"),
    )
    _, observation = await run_fault(manifest, tmp_path)
    assert observation.details["acceptance_durable"] is True
    assert observation.details["restart_observed"] is True
    assert observation.details["status_lookup"] == "no_award_status_method"
    assert observation.details["recovery_state"] == "unknown_paused"
    assert observation.delivery_count == 0
