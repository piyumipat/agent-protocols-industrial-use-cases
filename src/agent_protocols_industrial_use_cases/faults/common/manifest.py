"""Inputs for one isolated fault experiment attempt."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from agent_protocols_industrial_use_cases.runtime.config import ProtocolName


class FaultManifestError(ValueError):
    """A fault run input is incomplete or inconsistent."""


class FaultId(StrEnum):
    PF01 = "pf01"
    PF02 = "pf02"
    PF06 = "pf06"
    PF08 = "pf08"


class FaultRound(StrEnum):
    BASELINE = "baseline"
    SAFEGUARD = "safeguard"


INJECTION_BOUNDARY = {
    FaultId.PF01: "configured_endpoint_before_discovery",
    FaultId.PF02: "captured_award_after_completion",
    FaultId.PF06: "durable_acceptance_before_final_response",
    FaultId.PF08: "authenticated_award_before_business_dispatch",
}
FROZEN_PROTOCOL_SPECIFICATION = {
    ProtocolName.A2A: "A2A 1.0.1",
    ProtocolName.ANP: "ANP 1.1",
    ProtocolName.AGORA: "Agora Working Standard 2025-01-19 draft",
}
FROZEN_KIT_REVISION = "1222b3792c1d347d949b7184d5131a0551ff97bb"
_RUN_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@dataclass(frozen=True, slots=True)
class FaultManifest:
    """Versioned run inputs, independent of runtime ports and generated keys."""

    schema_version: int
    run_id: str
    task_id: str
    fault: FaultId
    protocol: ProtocolName
    round: FaultRound
    repetition: int
    attempt: int
    specification_version: str
    reference_kit_version: str
    reference_kit_revision: str
    use_case_revision: str
    injection_boundary: str
    enabled_controls: tuple[str, ...]
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise FaultManifestError("schema_version must be 1")
        if not _RUN_ID.fullmatch(self.run_id):
            raise FaultManifestError("run_id must be lowercase and filename-safe")
        if self.repetition <= 0 or self.attempt <= 0:
            raise FaultManifestError("repetition and attempt must be positive")
        for name in (
            "task_id", "specification_version", "reference_kit_version",
            "reference_kit_revision", "use_case_revision",
        ):
            if not getattr(self, name).strip():
                raise FaultManifestError(f"{name} must not be empty")
        if self.injection_boundary != INJECTION_BOUNDARY[self.fault]:
            raise FaultManifestError("injection_boundary does not match the selected fault")
        if self.fault in {FaultId.PF01, FaultId.PF02} and (
            self.specification_version != FROZEN_PROTOCOL_SPECIFICATION[self.protocol]
            or self.reference_kit_version != "0.2.0"
            or self.reference_kit_revision != FROZEN_KIT_REVISION
        ):
            raise FaultManifestError("protocol or reference-kit version differs from frozen inputs")
        if len(set(self.enabled_controls)) != len(self.enabled_controls):
            raise FaultManifestError("enabled_controls must be unique")
        if not {"task_ledger", "correlated_event_recording"} <= set(self.enabled_controls):
            raise FaultManifestError("common safety and evidence controls must be enabled")
        if (
            self.protocol is ProtocolName.A2A
            and self.fault is FaultId.PF01
            and "signed_agent_cards" not in self.enabled_controls
        ):
            raise FaultManifestError("A2A PF-01 requires signed Agent Cards")
        if (
            self.protocol is ProtocolName.A2A
            and self.fault is FaultId.PF02
            and "message_idempotency" not in self.enabled_controls
        ):
            raise FaultManifestError("A2A PF-02 requires messageId idempotency")
        if not all(isinstance(key, str) for key in self.parameters):
            raise FaultManifestError("parameter names must be strings")
        try:
            json.dumps(self.parameters, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise FaultManifestError("parameters must be finite JSON values") from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "fault": self.fault.value,
            "protocol": self.protocol.value,
            "round": self.round.value,
            "repetition": self.repetition,
            "attempt": self.attempt,
            "specification_version": self.specification_version,
            "reference_kit_version": self.reference_kit_version,
            "reference_kit_revision": self.reference_kit_revision,
            "use_case_revision": self.use_case_revision,
            "injection_boundary": self.injection_boundary,
            "enabled_controls": list(self.enabled_controls),
            "parameters": self.parameters,
        }

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> FaultManifest:
        expected = set(cls.__dataclass_fields__)
        if set(document) != expected:
            raise FaultManifestError(
                f"manifest fields mismatch: missing={sorted(expected - set(document))}, "
                f"extra={sorted(set(document) - expected)}"
            )
        try:
            if any(type(document[name]) is not int for name in ("schema_version", "repetition", "attempt")):
                raise FaultManifestError("schema_version, repetition, and attempt must be integers")
            if not isinstance(document["enabled_controls"], list) or not all(
                isinstance(item, str) for item in document["enabled_controls"]
            ):
                raise FaultManifestError("enabled_controls must be strings")
            if not isinstance(document["parameters"], dict):
                raise FaultManifestError("parameters must be an object")
            for name in (
                "run_id", "task_id", "specification_version", "reference_kit_version",
                "reference_kit_revision", "use_case_revision", "injection_boundary",
            ):
                if not isinstance(document[name], str):
                    raise FaultManifestError(f"{name} must be a string")
            return cls(
                schema_version=document["schema_version"],
                run_id=document["run_id"],
                task_id=document["task_id"],
                fault=FaultId(document["fault"]),
                protocol=ProtocolName(document["protocol"]),
                round=FaultRound(document["round"]),
                repetition=document["repetition"],
                attempt=document["attempt"],
                specification_version=document["specification_version"],
                reference_kit_version=document["reference_kit_version"],
                reference_kit_revision=document["reference_kit_revision"],
                use_case_revision=document["use_case_revision"],
                injection_boundary=document["injection_boundary"],
                enabled_controls=tuple(document["enabled_controls"]),
                parameters=document["parameters"],
            )
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, FaultManifestError):
                raise
            raise FaultManifestError(f"invalid fault manifest: {error}") from error


@dataclass(frozen=True, slots=True)
class FaultRunPlan:
    """One input file that expands into independent per-run manifests."""

    schema_version: int
    task_id: str
    fault: FaultId
    round: FaultRound
    protocols: tuple[ProtocolName, ...]
    repetitions: int
    attempt: int
    reference_kit_version: str
    reference_kit_revision: str
    use_case_revision: str
    injection_boundary: str
    enabled_controls: dict[ProtocolName, tuple[str, ...]]
    parameters: dict[ProtocolName, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise FaultManifestError("schema_version must be 1")
        if not self.protocols or len(set(self.protocols)) != len(self.protocols):
            raise FaultManifestError("protocols must be non-empty and unique")
        if self.repetitions <= 0 or self.attempt <= 0:
            raise FaultManifestError("repetitions and attempt must be positive")
        for name in (
            "task_id", "reference_kit_version", "reference_kit_revision", "use_case_revision"
        ):
            if not getattr(self, name).strip():
                raise FaultManifestError(f"{name} must not be empty")
        if self.injection_boundary != INJECTION_BOUNDARY[self.fault]:
            raise FaultManifestError("injection_boundary does not match the selected fault")
        if set(self.enabled_controls) != set(self.protocols):
            raise FaultManifestError("enabled_controls must be provided for each protocol")
        if not set(self.parameters) <= set(self.protocols):
            raise FaultManifestError("parameters may only be provided for selected protocols")
        for protocol, controls in self.enabled_controls.items():
            if len(set(controls)) != len(controls):
                raise FaultManifestError(f"enabled_controls for {protocol.value} must be unique")
        if self.fault in {FaultId.PF01, FaultId.PF02} and (
            self.reference_kit_version != "0.2.0"
            or self.reference_kit_revision != FROZEN_KIT_REVISION
        ):
            raise FaultManifestError("reference-kit version differs from frozen inputs")
        try:
            json.dumps(
                {protocol.value: value for protocol, value in self.parameters.items()},
                allow_nan=False,
            )
        except (TypeError, ValueError) as error:
            raise FaultManifestError("parameters must be finite JSON values") from error

    def expand(self, batch_id: str) -> tuple[FaultManifest, ...]:
        """Create run manifests with unique IDs for this plan execution."""

        if not _RUN_ID.fullmatch(batch_id):
            raise FaultManifestError("batch_id must be lowercase and filename-safe")
        manifests = []
        for protocol in self.protocols:
            for repetition in range(1, self.repetitions + 1):
                run_id = (
                    f"{self.fault.value}-{protocol.value}-{self.round.value}"
                    f"-{batch_id}-r{repetition:02d}"
                )
                if self.attempt > 1:
                    run_id += f"-a{self.attempt:02d}"
                manifests.append(
                    FaultManifest(
                        schema_version=self.schema_version,
                        run_id=run_id,
                        task_id=self.task_id,
                        fault=self.fault,
                        protocol=protocol,
                        round=self.round,
                        repetition=repetition,
                        attempt=self.attempt,
                        specification_version=FROZEN_PROTOCOL_SPECIFICATION[protocol],
                        reference_kit_version=self.reference_kit_version,
                        reference_kit_revision=self.reference_kit_revision,
                        use_case_revision=self.use_case_revision,
                        injection_boundary=self.injection_boundary,
                        enabled_controls=self.enabled_controls[protocol],
                        parameters=self.parameters.get(protocol, {}),
                    )
                )
        return tuple(manifests)

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> FaultRunPlan:
        expected = set(cls.__dataclass_fields__) | {"kind"}
        if set(document) != expected or document.get("kind") != "fault_run_plan":
            raise FaultManifestError("invalid fault run plan fields or kind")
        if type(document.get("schema_version")) is not int:
            raise FaultManifestError("schema_version must be an integer")
        if type(document.get("repetitions")) is not int or type(document.get("attempt")) is not int:
            raise FaultManifestError("repetitions and attempt must be integers")
        if not isinstance(document.get("protocols"), list) or not all(
            isinstance(item, str) for item in document["protocols"]
        ):
            raise FaultManifestError("protocols must be a list of names")
        raw_controls = document.get("enabled_controls")
        if not isinstance(raw_controls, dict):
            raise FaultManifestError("enabled_controls must be an object")
        controls: dict[ProtocolName, tuple[str, ...]] = {}
        for name, values in raw_controls.items():
            if not isinstance(name, str) or not isinstance(values, list) or not all(
                isinstance(value, str) for value in values
            ):
                raise FaultManifestError("enabled_controls must map protocol names to string lists")
            controls[ProtocolName(name)] = tuple(values)
        raw_parameters = document.get("parameters")
        if not isinstance(raw_parameters, dict) or not all(
            isinstance(name, str) and isinstance(value, dict)
            for name, value in raw_parameters.items()
        ):
            raise FaultManifestError("parameters must map protocol names to objects")
        if not all(
            isinstance(document.get(name), str)
            for name in (
                "task_id", "reference_kit_version", "reference_kit_revision",
                "use_case_revision", "injection_boundary",
            )
        ):
            raise FaultManifestError("plan text fields must be strings")
        return cls(
            schema_version=document["schema_version"],
            task_id=document["task_id"],
            fault=FaultId(document["fault"]),
            round=FaultRound(document["round"]),
            protocols=tuple(ProtocolName(item) for item in document["protocols"]),
            repetitions=document["repetitions"],
            attempt=document["attempt"],
            reference_kit_version=document["reference_kit_version"],
            reference_kit_revision=document["reference_kit_revision"],
            use_case_revision=document["use_case_revision"],
            injection_boundary=document["injection_boundary"],
            enabled_controls=controls,
            parameters={ProtocolName(name): value for name, value in raw_parameters.items()},
        )


def load_fault_manifest(path: Path) -> FaultManifest:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FaultManifestError(f"cannot read fault manifest: {path}") from error
    if not isinstance(document, dict):
        raise FaultManifestError("fault manifest must contain an object")
    return FaultManifest.from_dict(document)


def load_fault_run_plan(path: Path) -> FaultRunPlan:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FaultManifestError(f"cannot read fault run plan: {path}") from error
    if not isinstance(document, dict):
        raise FaultManifestError("fault run plan must contain an object")
    return FaultRunPlan.from_dict(document)
