from __future__ import annotations

from pathlib import Path

import pytest

from agent_protocols_industrial_use_cases.runtime.config import (
    FleetCondition,
    ProtocolMode,
    ProtocolName,
    load_manifest,
)

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize(
    ("manifest_name", "protocol", "condition", "mode"),
    (
        ("uc003-a2a-stable-pilot.json", ProtocolName.A2A, FleetCondition.STABLE, ProtocolMode.NATIVE),
        ("uc003-a2a-dynamic-pilot.json", ProtocolName.A2A, FleetCondition.DYNAMIC, ProtocolMode.NATIVE),
        ("uc003-anp-stable-pilot.json", ProtocolName.ANP, FleetCondition.STABLE, ProtocolMode.NATIVE),
        ("uc003-anp-dynamic-pilot.json", ProtocolName.ANP, FleetCondition.DYNAMIC, ProtocolMode.NATIVE),
        ("uc003-agora-stable-pilot.json", ProtocolName.AGORA, FleetCondition.STABLE, ProtocolMode.PRE_SHARED),
        ("uc003-agora-dynamic-pilot.json", ProtocolName.AGORA, FleetCondition.DYNAMIC, ProtocolMode.PROPOSAL),
    ),
)
def test_all_pilot_manifests_are_valid(
    manifest_name: str,
    protocol: ProtocolName,
    condition: FleetCondition,
    mode: ProtocolMode,
) -> None:
    manifest = load_manifest(ROOT / "tests/fixtures/pilot/manifests" / manifest_name)
    assert manifest.protocol is protocol
    assert manifest.condition is condition
    assert manifest.protocol_mode is mode
