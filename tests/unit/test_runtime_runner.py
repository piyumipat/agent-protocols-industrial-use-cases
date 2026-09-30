from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_protocols_industrial_use_cases.runtime.config import load_manifest
from agent_protocols_industrial_use_cases.runtime.runner import (
    _executor_ids,
    _run_manifest,
    _write_scaled_executor_fixture,
)

ROOT = Path(__file__).parents[2]


def test_executor_fixture_has_explicit_profiles_for_largest_fleet() -> None:
    manifest = load_manifest(ROOT / "tests/fixtures/pilot/manifests/uc003-a2a-stable-pilot.json")
    executor_ids = _executor_ids(manifest, 10)
    fixture = json.loads(
        (ROOT / "inputs/main/fixtures/executors.json").read_text(encoding="utf-8")
    )

    assert [entry["executor_id"] for entry in fixture["executors"]] == list(executor_ids)


def test_scaled_fixture_rejects_missing_explicit_profile(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps(
            {
                "executors": [
                    {
                        "executor_id": "executor-01",
                        "available": True,
                        "position": "aisle-1",
                        "energy_level": 80.0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="executor-02"):
        _write_scaled_executor_fixture(
            source,
            tmp_path / "selected.json",
            ("executor-01", "executor-02"),
        )


def test_dynamic_single_executor_is_the_introduced_participant(tmp_path: Path) -> None:
    manifest = load_manifest(ROOT / "tests/fixtures/pilot/manifests/uc003-a2a-dynamic-pilot.json")
    executor_ids = _executor_ids(manifest, 1)

    run_manifest = _run_manifest(
        manifest,
        executor_ids,
        fleet_size=1,
        rounds=1,
        run_kind="measured",
        index=1,
        executor_fixture=tmp_path / "executors.json",
    )

    assert run_manifest.executor_ids == ("executor-01",)
    assert run_manifest.introduced_executor_id == "executor-01"
