from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_protocols_industrial_use_cases.domain.models import (
    Bid,
    CoordinationOutcome,
    CoordinationStatus,
)
from agent_protocols_industrial_use_cases.domain.workflow import (
    EventLayer,
    EventStatus,
    StepId,
    WorkflowEvent,
)
from agent_protocols_industrial_use_cases.runtime import (
    EvidenceError,
    RunArtifacts,
    RunManifest,
    load_manifest,
)

MANIFESTS = Path(__file__).parents[2] / "tests" / "fixtures" / "pilot" / "manifests"


def _manifest() -> RunManifest:
    return load_manifest(MANIFESTS / "uc003-a2a-stable-pilot.json")


def _event() -> WorkflowEvent:
    from datetime import UTC, datetime

    return WorkflowEvent(
        run_id="run-evidence",
        task_id="task-evidence",
        step_id=StepId.SUBMIT_AND_COLLECT_BIDS,
        actor="welding-cell",
        peer="executor-01",
        protocol="a2a",
        layer=EventLayer.PROTOCOL_NATIVE,
        event_type="bid.submitted",
        status=EventStatus.COMPLETED,
        monotonic_ns=123,
        occurred_at=datetime.now(UTC),
        details={"candidate_ids": ("executor-01", "executor-02")},
    )


def test_run_artifacts_write_complete_evidence_layout(tmp_path: Path) -> None:
    outcome = CoordinationOutcome(
        task_id="task-evidence",
        status=CoordinationStatus.COMPLETED,
        winner_id="executor-01",
        bids=(Bid("task-evidence", "executor-01", 5, 2),),
        no_bids=(),
        timed_out_executor_ids=(),
        delivery=None,
    )
    with RunArtifacts(tmp_path / "results", _manifest()) as artifacts:
        artifacts.event_sink.emit(_event())
        artifacts.write_protocol_summary(
            {"discovered": True, "protocol_hash": "example"}
        )
        artifacts.write_outcome(outcome)

    run_dir = tmp_path / "results" / "uc003-a2a-stable-pilot"
    assert {
        path.name for path in run_dir.iterdir()
    } == {"events.jsonl", "environment.json", "manifest.json", "outcome.json", "protocol-summary.json"}
    event = json.loads((run_dir / "events.jsonl").read_text().strip())
    assert event["step_id"] == "UC003-04"
    assert event["details"]["candidate_ids"] == ["executor-01", "executor-02"]
    assert json.loads((run_dir / "outcome.json").read_text())["winner_id"] == "executor-01"


def test_run_artifacts_refuse_reuse_of_nonempty_directory(tmp_path: Path) -> None:
    artifacts = RunArtifacts(tmp_path / "results", _manifest())
    artifacts.open().close()

    with pytest.raises(EvidenceError, match="not empty"):
        RunArtifacts(tmp_path / "results", _manifest()).open()
