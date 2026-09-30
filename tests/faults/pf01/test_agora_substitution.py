"""Agora's HTTPS transport accepts a trusted but unapproved rogue endpoint."""

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


async def test_agora_rogue_tls_endpoint_reaches_bid(tmp_path: Path) -> None:
    manifest = FaultManifest(
        schema_version=1,
        run_id="pf01-agora-baseline-r01-a01",
        task_id="fault-task-001",
        fault=FaultId.PF01,
        protocol=ProtocolName.AGORA,
        round=FaultRound.BASELINE,
        repetition=1,
        attempt=1,
        specification_version="Agora Working Standard 2025-01-19 draft",
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
