from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_protocols_industrial_use_cases.analysis.metrics import (
    collect_measurements,
    write_measurement_summaries,
)

ROOT = Path(__file__).parents[2]


def test_measurements_are_derived_from_persisted_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ValidEvidence:
        def require_valid(self) -> None:
            pass

    monkeypatch.setattr(
        "agent_protocols_industrial_use_cases.analysis.metrics.validate_run_evidence",
        lambda path: ValidEvidence(),
    )
    manifest = json.loads(
        (ROOT / "tests/fixtures/pilot/manifests/uc003-a2a-stable-pilot.json").read_text()
    )
    run_directory = tmp_path / "run-1"
    run_directory.mkdir()
    (run_directory / "manifest.json").write_text(json.dumps(manifest))
    (run_directory / "protocol-summary.json").write_text(
        json.dumps({"observations": {"run_kind": "measured"}})
    )
    events = [
        {"task_id": "task-a2a-stable-001", "step_id": "UC003-02", "status": "completed", "monotonic_ns": 50, "layer": "protocol_native", "event_type": "a2a.agent_card.fetched", "details": {"serialized_bytes": 5}},
        {"task_id": "task-a2a-stable-001", "step_id": "UC003-01", "status": "started", "monotonic_ns": 100, "layer": "application", "event_type": "step.started", "details": {}},
        {"task_id": "task-a2a-stable-001", "step_id": "UC003-03", "status": "started", "monotonic_ns": 120, "layer": "mcp", "event_type": "mcp.executor_status.requested", "details": {}},
        {"task_id": "task-a2a-stable-001", "step_id": "UC003-02", "status": "started", "monotonic_ns": 150, "layer": "protocol_native", "event_type": "a2a.bid_request.sent", "details": {"serialized_bytes": 10}},
        {"task_id": "task-a2a-stable-001", "step_id": "UC003-04", "status": "completed", "monotonic_ns": 200, "layer": "protocol_native", "event_type": "a2a.bid_response.received", "details": {"serialized_bytes": 20}},
        {"task_id": "task-a2a-stable-001", "step_id": "UC003-07", "status": "completed", "monotonic_ns": 250, "layer": "application", "event_type": "step.completed", "details": {}},
        {"task_id": "task-a2a-stable-001", "step_id": "UC003-08", "status": "completed", "monotonic_ns": 300, "layer": "application", "event_type": "step.completed", "details": {}},
    ]
    (run_directory / "events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n"
    )

    measurements = collect_measurements(tmp_path)
    assert len(measurements) == 1
    measurement = measurements[0]
    assert measurement.end_to_end_latency_ns == 200
    assert measurement.inter_agent_latency_ns == 100
    assert measurement.logical_protocol_messages == 3
    assert measurement.serialized_protocol_bytes == 35
    assert measurement.mcp_calls == 1
    assert measurement.completed is True

    measurements_path, summary_path = write_measurement_summaries(
        measurements, tmp_path / "summaries"
    )
    assert measurements_path.is_file()
    assert summary_path.is_file()
