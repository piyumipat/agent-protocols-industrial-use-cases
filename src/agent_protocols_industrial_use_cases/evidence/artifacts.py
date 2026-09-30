"""Run-scoped evidence writers for UC-003 experiments."""

from __future__ import annotations

import json
import os
import platform
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Self

from agent_protocols_industrial_use_cases.domain.models import CoordinationOutcome
from agent_protocols_industrial_use_cases.domain.workflow import EventSink, WorkflowEvent
from agent_protocols_industrial_use_cases.tools.mcp.payloads import encode_outcome

from agent_protocols_industrial_use_cases.runtime.config import RunManifest


class EvidenceError(RuntimeError):
    pass


class JsonlEventSink(EventSink):
    """Append structured workflow events to one run-scoped JSONL file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self._file = path.open("x", encoding="utf-8")
        except OSError as error:
            raise EvidenceError(f"cannot create event stream: {path}") from error
        self._closed = False

    def emit(self, event: WorkflowEvent) -> None:
        if self._closed:
            raise EvidenceError("cannot emit to a closed event stream")
        payload = {
            "run_id": event.run_id,
            "task_id": event.task_id,
            "step_id": event.step_id.value,
            "actor": event.actor,
            "peer": event.peer,
            "protocol": event.protocol,
            "layer": event.layer.value,
            "event_type": event.event_type,
            "status": event.status.value,
            "monotonic_ns": event.monotonic_ns,
            "occurred_at": event.occurred_at.isoformat(),
            "details": _json_value(event.details),
        }
        self._file.write(json.dumps(payload, sort_keys=True) + "\n")
        self._file.flush()
        os.fsync(self._file.fileno())

    def close(self) -> None:
        if not self._closed:
            self._file.close()
            self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class RunArtifacts:
    """Own the immutable manifest and mutable evidence files for one run."""

    def __init__(self, results_root: Path, manifest: RunManifest) -> None:
        self.directory = results_root / manifest.run_id
        self.manifest = manifest
        self._events: JsonlEventSink | None = None

    @property
    def event_sink(self) -> JsonlEventSink:
        if self._events is None:
            raise EvidenceError("RunArtifacts is not open")
        return self._events

    def open(self) -> Self:
        if self.directory.exists() and any(self.directory.iterdir()):
            raise EvidenceError(f"run artifact directory is not empty: {self.directory}")
        self.directory.mkdir(parents=True, exist_ok=True)
        _write_json(self.directory / "manifest.json", self.manifest.to_dict(), exclusive=True)
        _write_json(self.directory / "environment.json", _environment(), exclusive=True)
        try:
            self._events = JsonlEventSink(self.directory / "events.jsonl")
        except BaseException:
            self._remove_empty_files()
            raise
        return self

    def close(self) -> None:
        if self._events is not None:
            self._events.close()
            self._events = None

    def write_outcome(self, outcome: CoordinationOutcome) -> None:
        _write_json(self.directory / "outcome.json", encode_outcome(outcome))

    def write_round_outcome(self, round_number: int, outcome: CoordinationOutcome) -> None:
        if round_number <= 0:
            raise EvidenceError("round_number must be positive")
        _write_json(
            self.directory / f"outcome-round-{round_number:02d}.json",
            encode_outcome(outcome),
        )

    def write_outcomes(self, outcomes: Sequence[CoordinationOutcome]) -> None:
        _write_json(
            self.directory / "outcomes.json",
            [encode_outcome(outcome) for outcome in outcomes],
        )

    def write_protocol_summary(self, summary: Mapping[str, Any]) -> None:
        payload = {
            "protocol": self.manifest.protocol.value,
            "condition": self.manifest.condition.value,
            "observations": _json_value(summary),
        }
        _write_json(self.directory / "protocol-summary.json", payload)

    def write_validation(self, validation: Mapping[str, Any]) -> None:
        _write_json(self.directory / "validation.json", validation)

    def __enter__(self) -> Self:
        return self.open()

    def __exit__(self, *args: object) -> None:
        self.close()

    def _remove_empty_files(self) -> None:
        for path in (self.directory / "manifest.json", self.directory / "environment.json"):
            if path.exists() and path.stat().st_size == 0:
                path.unlink()


def _write_json(path: Path, payload: object, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive and path.exists():
        raise EvidenceError(f"evidence file already exists: {path}")
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    try:
        temporary_path.write_text(
            json.dumps(_json_value(payload), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(path)
    except OSError as error:
        raise EvidenceError(f"cannot write evidence file: {path}") from error


def _environment() -> dict[str, str]:
    return {
        "python_version": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "operating_system": os.name,
        "recorded_at": datetime.now(UTC).isoformat(),
    }


def _json_value(value: object) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    raise EvidenceError(f"value is not JSON serializable: {type(value).__name__}")
