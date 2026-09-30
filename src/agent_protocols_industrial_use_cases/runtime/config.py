"""Validated, reproducible run manifests for UC-003 experiments."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class ManifestError(ValueError):
    """A run manifest is missing data or violates an experiment invariant."""


class ProtocolName(StrEnum):
    A2A = "a2a"
    ANP = "anp"
    AGORA = "agora"


class FleetCondition(StrEnum):
    STABLE = "stable"
    DYNAMIC = "dynamic"


class ProtocolMode(StrEnum):
    NATIVE = "native"
    PRE_SHARED = "pre_shared"
    PROPOSAL = "proposal"


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class RunManifest:
    """All immutable inputs needed to construct one UC-003 run."""

    schema_version: int
    run_id: str
    task_id: str
    protocol: ProtocolName
    condition: FleetCondition
    fleet_size: int
    rounds: int
    random_seed: int
    response_deadline_seconds: float
    inventory_fixture: str
    executor_fixture: str
    executor_ids: tuple[str, ...]
    introduced_executor_id: str | None
    endpoints: dict[str, str]
    service_ports: dict[str, int]
    endpoint_source: str
    protocol_mode: ProtocolMode
    enabled_controls: tuple[str, ...]
    reference_kit_version: str
    reference_kit_commit: str
    use_case_commit: str
    lockfile_sha256: str

    def __post_init__(self) -> None:
        _require_text(self.run_id, "run_id")
        _require_text(self.task_id, "task_id")
        _require_text(self.inventory_fixture, "inventory_fixture")
        _require_text(self.executor_fixture, "executor_fixture")
        _require_text(self.endpoint_source, "endpoint_source")
        _require_text(self.reference_kit_version, "reference_kit_version")
        _require_text(self.reference_kit_commit, "reference_kit_commit")
        _require_text(self.use_case_commit, "use_case_commit")
        if self.schema_version != 1:
            raise ManifestError("schema_version must be 1")
        if self.fleet_size != len(self.executor_ids) or self.fleet_size <= 0:
            raise ManifestError("fleet_size must equal the non-empty executor_ids list")
        if len(set(self.executor_ids)) != len(self.executor_ids):
            raise ManifestError("executor_ids must be unique")
        if self.rounds <= 0:
            raise ManifestError("rounds must be positive")
        if self.random_seed < 0:
            raise ManifestError("random_seed must not be negative")
        if self.response_deadline_seconds <= 0:
            raise ManifestError("response_deadline_seconds must be positive")
        if set(self.endpoints) != set(self.executor_ids):
            raise ManifestError("endpoints must contain exactly the executor IDs")
        for executor_id, endpoint in self.endpoints.items():
            _validate_endpoint(executor_id, endpoint)
        if not self.service_ports:
            raise ManifestError("service_ports must not be empty")
        for name, port in self.service_ports.items():
            if not name.strip() or not 1 <= port <= 65535:
                raise ManifestError(f"invalid service port: {name!r}={port!r}")
        if not self.enabled_controls or any(not control.strip() for control in self.enabled_controls):
            raise ManifestError("enabled_controls must contain non-empty names")
        if len(set(self.enabled_controls)) != len(self.enabled_controls):
            raise ManifestError("enabled_controls must be unique")
        if not _SHA256.fullmatch(self.lockfile_sha256):
            raise ManifestError("lockfile_sha256 must be a lowercase SHA-256 digest")
        if self.condition is FleetCondition.STABLE:
            if self.introduced_executor_id is not None:
                raise ManifestError("stable runs must not introduce an Executor")
            if self.endpoint_source != "configuration":
                raise ManifestError("stable runs must use configuration endpoint_source")
        elif self.introduced_executor_id not in self.executor_ids:
            raise ManifestError("dynamic runs must identify an introduced Executor")
        if self.protocol is ProtocolName.AGORA:
            if self.protocol_mode not in {
                ProtocolMode.PRE_SHARED,
                ProtocolMode.PROPOSAL,
            }:
                raise ManifestError("Agora runs require pre_shared or proposal protocol_mode")
        elif self.protocol_mode is not ProtocolMode.NATIVE:
            raise ManifestError("A2A and ANP runs require native protocol_mode")

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> RunManifest:
        expected = {field for field in cls.__dataclass_fields__}
        if set(document) != expected:
            missing = sorted(expected - set(document))
            extra = sorted(set(document) - expected)
            raise ManifestError(f"manifest fields mismatch; missing={missing}, extra={extra}")
        try:
            return cls(
                schema_version=_int(document, "schema_version"),
                run_id=_str(document, "run_id"),
                task_id=_str(document, "task_id"),
                protocol=ProtocolName(_str(document, "protocol")),
                condition=FleetCondition(_str(document, "condition")),
                fleet_size=_int(document, "fleet_size"),
                rounds=_int(document, "rounds"),
                random_seed=_int(document, "random_seed"),
                response_deadline_seconds=_float(document, "response_deadline_seconds"),
                inventory_fixture=_str(document, "inventory_fixture"),
                executor_fixture=_str(document, "executor_fixture"),
                executor_ids=_str_tuple(document, "executor_ids"),
                introduced_executor_id=_optional_str(document, "introduced_executor_id"),
                endpoints=_str_map(document, "endpoints"),
                service_ports=_int_map(document, "service_ports"),
                endpoint_source=_str(document, "endpoint_source"),
                protocol_mode=ProtocolMode(_str(document, "protocol_mode")),
                enabled_controls=_str_tuple(document, "enabled_controls"),
                reference_kit_version=_str(document, "reference_kit_version"),
                reference_kit_commit=_str(document, "reference_kit_commit"),
                use_case_commit=_str(document, "use_case_commit"),
                lockfile_sha256=_str(document, "lockfile_sha256"),
            )
        except (TypeError, ValueError) as error:
            if isinstance(error, ManifestError):
                raise
            raise ManifestError(str(error)) from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "protocol": self.protocol.value,
            "condition": self.condition.value,
            "fleet_size": self.fleet_size,
            "rounds": self.rounds,
            "random_seed": self.random_seed,
            "response_deadline_seconds": self.response_deadline_seconds,
            "inventory_fixture": self.inventory_fixture,
            "executor_fixture": self.executor_fixture,
            "executor_ids": list(self.executor_ids),
            "introduced_executor_id": self.introduced_executor_id,
            "endpoints": dict(sorted(self.endpoints.items())),
            "service_ports": dict(sorted(self.service_ports.items())),
            "endpoint_source": self.endpoint_source,
            "protocol_mode": self.protocol_mode.value,
            "enabled_controls": list(self.enabled_controls),
            "reference_kit_version": self.reference_kit_version,
            "reference_kit_commit": self.reference_kit_commit,
            "use_case_commit": self.use_case_commit,
            "lockfile_sha256": self.lockfile_sha256,
        }


def load_manifest(path: Path) -> RunManifest:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ManifestError(f"cannot load manifest: {path}") from error
    if not isinstance(document, dict):
        raise ManifestError("manifest must contain a JSON object")
    return RunManifest.from_dict(document)


def write_manifest(path: Path, manifest: RunManifest) -> None:
    path.write_text(
        json.dumps(manifest.to_dict(), indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def _require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{field} must be a non-empty string")


def _str(document: dict[str, Any], field: str) -> str:
    value = document[field]
    if not isinstance(value, str):
        raise ManifestError(f"{field} must be a string")
    return value


def _optional_str(document: dict[str, Any], field: str) -> str | None:
    value = document[field]
    if value is not None and not isinstance(value, str):
        raise ManifestError(f"{field} must be a string or null")
    return value


def _int(document: dict[str, Any], field: str) -> int:
    value = document[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ManifestError(f"{field} must be an integer")
    return value


def _float(document: dict[str, Any], field: str) -> float:
    value = document[field]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ManifestError(f"{field} must be a number")
    return float(value)


def _str_tuple(document: dict[str, Any], field: str) -> tuple[str, ...]:
    value = document[field]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ManifestError(f"{field} must be an array of strings")
    return tuple(value)


def _str_map(document: dict[str, Any], field: str) -> dict[str, str]:
    value = document[field]
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ManifestError(f"{field} must be an object of strings")
    return dict(value)


def _int_map(document: dict[str, Any], field: str) -> dict[str, int]:
    value = document[field]
    if not isinstance(value, dict) or not all(
        isinstance(key, str)
        and not isinstance(item, bool)
        and isinstance(item, int)
        for key, item in value.items()
    ):
        raise ManifestError(f"{field} must be an object of integers")
    return dict(value)


def _validate_endpoint(executor_id: str, endpoint: str) -> None:
    parsed = urlsplit(endpoint)
    if (
        not executor_id.strip()
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ManifestError(f"invalid endpoint for {executor_id}: {endpoint!r}")
    try:
        _ = parsed.port
    except ValueError as error:
        raise ManifestError(f"invalid endpoint port for {executor_id}") from error
