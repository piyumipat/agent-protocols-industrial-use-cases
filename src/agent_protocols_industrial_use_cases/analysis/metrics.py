"""Reproducible measurements derived from UC-003 evidence streams."""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from statistics import median, quantiles
from typing import Any, cast

from agent_protocols_industrial_use_cases.evidence.validation import validate_run_evidence


@dataclass(frozen=True, slots=True)
class Measurement:
    run_id: str
    task_id: str
    protocol: str
    condition: str
    fleet_size: int
    run_kind: str
    round_number: int
    end_to_end_latency_ns: int | None
    inter_agent_latency_ns: int | None
    logical_protocol_messages: int
    serialized_protocol_bytes: int
    mcp_calls: int
    completed: bool

    def to_row(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "protocol": self.protocol,
            "condition": self.condition,
            "fleet_size": self.fleet_size,
            "run_kind": self.run_kind,
            "round_number": self.round_number,
            "round_phase": "first" if self.round_number == 1 else "repeated",
            "end_to_end_latency_ns": self.end_to_end_latency_ns,
            "inter_agent_latency_ns": self.inter_agent_latency_ns,
            "logical_protocol_messages": self.logical_protocol_messages,
            "serialized_protocol_bytes": self.serialized_protocol_bytes,
            "mcp_calls": self.mcp_calls,
            "completed": self.completed,
        }


def collect_measurements(results_root: Path) -> list[Measurement]:
    measurements: list[Measurement] = []
    for events_path in sorted(results_root.glob("*/events.jsonl")):
        run_directory = events_path.parent
        validate_run_evidence(run_directory).require_valid()
        manifest = _read_object(run_directory / "manifest.json")
        observations = _read_observations(run_directory / "protocol-summary.json")
        events = _read_events(events_path)
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in events:
            grouped[_string(event, "task_id")].append(event)
        for task_id, task_events in sorted(grouped.items()):
            measurements.append(
                _measure_task(
                    manifest,
                    observations,
                    task_id,
                    task_events,
                )
            )
    return measurements


def write_measurement_summaries(
    measurements: Iterable[Measurement],
    summary_root: Path,
) -> tuple[Path, Path]:
    rows = [measurement.to_row() for measurement in measurements]
    summary_root.mkdir(parents=True, exist_ok=True)
    measurements_path = summary_root / "measurements.csv"
    _write_csv(measurements_path, rows)

    grouped: dict[tuple[str, ...], list[Measurement]] = defaultdict(list)
    for measurement in measurements:
        grouped[
            (
                measurement.protocol,
                measurement.condition,
                str(measurement.fleet_size),
                measurement.run_kind,
                "first" if measurement.round_number == 1 else "repeated",
            )
        ].append(measurement)
    summary_rows: list[dict[str, object]] = []
    for key, group in sorted(grouped.items()):
        protocol, condition, fleet_size, run_kind, round_phase = key
        latencies = [
            measurement.end_to_end_latency_ns
            for measurement in group
            if measurement.end_to_end_latency_ns is not None
        ]
        summary_rows.append(
            {
                "protocol": protocol,
                "condition": condition,
                "fleet_size": fleet_size,
                "run_kind": run_kind,
                "round_phase": round_phase,
                "observations": len(group),
                "completed": sum(measurement.completed for measurement in group),
                "completion_rate": sum(measurement.completed for measurement in group)
                / len(group),
                "latency_median_ns": _median(latencies),
                "latency_iqr_ns": _iqr(latencies),
                "latency_p95_ns": _percentile95(latencies),
                "latency_min_ns": min(latencies) if latencies else None,
                "latency_max_ns": max(latencies) if latencies else None,
                "protocol_messages_median": _median(
                    [measurement.logical_protocol_messages for measurement in group]
                ),
                "protocol_bytes_median": _median(
                    [measurement.serialized_protocol_bytes for measurement in group]
                ),
                "mcp_calls_median": _median(
                    [measurement.mcp_calls for measurement in group]
                ),
            }
        )
    summary_path = summary_root / "measurements-summary.csv"
    _write_csv(summary_path, summary_rows)
    return measurements_path, summary_path


