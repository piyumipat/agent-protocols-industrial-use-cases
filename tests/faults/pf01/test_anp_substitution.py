"""ANP verifies a substituted Executor's own DID in the baseline."""

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


async def test_anp_valid_rogue_did_reaches_bid(tmp_path: Path) -> None:
    manifest = FaultManifest(
        schema_version=1,
        run_id="pf01-anp-baseline-r01-a01",
        task_id="fault-task-001",
        fault=FaultId.PF01,
        protocol=ProtocolName.ANP,
        round=FaultRound.BASELINE,
        repetition=1,
        attempt=1,
        specification_version="ANP 1.1",
        reference_kit_version="0.2.0",
        reference_kit_revision="1222b3792c1d347d949b7184d5131a0551ff97bb",
        use_case_revision="development",
        injection_boundary=INJECTION_BOUNDARY[FaultId.PF01],
        enabled_controls=("task_ledger", "correlated_event_recording"),
    )
    _, observation = await run_fault(manifest, tmp_path)
    assert observation.details["verified_identity"] != observation.details["approved_identity"]
    assert observation.details["rogue_bid_reached"] is True
    assert observation.delivery_count == 0
