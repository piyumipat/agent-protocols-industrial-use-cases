"""Persistent, configurable UC-003 pilot runner."""

from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing
import traceback
from dataclasses import dataclass, replace
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

from .config import FleetCondition, RunManifest, load_manifest
from agent_protocols_industrial_use_cases.analysis.metrics import Measurement, collect_measurements, write_measurement_summaries
from .smoke import SmokeRunResult, run_smoke_manifest


@dataclass(frozen=True, slots=True)
class ExperimentRunReport:
    runs: tuple[SmokeRunResult, ...]
    measurements: tuple[Measurement, ...]
    measurements_path: Path
    summary_path: Path


class IsolatedRunError(RuntimeError):
    """One experiment child process failed or returned no result."""


async def run_experiment(
    manifest_path: Path,
    *,
    results_root: Path | None = None,
    summary_root: Path | None = None,
    warmups: int = 0,
    replications: int = 1,
    rounds: int | None = None,
    fleet_sizes: tuple[int, ...] | None = None,
) -> ExperimentRunReport:
    """Execute configurable pilot runs and persist raw and aggregate measurements."""

    if warmups < 0:
        raise ValueError("warmups must not be negative")
    if replications <= 0:
        raise ValueError("replications must be positive")
    manifest = load_manifest(manifest_path)
    repo_root = manifest_path.resolve().parents[2]
    resolved_results = results_root or manifest_path.resolve().parents[1] / "results"
    resolved_summary = summary_root or manifest_path.resolve().parents[1] / "summaries"
    selected_fleet_sizes = fleet_sizes or (manifest.fleet_size,)
    if any(size <= 0 for size in selected_fleet_sizes):
        raise ValueError("fleet sizes must be positive")
    if rounds is not None and rounds <= 0:
        raise ValueError("rounds must be positive")

    fixture_root = resolved_results / "_fixtures"
    run_results: list[SmokeRunResult] = []
    total_runs = warmups + replications
    for fleet_size in selected_fleet_sizes:
        executor_ids = _executor_ids(manifest, fleet_size)
        fixture_path = _write_scaled_executor_fixture(
            repo_root / manifest.executor_fixture
            if not Path(manifest.executor_fixture).is_absolute()
            else Path(manifest.executor_fixture),
            fixture_root / f"{manifest.protocol.value}-{manifest.condition.value}-{fleet_size}.json",
            executor_ids,
        )
        for sequence in range(1, total_runs + 1):
            run_kind = "warmup" if sequence <= warmups else "measured"
            index = sequence if run_kind == "warmup" else sequence - warmups
            run_manifest = _run_manifest(
                manifest,
                executor_ids,
                fleet_size=fleet_size,
                rounds=rounds or manifest.rounds,
                run_kind=run_kind,
                index=index,
                executor_fixture=fixture_path,
            )
            run_results.append(
                await _run_isolated(
                    run_manifest,
                    resolved_results,
                    repo_root=repo_root,
                    run_kind=run_kind,
                )
            )

    measurements = tuple(collect_measurements(resolved_results))
    measurements_path, summary_path = write_measurement_summaries(
        measurements,
        resolved_summary,
    )
    return ExperimentRunReport(
        tuple(run_results),
        measurements,
        measurements_path,
        summary_path,
    )


async def _run_isolated(
    manifest: RunManifest,
    results_root: Path,
    *,
    repo_root: Path,
    run_kind: str,
) -> SmokeRunResult:
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_run_smoke_child,
        args=(sender, manifest, results_root, repo_root, run_kind),
        name=f"uc003-{manifest.run_id}",
    )
    process.start()
    sender.close()
    try:
        await asyncio.to_thread(process.join)
        if not receiver.poll():
            raise IsolatedRunError(
                f"isolated run {manifest.run_id} exited with code {process.exitcode} "
                "without returning a result"
            )
        status, payload = receiver.recv()
    finally:
        receiver.close()
        process.close()
    if status == "error":
        raise IsolatedRunError(f"isolated run {manifest.run_id} failed:\n{payload}")
    if status != "ok" or not isinstance(payload, SmokeRunResult):
        raise IsolatedRunError(f"isolated run {manifest.run_id} returned invalid data")
    return payload


