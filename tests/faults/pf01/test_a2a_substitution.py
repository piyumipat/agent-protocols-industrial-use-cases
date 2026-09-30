"""A signed rogue card passes cryptography but fails plant signer admission."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_protocols_industrial_use_cases.faults.common.evidence import validate_fault_bundle
from agent_protocols_industrial_use_cases.faults.common.manifest import (
    INJECTION_BOUNDARY,
    FaultId,
    FaultManifest,
    FaultRound,
)
from agent_protocols_industrial_use_cases.faults.run import run_fault
from agent_protocols_industrial_use_cases.runtime.config import ProtocolName


@pytest.mark.parametrize("round_name", [FaultRound.BASELINE, FaultRound.SAFEGUARD])
async def test_a2a_signed_rogue_endpoint(
    round_name: FaultRound, tmp_path: Path
) -> None:
    manifest = FaultManifest(
        schema_version=1,
        run_id=f"pf01-a2a-{round_name.value}-r01-a01",
        task_id="fault-task-001",
        fault=FaultId.PF01,
        protocol=ProtocolName.A2A,
        round=round_name,
        repetition=1,
        attempt=1,
        specification_version="A2A 1.0.1",
        reference_kit_version="0.2.0",
        reference_kit_revision="1222b3792c1d347d949b7184d5131a0551ff97bb",
        use_case_revision="draft",
        injection_boundary=INJECTION_BOUNDARY[FaultId.PF01],
        enabled_controls=(
            "task_ledger", "correlated_event_recording", "signed_agent_cards",
        ),
    )
    directory, observation = await run_fault(manifest, tmp_path)
    assert (directory / "validation.json").exists()
    assert observation.details["identity_verification"] == "valid_signature"
    assert observation.details["rogue_bid_reached"] is (round_name is FaultRound.BASELINE)
    assert observation.delivery_count == 0

    if round_name is FaultRound.BASELINE:
        event_path = directory / "events.jsonl"
        original = event_path.read_text(encoding="utf-8")
        events = [json.loads(line) for line in original.splitlines()]
        for omitted_type, expected_error in (
            ("fleet.registry.initialized", "registry setup"),
            ("a2a.agent_card.signature_verified", "credential verification"),
            ("pf01.rogue_bid_request.received", "bid request"),
        ):
            event_path.write_text(
                "\n".join(
                    json.dumps(event) for event in events
                    if event["event_type"] != omitted_type
                ) + "\n",
                encoding="utf-8",
            )
            assert expected_error in " ".join(validate_fault_bundle(directory).errors)
        event_path.write_text(original, encoding="utf-8")
