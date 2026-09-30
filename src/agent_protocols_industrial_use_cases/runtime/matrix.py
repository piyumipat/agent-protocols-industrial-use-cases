"""Resumable, balanced UC-003 experiment-matrix scheduler."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import load_manifest
from agent_protocols_industrial_use_cases.analysis.metrics import collect_measurements, write_measurement_summaries
from .runner import (
    _executor_ids,
    _run_isolated,
    _run_manifest,
    _write_scaled_executor_fixture,
)
from agent_protocols_industrial_use_cases.evidence.validation import validate_run_evidence


class MatrixConfigurationError(ValueError):
    """The matrix configuration or persisted schedule is inconsistent."""


@dataclass(frozen=True, slots=True)
class ScheduledRun:
    ordinal: int
    manifest_path: Path
    protocol: str
    condition: str
    fleet_size: int
    run_kind: str
    index: int
    rounds: int
    run_id: str

    def to_dict(self) -> dict[str, object]:
        return {
            "ordinal": self.ordinal,
            "manifest_path": str(self.manifest_path),
            "protocol": self.protocol,
            "condition": self.condition,
            "fleet_size": self.fleet_size,
            "run_kind": self.run_kind,
            "index": self.index,
            "rounds": self.rounds,
            "run_id": self.run_id,
        }


@dataclass(frozen=True, slots=True)
class MatrixRunReport:
    scheduled: int
    completed: int
    skipped: int
    failed_attempts: int
    measurements_path: Path
    summary_path: Path


def build_schedule(config_path: Path) -> tuple[ScheduledRun, ...]:
    config = _read_config(config_path)
    cells = tuple(_resolve_cell(config_path, value) for value in _string_list(config, "cells"))
    manifests = {path: load_manifest(path) for path in cells}
    fleet_sizes = _integer_list(config, "fleet_sizes")
    warmups = _integer(config, "warmup_runs")
    measured = _integer(config, "measured_runs")
    rounds = _integer(config, "rounds_per_run")
    seed = _integer(config, "execution_order_seed")
    if warmups < 0 or measured <= 0 or rounds <= 0:
        raise MatrixConfigurationError("matrix run counts must be positive; warmups may be zero")

    ordered_fleets = sorted(fleet_sizes, key=lambda size: _order_key(seed, "fleet", size))
    schedule: list[ScheduledRun] = []
    for fleet_size in ordered_fleets:
        base = sorted(cells, key=lambda path: _order_key(seed, fleet_size, str(path)))
        phases = (
            (("warmup", index) for index in range(1, warmups + 1)),
            (("measured", index) for index in range(1, measured + 1)),
        )
        sequence = 0
        for phase in phases:
            for run_kind, index in phase:
                offset = sequence % len(base)
                ordered_cells = base[offset:] + base[:offset]
                sequence += 1
                for path in ordered_cells:
                    manifest = manifests[path]
                    run_id = f"{manifest.run_id}-fleet-{fleet_size}-{run_kind}-{index:02d}"
                    schedule.append(
                        ScheduledRun(
                            ordinal=len(schedule) + 1,
                            manifest_path=path,
                            protocol=manifest.protocol.value,
                            condition=manifest.condition.value,
                            fleet_size=fleet_size,
                            run_kind=run_kind,
                            index=index,
                            rounds=rounds,
                            run_id=run_id,
                        )
                    )
    return tuple(schedule)


async def run_matrix(
    config_path: Path,
    *,
    results_root: Path,
    summary_root: Path,
) -> MatrixRunReport:
    schedule = build_schedule(config_path)
    schedule_document = {
        "schema_version": 1,
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "runs": [run.to_dict() for run in schedule],
    }
    _persist_or_verify_schedule(results_root / "schedule.json", schedule_document)
    completed = 0
    skipped = 0
    failed_attempts = _failed_attempt_count(results_root)

    for planned in schedule:
        run_directory = results_root / planned.run_id
        if _is_valid_run(run_directory):
            completed += 1
            skipped += 1
            _write_progress(results_root, schedule, completed, skipped, failed_attempts)
            continue
        if run_directory.exists():
            _archive_failed_attempt(results_root, run_directory)
            failed_attempts += 1

        base = load_manifest(planned.manifest_path)
        repo_root = planned.manifest_path.resolve().parents[2]
        executor_ids = _executor_ids(base, planned.fleet_size)
        fixture_path = _write_scaled_executor_fixture(
            repo_root / base.executor_fixture
            if not Path(base.executor_fixture).is_absolute()
            else Path(base.executor_fixture),
            results_root
            / "_fixtures"
            / f"{base.protocol.value}-{base.condition.value}-{planned.fleet_size}.json",
            executor_ids,
        )
        manifest = _run_manifest(
            base,
            executor_ids,
            fleet_size=planned.fleet_size,
            rounds=planned.rounds,
            run_kind=planned.run_kind,
            index=planned.index,
            executor_fixture=fixture_path,
        )
        try:
            await _run_isolated(
                manifest,
                results_root,
                repo_root=repo_root,
                run_kind=planned.run_kind,
            )
        except BaseException:
            if run_directory.exists():
                _archive_failed_attempt(results_root, run_directory)
                failed_attempts += 1
            _write_progress(results_root, schedule, completed, skipped, failed_attempts)
            raise
        completed += 1
        _write_progress(results_root, schedule, completed, skipped, failed_attempts)
        measurements = collect_measurements(results_root)
        write_measurement_summaries(measurements, summary_root)

    measurements = collect_measurements(results_root)
    measurements_path, summary_path = write_measurement_summaries(
        measurements, summary_root
    )
    return MatrixRunReport(
        scheduled=len(schedule),
        completed=completed,
        skipped=skipped,
        failed_attempts=failed_attempts,
        measurements_path=measurements_path,
        summary_path=summary_path,
    )


def _is_valid_run(run_directory: Path) -> bool:
    if not (run_directory / "validation.json").is_file():
        return False
    return validate_run_evidence(run_directory).valid


def _archive_failed_attempt(results_root: Path, run_directory: Path) -> Path:
    archive_root = results_root / "_failed"
    archive_root.mkdir(parents=True, exist_ok=True)
    attempt = 1
    while (archive_root / f"{run_directory.name}-attempt-{attempt:02d}").exists():
        attempt += 1
    destination = archive_root / f"{run_directory.name}-attempt-{attempt:02d}"
    run_directory.replace(destination)
    return destination


def _failed_attempt_count(results_root: Path) -> int:
    archive_root = results_root / "_failed"
    return sum(path.is_dir() for path in archive_root.iterdir()) if archive_root.exists() else 0


def _write_progress(
    results_root: Path,
    schedule: tuple[ScheduledRun, ...],
    completed: int,
    skipped: int,
    failed_attempts: int,
) -> None:
    _write_json(
        results_root / "progress.json",
        {
            "schema_version": 1,
            "updated_at": datetime.now(UTC).isoformat(),
            "scheduled": len(schedule),
            "completed": completed,
            "remaining": len(schedule) - completed,
            "skipped_on_resume": skipped,
            "failed_attempts": failed_attempts,
        },
    )


def _persist_or_verify_schedule(path: Path, document: dict[str, object]) -> None:
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != document:
            raise MatrixConfigurationError(
                f"persisted schedule does not match current configuration: {path}"
            )
        return
    _write_json(path, document)


def _write_json(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _order_key(seed: int, *parts: object) -> str:
    value = ":".join((str(seed), *(str(part) for part in parts)))
    return hashlib.sha256(value.encode()).hexdigest()


def _resolve_cell(config_path: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    repo_root = config_path.resolve().parents[1]
    return repo_root / path


def _read_config(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise MatrixConfigurationError("matrix configuration must be an object")
    return value


def _string_list(document: dict[str, Any], key: str) -> list[str]:
    value = document.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise MatrixConfigurationError(f"{key} must be a string array")
    return value


def _integer_list(document: dict[str, Any], key: str) -> list[int]:
    value = document.get(key)
    if not isinstance(value, list) or not all(
        isinstance(item, int) and not isinstance(item, bool) and item > 0
        for item in value
    ):
        raise MatrixConfigurationError(f"{key} must be a positive integer array")
    return value


def _integer(document: dict[str, Any], key: str) -> int:
    value = document.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise MatrixConfigurationError(f"{key} must be an integer")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--summary-root", type=Path, required=True)
    return parser


async def _main(arguments: list[str] | None = None) -> None:
    options = _parser().parse_args(arguments)
    report = await run_matrix(
        options.config,
        results_root=options.results_root,
        summary_root=options.summary_root,
    )
    print(
        f"scheduled={report.scheduled} completed={report.completed} "
        f"skipped={report.skipped} failed_attempts={report.failed_attempts}"
    )
    print(f"measurements={report.measurements_path}")
    print(f"summary={report.summary_path}")


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
