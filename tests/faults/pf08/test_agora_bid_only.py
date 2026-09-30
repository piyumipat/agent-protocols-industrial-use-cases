"""Valid bid-only Agora caller must be denied before award dispatch."""

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


async def test_agora_records_bid_allow_and_award_deny(tmp_path: Path) -> None:
    manifest = FaultManifest(
        schema_version=1,
        run_id="pf08-agora-baseline-r01-a01",
        task_id="fault-task-001",
        fault=FaultId.PF08,
        protocol=ProtocolName.AGORA,
        round=FaultRound.BASELINE,
        repetition=1,
        attempt=1,
        specification_version="Agora current draft",
        reference_kit_version="0.2.0",
        reference_kit_revision="published-v0.2.0",
        use_case_revision="development",
        injection_boundary=INJECTION_BOUNDARY[FaultId.PF08],
        enabled_controls=("task_ledger", "correlated_event_recording"),
    )
    _, observation = await run_fault(manifest, tmp_path)
    assert observation.details["verified_requests"] == 2
    assert observation.details["bid_policy_decision"] == "allowed"
    assert observation.details["award_policy_decision"] == "denied"
