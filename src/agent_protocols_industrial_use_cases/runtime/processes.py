"""Async process supervision for evidence-producing UC-003 runs."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Self

from .config import RunManifest


class ProcessRole(StrEnum):
    MCP = "mcp"
    EXECUTOR = "executor"
    WELDING_CELL = "welding_cell"


class ProcessSupervisorError(RuntimeError):
    pass


class ProcessStartError(ProcessSupervisorError):
    pass


@dataclass(frozen=True, slots=True)
class ProcessSpec:
    """One executable process owned by a run supervisor."""

    name: str
    role: ProcessRole
    command: tuple[str, ...]
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: Path | None = None

    def __post_init__(self) -> None:
        if not self.name or "/" in self.name or "\\" in self.name:
            raise ValueError("process name must be a non-empty filename-safe value")
        if not self.command or any(not argument for argument in self.command):
            raise ValueError("process command must contain non-empty arguments")


@dataclass(slots=True)
class ManagedProcess:
    spec: ProcessSpec
    process: asyncio.subprocess.Process
    log_path: Path
    _log_file: object

    @property
    def pid(self) -> int:
        if self.process.pid is None:
            raise ProcessSupervisorError(f"process {self.spec.name} has no PID")
        return self.process.pid

    @property
    def returncode(self) -> int | None:
        return self.process.returncode

    async def wait(self) -> int:
        return await self.process.wait()

    def _close_log(self) -> None:
        close = getattr(self._log_file, "close", None)
        if callable(close):
            close()


class ProcessSupervisor:
    """Start and stop run-owned processes with deterministic lifecycle semantics."""

    def __init__(
        self,
        *,
        log_directory: Path,
        stop_timeout_seconds: float = 5.0,
        startup_grace_seconds: float = 0.1,
    ) -> None:
        if stop_timeout_seconds <= 0:
            raise ValueError("stop_timeout_seconds must be positive")
        if startup_grace_seconds < 0:
            raise ValueError("startup_grace_seconds must not be negative")
        self.log_directory = log_directory
        self.stop_timeout_seconds = stop_timeout_seconds
        self.startup_grace_seconds = startup_grace_seconds
        self._specs: dict[str, ProcessSpec] = {}
        self._processes: dict[str, ManagedProcess] = {}

    def register(self, spec: ProcessSpec) -> None:
        if self._processes:
            raise ProcessSupervisorError("cannot register after processes have started")
        if spec.name in self._specs:
            raise ProcessSupervisorError(f"duplicate process name: {spec.name}")
        self._specs[spec.name] = spec

    def register_many(self, specs: Sequence[ProcessSpec]) -> None:
        for spec in specs:
            self.register(spec)

    @property
    def processes(self) -> Mapping[str, ManagedProcess]:
        return dict(self._processes)

    async def start_all(self) -> Mapping[str, ManagedProcess]:
        if self._processes:
            raise ProcessSupervisorError("processes have already started")
        self.log_directory.mkdir(parents=True, exist_ok=True)
        started: list[ManagedProcess] = []
        try:
            for spec in self._specs.values():
                started.append(await self._start(spec))
        except BaseException:
            await self._stop_handles(reversed(started))
            raise
        self._processes = {managed.spec.name: managed for managed in started}
        return self.processes

    async def stop_all(self) -> None:
        handles = tuple(self._processes.values())
        self._processes.clear()
        await self._stop_handles(reversed(handles))

    async def __aenter__(self) -> Self:
        await self.start_all()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        await self.stop_all()

    async def _start(self, spec: ProcessSpec) -> ManagedProcess:
        log_path = self.log_directory / f"{spec.name}.log"
        log_file = log_path.open("w", encoding="utf-8")
        environment = os.environ.copy()
        environment.update(spec.env)
        try:
            process = await asyncio.create_subprocess_exec(
                *spec.command,
                cwd=None if spec.cwd is None else str(spec.cwd),
                env=environment,
                stdout=log_file,
                stderr=asyncio.subprocess.STDOUT,
            )
        except BaseException:
            log_file.close()
            raise
        managed = ManagedProcess(spec, process, log_path, log_file)
        try:
            returncode = await asyncio.wait_for(
                asyncio.shield(process.wait()), self.startup_grace_seconds
            )
        except TimeoutError:
            return managed
        else:
            managed._close_log()
            raise ProcessStartError(
                f"process {spec.name} exited during startup with code {returncode}"
            )

    async def _stop_handles(self, handles: Iterable[ManagedProcess]) -> None:
        for managed in handles:
            process = managed.process
            try:
                if process.returncode is None:
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), self.stop_timeout_seconds)
                except TimeoutError:
                    if process.returncode is None:
                        process.kill()
                    await process.wait()
            finally:
                managed._close_log()


def mcp_process_specs(
    manifest: RunManifest,
    *,
    outcome_path: Path,
    python_executable: str = sys.executable,
) -> tuple[ProcessSpec, ...]:
    """Build the three MCP stdio subprocess specifications for one manifest."""

    return (
        ProcessSpec(
            name="mcp-inventory",
            role=ProcessRole.MCP,
            command=(
                python_executable,
                "-m",
                "agent_protocols_industrial_use_cases.tools.mcp.inventory_server",
                "--fixture",
                manifest.inventory_fixture,
            ),
        ),
        ProcessSpec(
            name="mcp-status",
            role=ProcessRole.MCP,
            command=(
                python_executable,
                "-m",
                "agent_protocols_industrial_use_cases.tools.mcp.status_server",
                "--fixture",
                manifest.executor_fixture,
            ),
        ),
        ProcessSpec(
            name="mcp-outcome",
            role=ProcessRole.MCP,
            command=(
                python_executable,
                "-m",
                "agent_protocols_industrial_use_cases.tools.mcp.outcome_server",
                "--output",
                str(outcome_path),
            ),
        ),
    )
