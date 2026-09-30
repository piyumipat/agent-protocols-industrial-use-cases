from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent_protocols_industrial_use_cases.runtime import (
    ProcessRole,
    ProcessSpec,
    ProcessStartError,
    ProcessSupervisor,
    load_manifest,
    mcp_process_specs,
)

MANIFESTS = Path(__file__).parents[2] / "tests" / "fixtures" / "pilot" / "manifests"


def _sleep_spec(name: str, role: ProcessRole) -> ProcessSpec:
    return ProcessSpec(
        name=name,
        role=role,
        command=(
            sys.executable,
            "-u",
            "-c",
            "import time; print('ready', flush=True); time.sleep(60)",
        ),
    )


async def test_process_supervisor_starts_logs_and_stops_in_reverse_order(
    tmp_path: Path,
) -> None:
    supervisor = ProcessSupervisor(log_directory=tmp_path / "logs")
    supervisor.register(_sleep_spec("mcp", ProcessRole.MCP))
    supervisor.register(_sleep_spec("executor", ProcessRole.EXECUTOR))
    supervisor.register(_sleep_spec("welding-cell", ProcessRole.WELDING_CELL))

    async with supervisor:
        assert set(supervisor.processes) == {"mcp", "executor", "welding-cell"}
        assert all(process.pid > 0 for process in supervisor.processes.values())
        assert all(process.log_path.exists() for process in supervisor.processes.values())

    assert supervisor.processes == {}


async def test_process_supervisor_rolls_back_when_a_process_exits_at_startup(
    tmp_path: Path,
) -> None:
    supervisor = ProcessSupervisor(log_directory=tmp_path / "logs")
    supervisor.register(_sleep_spec("first", ProcessRole.MCP))
    supervisor.register(
        ProcessSpec(
            name="fails",
            role=ProcessRole.EXECUTOR,
            command=(sys.executable, "-c", "raise SystemExit(3)"),
        )
    )

    with pytest.raises(ProcessStartError, match="fails"):
        await supervisor.start_all()

    assert supervisor.processes == {}
    assert (tmp_path / "logs" / "first.log").exists()


def test_mcp_specs_bind_manifest_fixtures_and_outcome_path(tmp_path: Path) -> None:
    manifest = load_manifest(MANIFESTS / "uc003-a2a-stable-pilot.json")
    specs = mcp_process_specs(manifest, outcome_path=tmp_path / "outcome.json")

    assert [spec.name for spec in specs] == [
        "mcp-inventory",
        "mcp-status",
        "mcp-outcome",
    ]
    assert all(spec.role is ProcessRole.MCP for spec in specs)
    assert "main/fixtures/inventory.json" in specs[0].command
    assert "main/fixtures/executors.json" in specs[1].command
    assert str(tmp_path / "outcome.json") in specs[2].command