def _run_smoke_child(
    sender: Connection,
    manifest: RunManifest,
    results_root: Path,
    repo_root: Path,
    run_kind: str,
) -> None:
    try:
        result = asyncio.run(
            run_smoke_manifest(
                manifest,
                results_root,
                repo_root=repo_root,
                run_kind=run_kind,
            )
        )
        sender.send(("ok", result))
    except BaseException:  # noqa: BLE001 - child failures must reach the parent
        sender.send(("error", traceback.format_exc()))
    finally:
        sender.close()


def _run_manifest(
    manifest: RunManifest,
    executor_ids: tuple[str, ...],
    *,
    fleet_size: int,
    rounds: int,
    run_kind: str,
    index: int,
    executor_fixture: Path,
) -> RunManifest:
    endpoints = {
        executor_id: _placeholder_endpoint(manifest, executor_id)
        for executor_id in executor_ids
    }
    service_ports = {
        executor_id: 1 for executor_id in executor_ids
    }
    service_ports.update(
        {name: port for name, port in manifest.service_ports.items() if name.startswith("mcp_")}
    )
    introduced = None
    if manifest.condition is FleetCondition.DYNAMIC:
        introduced = (
            manifest.introduced_executor_id
            if manifest.introduced_executor_id in executor_ids
            else executor_ids[-1]
        )
    return replace(
        manifest,
        run_id=f"{manifest.run_id}-fleet-{fleet_size}-{run_kind}-{index:02d}",
        task_id=f"{manifest.task_id}-fleet-{fleet_size}-{run_kind}-{index:02d}",
        fleet_size=fleet_size,
        rounds=rounds,
        random_seed=manifest.random_seed + index,
        executor_fixture=str(executor_fixture),
        executor_ids=executor_ids,
        introduced_executor_id=introduced,
        endpoints=endpoints,
        service_ports=service_ports,
    )


def _executor_ids(manifest: RunManifest, fleet_size: int) -> tuple[str, ...]:
    if fleet_size <= len(manifest.executor_ids):
        return manifest.executor_ids[:fleet_size]
    return tuple(f"executor-{index:02d}" for index in range(1, fleet_size + 1))


def _placeholder_endpoint(manifest: RunManifest, executor_id: str) -> str:
    endpoint = manifest.endpoints.get(executor_id)
    if endpoint is not None:
        return endpoint
    suffix = "/agora" if manifest.protocol.value == "agora" else ""
    return f"http://127.0.0.1:1{suffix}"


def _write_scaled_executor_fixture(
    source_path: Path,
    output_path: Path,
    executor_ids: tuple[str, ...],
) -> Path:
    source = json.loads(source_path.read_text(encoding="utf-8"))
    entries = source.get("executors")
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise ValueError(f"invalid executor fixture: {source_path}")
    by_id = {str(entry["executor_id"]): entry for entry in entries}
    missing = sorted(set(executor_ids) - set(by_id))
    if missing:
        raise ValueError(
            f"executor fixture {source_path} has no explicit profiles for: "
            f"{', '.join(missing)}"
        )
    scaled: list[dict[str, Any]] = []
    for executor_id in executor_ids:
        scaled.append(dict(by_id[executor_id]))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps({"executors": scaled}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--results-root", type=Path)
    parser.add_argument("--summary-root", type=Path)
    parser.add_argument("--warmups", type=int, default=0)
    parser.add_argument("--replications", type=int, default=1)
    parser.add_argument("--rounds", type=int)
    parser.add_argument("--fleet-sizes", type=str)
    return parser


async def _main(arguments: list[str] | None = None) -> None:
    options = _parser().parse_args(arguments)
    sizes = (
        None
        if options.fleet_sizes is None
        else tuple(int(value) for value in options.fleet_sizes.split(",") if value)
    )
    report = await run_experiment(
        options.manifest,
        results_root=options.results_root,
        summary_root=options.summary_root,
        warmups=options.warmups,
        replications=options.replications,
        rounds=options.rounds,
        fleet_sizes=sizes,
    )
    print(f"runs={len(report.runs)} measurements={len(report.measurements)}")
    print(f"measurements={report.measurements_path}")
    print(f"summary={report.summary_path}")


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
