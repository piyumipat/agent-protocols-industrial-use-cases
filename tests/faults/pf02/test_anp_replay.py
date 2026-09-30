"""ANP bearer replay reaches the user-side ledger."""

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


async def test_anp_bearer_award_replay_reaches_ledger(tmp_path: Path) -> None:
    manifest = FaultManifest(
        schema_version=1,
        run_id="pf02-anp-baseline-r01-a01",
        task_id="fault-task-001",
        fault=FaultId.PF02,
        protocol=ProtocolName.ANP,
        round=FaultRound.BASELINE,
        repetition=1,
        attempt=1,
        specification_version="ANP 1.1",
        reference_kit_version="0.2.0",
        reference_kit_revision="1222b3792c1d347d949b7184d5131a0551ff97bb",
        use_case_revision="development",
        injection_boundary=INJECTION_BOUNDARY[FaultId.PF02],
        enabled_controls=("task_ledger", "correlated_event_recording"),
    )
    _, observation = await run_fault(manifest, tmp_path)
    assert observation.details["bearer_token_reused"] is True
    assert observation.business_dispatches == 2
    assert observation.delivery_count == 1
    assert observation.first_detection_layer == "external_control"
