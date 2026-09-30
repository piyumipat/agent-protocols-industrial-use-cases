"""Structured evidence events for the observable UC-003 workflow."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from time import monotonic_ns
from typing import Any, Protocol


class StepId(StrEnum):
    CONFIRM_MATERIAL_NEED = "UC003-01"
    REQUEST_BIDS = "UC003-02"
    ASSESS_EXECUTOR_STATUS = "UC003-03"
    SUBMIT_AND_COLLECT_BIDS = "UC003-04"
    SELECT_WINNER = "UC003-05"
    AWARD_TASK = "UC003-06"
    EXECUTE_DELIVERY = "UC003-07"
    RECORD_OUTCOME = "UC003-08"


class EventStatus(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


class EventLayer(StrEnum):
    APPLICATION = "application"
    MCP = "mcp"
    PROTOCOL_NATIVE = "protocol_native"
    PAPER_BASED = "paper_based"
    EXTERNAL_CONTROL = "external_control"


@dataclass(frozen=True, slots=True)
class WorkflowEvent:
    run_id: str
    task_id: str
    step_id: StepId
    actor: str
    peer: str | None
    protocol: str
    layer: EventLayer
    event_type: str
    status: EventStatus
    monotonic_ns: int
    occurred_at: datetime
    details: Mapping[str, Any] = field(default_factory=dict)


class EventSink(Protocol):
    def emit(self, event: WorkflowEvent) -> None: ...


class InMemoryEventSink:
    """Small deterministic sink used by tests and in-process runs."""

    def __init__(self) -> None:
        self.events: list[WorkflowEvent] = []

    def emit(self, event: WorkflowEvent) -> None:
        self.events.append(event)


class WorkflowEvents:
    """Creates consistently correlated events without owning workflow control."""

    def __init__(
        self,
        *,
        run_id: str,
        task_id: str,
        protocol: str,
        sink: EventSink,
    ) -> None:
        self.run_id = run_id
        self.task_id = task_id
        self.protocol = protocol
        self.sink = sink

    def started(
        self,
        step_id: StepId,
        *,
        actor: str,
        peer: str | None = None,
        layer: EventLayer = EventLayer.APPLICATION,
        event_type: str = "step.started",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self._emit(step_id, actor, peer, layer, event_type, EventStatus.STARTED, details)

    def completed(
        self,
        step_id: StepId,
        *,
        actor: str,
        peer: str | None = None,
        layer: EventLayer = EventLayer.APPLICATION,
        event_type: str = "step.completed",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self._emit(step_id, actor, peer, layer, event_type, EventStatus.COMPLETED, details)

    def failed(
        self,
        step_id: StepId,
        *,
        actor: str,
        peer: str | None = None,
        layer: EventLayer = EventLayer.APPLICATION,
        event_type: str = "step.failed",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self._emit(step_id, actor, peer, layer, event_type, EventStatus.FAILED, details)

    def _emit(
        self,
        step_id: StepId,
        actor: str,
        peer: str | None,
        layer: EventLayer,
        event_type: str,
        status: EventStatus,
        details: Mapping[str, Any] | None,
    ) -> None:
        self.sink.emit(
            WorkflowEvent(
                run_id=self.run_id,
                task_id=self.task_id,
                step_id=step_id,
                actor=actor,
                peer=peer,
                protocol=self.protocol,
                layer=layer,
                event_type=event_type,
                status=status,
                monotonic_ns=monotonic_ns(),
                occurred_at=datetime.now(UTC),
                details={} if details is None else dict(details),
            )
        )

