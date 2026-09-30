"""Typed application ports backed by the reference kit's public MCP client."""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self, cast

from agent_protocols.mcp import ConnectionTarget, MCPClient
from mcp.types import TextContent

from agent_protocols_industrial_use_cases.domain.models import (
    CoordinationOutcome,
    ExecutorStatus,
    InventoryItem,
)
from agent_protocols_industrial_use_cases.domain.workflow import EventLayer, StepId, WorkflowEvents

from .payloads import encode_outcome


class MCPToolError(RuntimeError):
    pass


class _ToolClient:
    def __init__(
        self,
        target: ConnectionTarget,
        *,
        read_timeout_seconds: float | None = 30.0,
        events: WorkflowEvents | None = None,
    ) -> None:
        self._client = MCPClient(target, read_timeout_seconds=read_timeout_seconds)
        self._events = events

    async def __aenter__(self) -> Self:
        await self._client.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._client.__aexit__(exc_type, exc, tb)

    async def _call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self._client.call_tool(name, arguments)
        if result.is_error:
            messages = [block.text for block in result.content if isinstance(block, TextContent)]
            detail = "; ".join(messages) or "unknown tool error"
            raise MCPToolError(f"{name} failed: {detail}")
        if not isinstance(result.structured_content, dict):
            raise MCPToolError(f"{name} returned no structured content")
        structured = cast(dict[str, Any], result.structured_content)
        wrapped = structured.get("result")
        if len(structured) == 1 and isinstance(wrapped, dict):
            return cast(dict[str, Any], wrapped)
        return structured

    def _started(self, step: StepId, event_type: str, actor: str, peer: str) -> None:
        if self._events is not None:
            self._events.started(
                step,
                actor=actor,
                peer=peer,
                layer=EventLayer.MCP,
                event_type=event_type,
            )

    def _completed(self, step: StepId, event_type: str, actor: str, peer: str) -> None:
        if self._events is not None:
            self._events.completed(
                step,
                actor=actor,
                peer=peer,
                layer=EventLayer.MCP,
                event_type=event_type,
            )

    def _failed(
        self,
        step: StepId,
        event_type: str,
        actor: str,
        peer: str,
        error: Exception,
    ) -> None:
        if self._events is not None:
            self._events.failed(
                step,
                actor=actor,
                peer=peer,
                layer=EventLayer.MCP,
                event_type=event_type,
                details={"error": type(error).__name__},
            )


def _string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _integer(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer")
    return value


def _number(payload: dict[str, Any], key: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{key} must be a number")
    return float(value)


class InventoryMCPClient(_ToolClient):
    async def locate_part(self, part_id: str) -> InventoryItem:
        step = StepId.CONFIRM_MATERIAL_NEED
        actor = "welding-cell"
        peer = "inventory"
        self._started(step, "mcp.inventory.requested", actor, peer)
        try:
            payload = await self._call("locate_part", {"part_id": part_id})
            item = InventoryItem(
                part_id=_string(payload, "part_id"),
                available_quantity=_integer(payload, "available_quantity"),
                source_zone=_string(payload, "source_zone"),
            )
        except Exception as error:
            self._failed(step, "mcp.inventory.failed", actor, peer, error)
            if isinstance(error, MCPToolError):
                raise
            raise MCPToolError("locate_part returned an invalid payload") from error
        self._completed(step, "mcp.inventory.returned", actor, peer)
        return item


class ExecutorStatusMCPClient(_ToolClient):
    async def get_status(self, executor_id: str) -> ExecutorStatus:
        step = StepId.ASSESS_EXECUTOR_STATUS
        peer = "executor-status"
        self._started(step, "mcp.executor_status.requested", executor_id, peer)
        try:
            payload = await self._call("get_status", {"executor_id": executor_id})
            available = payload["available"]
            if not isinstance(available, bool):
                raise TypeError("available must be a boolean")
            status = ExecutorStatus(
                executor_id=_string(payload, "executor_id"),
                available=available,
                position=_string(payload, "position"),
                energy_level=_number(payload, "energy_level"),
            )
        except Exception as error:
            self._failed(step, "mcp.executor_status.failed", executor_id, peer, error)
            if isinstance(error, MCPToolError):
                raise
            raise MCPToolError("get_status returned an invalid payload") from error
        self._completed(step, "mcp.executor_status.returned", executor_id, peer)
        return status


class OutcomeMCPClient(_ToolClient):
    async def record_outcome(self, outcome: CoordinationOutcome) -> None:
        step = StepId.RECORD_OUTCOME
        actor = "welding-cell"
        peer = "outcome-store"
        self._started(step, "mcp.outcome.requested", actor, peer)
        try:
            payload = await self._call(
                "record_outcome",
                {"outcome": encode_outcome(outcome)},
            )
            if payload.get("recorded") is not True or payload.get("task_id") != outcome.task_id:
                raise MCPToolError("record_outcome returned an invalid acknowledgement")
        except Exception as error:
            self._failed(step, "mcp.outcome.failed", actor, peer, error)
            raise
        self._completed(step, "mcp.outcome.recorded", actor, peer)
