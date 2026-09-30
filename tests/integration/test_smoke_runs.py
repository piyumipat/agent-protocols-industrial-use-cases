from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_protocols_industrial_use_cases.domain.models import CoordinationStatus
from agent_protocols_industrial_use_cases.runtime import (
    SmokeRunResult,
    run_smoke_scenario,
    validate_run_evidence,
)
from agent_protocols_industrial_use_cases.runtime.matrix import run_matrix
from agent_protocols_industrial_use_cases.runtime.runner import run_experiment

ROOT = Path(__file__).parents[2]
MANIFESTS = ROOT / "tests" / "fixtures" / "pilot" / "manifests"


@pytest.mark.parametrize(
    ("manifest_name", "expected_event", "forbidden_event"),
    (
        (
            "uc003-a2a-stable-pilot.json",
            "a2a.bid_response.received",
            "agora.protocol_document.negotiated",
        ),
        (
            "uc003-agora-dynamic-pilot.json",
            "agora.protocol_document.negotiated",
            "a2a.agent_card.fetched",
        ),
    ),
)
async def test_uc003_smoke_scenario(
    tmp_path: Path,
    manifest_name: str,
    expected_event: str,
    forbidden_event: str,
) -> None:
    result = await run_smoke_scenario(MANIFESTS / manifest_name, tmp_path / "results")

    _assert_successful_run(result, expected_event, forbidden_event)


async def test_runner_isolates_sequential_a2a_fleets(tmp_path: Path) -> None:
    report = await run_experiment(
        MANIFESTS / "uc003-a2a-stable-pilot.json",
        results_root=tmp_path / "results",
        summary_root=tmp_path / "summaries",
        replications=1,
        rounds=1,
        fleet_sizes=(1, 3),
    )

    assert [run.manifest.fleet_size for run in report.runs] == [1, 3]
    assert all(
        json.loads((run.artifact_directory / "validation.json").read_text())["valid"]
        for run in report.runs
    )


async def test_matrix_resume_skips_validated_runs(tmp_path: Path) -> None:
    config = {
        "cells": [str(MANIFESTS / "uc003-a2a-stable-pilot.json")],
        "fleet_sizes": [1],
        "warmup_runs": 0,
        "measured_runs": 1,
        "rounds_per_run": 1,
        "execution_order_seed": 30001,
    }
    config_path = tmp_path / "matrix.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    results_root = tmp_path / "results"
    summary_root = tmp_path / "summaries"

    first = await run_matrix(
        config_path, results_root=results_root, summary_root=summary_root
    )
    resumed = await run_matrix(
        config_path, results_root=results_root, summary_root=summary_root
    )

    assert first.completed == 1
    assert first.skipped == 0
    assert resumed.completed == 1
    assert resumed.skipped == 1
    assert json.loads((results_root / "progress.json").read_text())["remaining"] == 0


def _assert_successful_run(
    result: SmokeRunResult,
    expected_event: str,
    forbidden_event: str,
) -> None:
    assert result.outcome.status is CoordinationStatus.COMPLETED
    assert result.outcome.winner_id == "executor-01"
    assert result.outcome.delivery is not None
    assert result.artifact_directory.is_dir()

    events = [
        json.loads(line)
        for line in (result.artifact_directory / "events.jsonl").read_text().splitlines()
    ]
    assert {event["step_id"] for event in events} == {
        "UC003-01",
        "UC003-02",
        "UC003-03",
        "UC003-04",
        "UC003-05",
        "UC003-06",
        "UC003-07",
        "UC003-08",
    }
    assert expected_event in {event["event_type"] for event in events}
    assert forbidden_event not in {event["event_type"] for event in events}
    assert (result.artifact_directory / "manifest.json").is_file()
    assert (result.artifact_directory / "environment.json").is_file()
    assert (result.artifact_directory / "outcome.json").is_file()
    assert (result.artifact_directory / "protocol-summary.json").is_file()
    assert (result.artifact_directory / "mcp-outcome.json").is_file()
    validation = json.loads(
        (result.artifact_directory / "validation.json").read_text()
    )
    assert validation["valid"] is True
    assert validation["validated_rounds"] == 1

    outcomes_path = result.artifact_directory / "outcomes.json"
    outcomes = json.loads(outcomes_path.read_text())
    outcomes[0]["winner_id"] = "executor-02"
    outcomes_path.write_text(json.dumps(outcomes))
    assert validate_run_evidence(result.artifact_directory).valid is False
