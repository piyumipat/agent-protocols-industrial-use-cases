"""Durable external proof and correlated crash events for PF-06."""

from __future__ import annotations

import json
import os
from pathlib import Path

from agent_protocols_industrial_use_cases.domain.workflow import EventLayer, StepId, WorkflowEvents
from agent_protocols_industrial_use_cases.faults.common.manifest import FaultManifest
from agent_protocols_industrial_use_cases.faults.pf06.process import CRASH_EXIT_CODE


def prepare_audits(directory: Path) -> tuple[Path, Path]:
    acceptance = directory / "acceptance-audit.json"
    delivery = directory / "delivery-audit.jsonl"
    with delivery.open("x") as stream:
        stream.flush()
        os.fsync(stream.fileno())
    return acceptance, delivery


def verify_crash_audit(
    manifest: FaultManifest, acceptance: Path, delivery: Path
) -> None:
    audit = json.loads(acceptance.read_text(encoding="utf-8"))
    if audit != {
        "run_id": manifest.run_id,
        "task_id": manifest.task_id,
        "executor_id": "executor-01",
        "state": "accepted_before_delivery",
    }:
        raise RuntimeError("durable acceptance audit differs from the request")
    if delivery.stat().st_size != 0:
        raise RuntimeError("delivery happened before the crash")


def record_crash(manifest: FaultManifest, events: WorkflowEvents, exit_code: int) -> None:
    if exit_code != CRASH_EXIT_CODE:
        raise RuntimeError("Executor stopped at the wrong boundary")
    events.completed(
        StepId.AWARD_TASK,
        actor="fault-injector",
        peer="executor-01",
        layer=EventLayer.EXTERNAL_CONTROL,
        event_type="fault.injected",
        details={"boundary": manifest.injection_boundary, "process_exit_code": exit_code},
    )
    events.failed(
        StepId.EXECUTE_DELIVERY,
        actor="fault-observer",
        peer="executor-01",
        layer=EventLayer.EXTERNAL_CONTROL,
        event_type="executor.process_stopped",
        details={"exit_code": exit_code},
    )
