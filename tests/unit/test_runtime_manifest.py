from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_protocols_industrial_use_cases.runtime.config import (
    FleetCondition,
    ManifestError,
    ProtocolMode,
    ProtocolName,
    RunManifest,
    load_manifest,
    write_manifest,
)

MANIFESTS = Path(__file__).parents[2] / "tests" / "fixtures" / "pilot" / "manifests"


def test_pilot_manifests_load_and_capture_both_conditions() -> None:
    stable = load_manifest(MANIFESTS / "uc003-a2a-stable-pilot.json")
    dynamic = load_manifest(MANIFESTS / "uc003-agora-dynamic-pilot.json")

    assert stable.protocol is ProtocolName.A2A
    assert stable.condition is FleetCondition.STABLE
    assert stable.protocol_mode is ProtocolMode.NATIVE
    assert stable.introduced_executor_id is None
    assert dynamic.protocol is ProtocolName.AGORA
    assert dynamic.condition is FleetCondition.DYNAMIC
    assert dynamic.protocol_mode is ProtocolMode.PROPOSAL
    assert dynamic.introduced_executor_id == "executor-02"


def test_manifest_round_trip_is_deterministic(tmp_path: Path) -> None:
    source = MANIFESTS / "uc003-agora-dynamic-pilot.json"
    manifest = load_manifest(source)
    output = tmp_path / "manifest.json"
    write_manifest(output, manifest)

    assert json.loads(output.read_text()) == manifest.to_dict()
    assert load_manifest(output) == manifest


def test_manifest_rejects_stable_introduction() -> None:
    document = json.loads(
        (MANIFESTS / "uc003-a2a-stable-pilot.json").read_text()
    )
    document["introduced_executor_id"] = "executor-01"

    with pytest.raises(ManifestError, match="stable runs"):
        RunManifest.from_dict(document)


def test_manifest_rejects_endpoint_set_mismatch() -> None:
    document = json.loads(
        (MANIFESTS / "uc003-a2a-stable-pilot.json").read_text()
    )
    del document["endpoints"]["executor-03"]

    with pytest.raises(ManifestError, match="endpoints"):
        RunManifest.from_dict(document)