def _measure_task(
    manifest: Mapping[str, Any],
    observations: Mapping[str, Any],
    task_id: str,
    events: list[dict[str, Any]],
) -> Measurement:
    first_start = _timestamp(events, "UC003-01", "started", minimum=True)
    outcome_end = _timestamp(events, "UC003-08", "completed", minimum=False)
    protocol_start = _protocol_timestamp(
        events,
        minimum=True,
        not_before=first_start,
    )
    delivery_end = _timestamp(events, "UC003-07", "completed", minimum=False)
    end_to_end = _difference(first_start, outcome_end)
    inter_agent = _difference(protocol_start, delivery_end)
    run_id = _string(manifest, "run_id")
    round_number = _round_number(task_id, _string(manifest, "task_id"))
    return Measurement(
        run_id=run_id,
        task_id=task_id,
        protocol=_string(manifest, "protocol"),
        condition=_string(manifest, "condition"),
        fleet_size=int(manifest["fleet_size"]),
        run_kind=str(observations.get("run_kind", "measured")),
        round_number=round_number,
        end_to_end_latency_ns=end_to_end,
        inter_agent_latency_ns=inter_agent,
        logical_protocol_messages=sum(
            _is_protocol_message(event) for event in events
        ),
        serialized_protocol_bytes=sum(
            _detail_integer(event, "serialized_bytes") for event in events
        ),
        mcp_calls=sum(
            event.get("layer") == "mcp"
            and str(event.get("event_type", "")).endswith(".requested")
            for event in events
        ),
        completed=not any(event.get("status") == "failed" for event in events)
        and outcome_end is not None,
    )


def _is_protocol_message(event: Mapping[str, Any]) -> bool:
    if event.get("layer") not in {"protocol_native", "paper_based"}:
        return False
    event_type = str(event.get("event_type", ""))
    return event_type.endswith(
        (".sent", ".received", ".fetching", ".fetched", ".shared", ".negotiated", ".proposed")
    )


def _timestamp(
    events: Iterable[Mapping[str, Any]],
    step_id: str,
    status: str,
    *,
    minimum: bool,
) -> int | None:
    values = [
        int(event["monotonic_ns"])
        for event in events
        if event.get("step_id") == step_id and event.get("status") == status
    ]
    if not values:
        return None
    return min(values) if minimum else max(values)


def _protocol_timestamp(
    events: Iterable[Mapping[str, Any]],
    *,
    minimum: bool,
    not_before: int | None = None,
) -> int | None:
    values = [
        int(event["monotonic_ns"])
        for event in events
        if event.get("layer") in {"protocol_native", "paper_based"}
        and (not_before is None or int(event["monotonic_ns"]) >= not_before)
    ]
    if not values:
        return None
    return min(values) if minimum else max(values)


def _difference(start: int | None, end: int | None) -> int | None:
    if start is None or end is None or end < start:
        return None
    return end - start


def _round_number(task_id: str, base_task_id: str) -> int:
    if task_id == base_task_id:
        return 1
    match = re.search(r"-r(\d+)$", task_id)
    return int(match.group(1)) if match else 1


def _detail_integer(event: Mapping[str, Any], key: str) -> int:
    details = event.get("details")
    if not isinstance(details, Mapping):
        return 0
    value = details.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _read_events(path: Path) -> list[dict[str, Any]]:
    return [cast(dict[str, Any], json.loads(line)) for line in path.read_text().splitlines()]


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return cast(dict[str, Any], value)


def _read_observations(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        return {}
    value = _read_object(path).get("observations", {})
    return cast(Mapping[str, Any], value) if isinstance(value, Mapping) else {}


def _string(document: Mapping[str, Any], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _median(values: list[int]) -> int | None:
    return int(median(values)) if values else None


def _iqr(values: list[int]) -> int | None:
    if len(values) < 2:
        return 0 if values else None
    quartiles = quantiles(values, n=4, method="inclusive")
    return int(quartiles[2] - quartiles[0])


def _percentile95(values: list[int]) -> int | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return int(quantiles(values, n=100, method="inclusive")[94])


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    fieldnames = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
