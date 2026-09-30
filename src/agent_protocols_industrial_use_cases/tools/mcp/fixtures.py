"""Load immutable experiment fixture data for MCP servers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from agent_protocols_industrial_use_cases.domain.models import ExecutorStatus, InventoryItem


class FixtureError(ValueError):
    pass


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FixtureError(f"cannot load fixture: {path}") from error
    if not isinstance(value, dict):
        raise FixtureError(f"fixture must contain a JSON object: {path}")
    return cast(dict[str, Any], value)


def _object_list(document: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    value = document.get(key)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise FixtureError(f"fixture field {key!r} must be a list of objects")
    return cast(list[dict[str, Any]], value)


def load_inventory(path: Path) -> dict[str, InventoryItem]:
    try:
        items = [InventoryItem(**item) for item in _object_list(_load_object(path), "parts")]
    except (TypeError, ValueError) as error:
        raise FixtureError(f"invalid inventory fixture: {path}") from error
    if len({item.part_id for item in items}) != len(items):
        raise FixtureError("inventory fixture contains duplicate part IDs")
    return {item.part_id: item for item in items}


def load_executor_statuses(path: Path) -> dict[str, ExecutorStatus]:
    try:
        statuses = [
            ExecutorStatus(**item)
            for item in _object_list(_load_object(path), "executors")
        ]
    except (TypeError, ValueError) as error:
        raise FixtureError(f"invalid Executor fixture: {path}") from error
    if len({status.executor_id for status in statuses}) != len(statuses):
        raise FixtureError("Executor fixture contains duplicate Executor IDs")
    return {status.executor_id: status for status in statuses}

