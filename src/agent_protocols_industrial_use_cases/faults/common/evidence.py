"""Immutable fault-run bundles and minimum evidence validation."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

from agent_protocols_industrial_use_cases.runtime.config import ProtocolName
from agent_protocols_industrial_use_cases.evidence.artifacts import JsonlEventSink

from .manifest import FaultId, FaultManifest, FaultRound


class FaultEvidenceError(RuntimeError):
    """A run bundle cannot be created or completed safely."""


_DETAILS_BY_FAULT: dict[FaultId, frozenset[str]] = {
    FaultId.PF01: frozenset({
        "endpoint_swap", "claimed_identity", "verified_identity",
        "approved_identity", "admission_decision", "rogue_bid_reached",
    }),
    FaultId.PF02: frozenset({
        "original_request_sha256", "replay_request_sha256",
        "first_response_sha256", "replay_response_sha256",
        "authentication_mode", "ledger_decision", "original_status",
        "replay_http_status", "request_method", "request_url", "credential_valid",
    }),
    FaultId.PF06: frozenset({
        "acceptance_audit_path", "acceptance_durable", "stop_observed",
        "caller_known_task_id", "recovery_state",
    }),
    FaultId.PF08: frozenset({
        "verified_caller", "verified_requests", "bid_policy_decision",
        "award_policy_decision",
    }),
}


@dataclass(frozen=True, slots=True)
class FaultObservation:
    """Consequences recorded by the controller after one injection."""

    injection_count: int
    caller_outcome: str
    first_detection_layer: str | None
    first_detection_event: str | None
    business_dispatches: int
    delivery_count: int
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value < 0
            for value in (self.injection_count, self.business_dispatches, self.delivery_count)
        ):
            raise ValueError("counts must be non-negative integers")
        if not self.caller_outcome.strip():
            raise ValueError("caller_outcome must not be empty")
        try:
            json.dumps(self.details, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError("details must be finite JSON values") from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "injection_count": self.injection_count,
            "caller_outcome": self.caller_outcome,
            "first_detection_layer": self.first_detection_layer,
            "first_detection_event": self.first_detection_event,
            "business_dispatches": self.business_dispatches,
            "delivery_count": self.delivery_count,
            "details": self.details,
        }


@dataclass(frozen=True, slots=True)
class FaultValidation:
    valid: bool
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, "errors": list(self.errors)}


class FaultRunBundle:
    """One exclusive result directory under the selected fault's data tree."""

    def __init__(self, faults_root: Path, manifest: FaultManifest) -> None:
        self.manifest = manifest
        self.directory = faults_root / manifest.fault.value / "results" / manifest.run_id
        self._event_sink: JsonlEventSink | None = None

    @property
    def event_sink(self) -> JsonlEventSink:
        if self._event_sink is None:
            raise FaultEvidenceError("bundle is not open")
        return self._event_sink

    def __enter__(self) -> Self:
        try:
            self.directory.mkdir(parents=True, exist_ok=False)
            _write_json(self.directory / "manifest.json", self.manifest.to_dict())
            self._event_sink = JsonlEventSink(self.directory / "events.jsonl")
        except OSError as error:
            raise FaultEvidenceError(f"cannot create run bundle: {self.directory}") from error
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        del traceback
        if self._event_sink is not None:
            self._event_sink.close()
            self._event_sink = None
        if exc_type is not None:
            try:
                _write_json(
                    self.directory / "failure.json",
                    {"error_type": exc_type.__name__, "message": str(exc)},
                )
            except OSError:
                pass

    def complete(self, observation: FaultObservation) -> FaultValidation:
        if self._event_sink is None:
            raise FaultEvidenceError("bundle is not open")
        self._event_sink.close()
        self._event_sink = None
        _write_json(self.directory / "observation.json", observation.to_dict())
        validation = validate_fault_bundle(self.directory)
        _write_json(self.directory / "validation.json", validation.to_dict())
        return validation


def validate_fault_bundle(directory: Path) -> FaultValidation:
    """Check run completeness without treating an unsafe outcome as invalid data."""

    errors: list[str] = []
    try:
        manifest_document = json.loads((directory / "manifest.json").read_text())
        observation = json.loads((directory / "observation.json").read_text())
        events = [
            json.loads(line)
            for line in (directory / "events.jsonl").read_text().splitlines()
        ]
        if not isinstance(manifest_document, dict) or not all(
            isinstance(event, dict) for event in events
        ):
            raise ValueError("manifest and event records must be objects")
        manifest = FaultManifest.from_dict(manifest_document)
    except (OSError, ValueError, TypeError, KeyError) as error:
        return FaultValidation(False, (f"unreadable run evidence: {error}",))
    if not isinstance(observation, dict):
        return FaultValidation(False, ("observation must be an object",))
    if type(observation.get("injection_count")) is not int or observation["injection_count"] != 1:
        errors.append("exactly one injection must be observed")
    injected = [
        event for event in events
        if event.get("event_type") == "fault.injected"
        and event.get("status") == "completed"
        and event.get("layer") == "external_control"
    ]
    if len(injected) != 1:
        errors.append("exactly one fault.injected event is required")
    if not events:
        errors.append("event stream is empty")
    for event in events:
        if (
            event.get("run_id") != manifest.run_id
            or event.get("task_id") != manifest.task_id
            or event.get("protocol") != manifest.protocol.value
        ):
            errors.append("event correlation differs from manifest")
            break
    for name in (
        "caller_outcome", "first_detection_layer", "first_detection_event",
        "business_dispatches", "delivery_count",
    ):
        if name not in observation:
            errors.append(f"missing observation field: {name}")
    if not isinstance(observation.get("caller_outcome"), str) or not observation.get("caller_outcome"):
        errors.append("caller_outcome must be non-empty text")
    for name in ("first_detection_layer", "first_detection_event"):
        value = observation.get(name)
        if value is not None and (not isinstance(value, str) or not value):
            errors.append(f"{name} must be non-empty text or null")
    detection_layer = observation.get("first_detection_layer")
    detection_event = observation.get("first_detection_event")
    if (detection_layer is None) != (detection_event is None):
        errors.append("first detection layer and event must appear together")
    elif (
        isinstance(detection_layer, str)
        and isinstance(detection_event, str)
        and not any(
            event.get("layer") == detection_layer
            and event.get("event_type") == detection_event
            for event in events
        )
    ):
        errors.append("first detector is absent from the event stream")
    for name in ("business_dispatches", "delivery_count"):
        value = observation.get(name)
        if type(value) is not int or value < 0:
            errors.append(f"{name} must be a non-negative integer")
    details = observation.get("details")
    if not isinstance(details, dict):
        errors.append("observation details must be an object")
    else:
        for name in sorted(_DETAILS_BY_FAULT[manifest.fault] - set(details)):
            errors.append(f"missing {manifest.fault.value} detail: {name}")
        if manifest.fault is FaultId.PF01 and (
            details.get("endpoint_swap") is not True
            or not details.get("verified_identity")
            or not details.get("approved_identity")
            or details.get("verified_identity") == details.get("approved_identity")
            or details.get("genuine_endpoint_running") is not True
            or details.get("rogue_endpoint_running") is not True
        ):
            errors.append("PF-01 endpoint or identity proof is missing")
        if manifest.fault is FaultId.PF01:
            errors.extend(_validate_pf01(manifest, observation, events))
        if manifest.fault is FaultId.PF02:
            errors.extend(_validate_pf02(manifest, observation, events))
        if manifest.fault is FaultId.PF06 and (
            details.get("acceptance_durable") is not True
            or details.get("stop_observed") is not True
            or details.get("restart_observed") is not True
            or details.get("blind_retry") is not False
            or details.get("process_exit_code") != 86
        ):
            errors.append("PF-06 acceptance or stop proof is missing")
        if manifest.fault is FaultId.PF06:
            acceptance = details.get("acceptance_audit_path")
            delivery = details.get("delivery_audit_path")
            if (
                not isinstance(acceptance, str)
                or Path(acceptance) != directory / "acceptance-audit.json"
                or not isinstance(delivery, str)
                or Path(delivery) != directory / "delivery-audit.jsonl"
            ):
                errors.append("PF-06 audit paths differ from the run bundle")
            else:
                try:
                    audit = json.loads(Path(acceptance).read_text(encoding="utf-8"))
                    delivered_bytes = Path(delivery).stat().st_size
                except (OSError, ValueError) as error:
                    errors.append(f"PF-06 audit is unreadable: {error}")
                else:
                    if audit != {
                        "run_id": manifest.run_id,
                        "task_id": manifest.task_id,
                        "executor_id": "executor-01",
                        "state": "accepted_before_delivery",
                    } or delivered_bytes != 0:
                        errors.append("PF-06 audit does not prove accepted, undelivered work")
            if manifest.protocol is ProtocolName.A2A:
                known_task_id = details.get("caller_known_task_id")
                delivered_before_crash = details.get("task_id_delivered_before_crash")
                lookup = details.get("status_lookup")
                if manifest.parameters.get("task_id_delivery_barrier") is True:
                    if (
                        not isinstance(known_task_id, str)
                        or not known_task_id
                        or delivered_before_crash is not True
                        or not isinstance(lookup, str)
                        or not lookup
                        or lookup == "not_attempted_no_task_id"
                    ):
                        errors.append("PF-06 A2A type 2 requires a delivered Task ID and status lookup")
                elif (
                    known_task_id is not None
                    or delivered_before_crash is True
                    or lookup != "not_attempted_no_task_id"
                ):
                    errors.append("PF-06 A2A type 1 must have no Task ID or status lookup")
        if manifest.fault is FaultId.PF08 and (
            not isinstance(details.get("verified_caller"), str)
            or not details.get("verified_caller")
            or details.get("verified_requests") != 2
            or not isinstance(details.get("bid_policy_decision"), str)
            or details.get("bid_policy_decision") not in {"allowed", "denied"}
            or not isinstance(details.get("award_policy_decision"), str)
            or details.get("award_policy_decision") not in {"allowed", "denied"}
        ):
            errors.append("PF-08 authenticated bid and award permission decisions are missing")
    return FaultValidation(not errors, tuple(errors))


def _validate_pf02(
    manifest: FaultManifest, observation: dict[str, Any], events: list[dict[str, Any]]
) -> list[str]:
    errors: list[str] = []
    details = observation["details"]
    injected = [
        (index, event) for index, event in enumerate(events)
        if event.get("event_type") == "fault.injected"
        and event.get("status") == "completed"
    ]
    completed_replays = [
        (index, event) for index, event in enumerate(events)
        if event.get("event_type") == "fault.replay.completed"
        and event.get("status") == "completed"
    ]
    if len(injected) != 1 or len(completed_replays) != 1:
        return ["PF-02 requires one replay injection and one completed replay record"]
    injection_index, injection = injected[0]
    replay_index, replay = completed_replays[0]
    injection_details = injection.get("details")
    replay_details = replay.get("details")
    if not isinstance(injection_details, dict) or not isinstance(replay_details, dict):
        return ["PF-02 replay event details must be objects"]

    deliveries_before_replay = [
        index for index, event in enumerate(events)
        if event.get("event_type") == "delivery.completed"
        and event.get("status") == "completed"
        and index < injection_index
    ]
    bid_before_replay = [
        index for index, event in enumerate(events)
        if event.get("event_type") == "bid.submitted"
        and event.get("status") == "completed"
        and index < injection_index
    ]
    if len(deliveries_before_replay) != 1 or len(bid_before_replay) != 1:
        errors.append("PF-02 original bid and one completed delivery must precede replay")
    if replay_index <= injection_index:
        errors.append("PF-02 replay completion must follow its injection")

    original_hash = details.get("original_request_sha256")
    replay_hash = details.get("replay_request_sha256")
    request_matches = (
        isinstance(original_hash, str)
        and len(original_hash) == 64
        and original_hash == replay_hash
    )
    if (
        not request_matches
        or replay_details.get("request_matches_original") is not True
        or injection_details.get("original_request_sha256") != original_hash
        or replay_details.get("replay_request_sha256") != replay_hash
        or injection_details.get("method") != details.get("request_method")
        or replay_details.get("method") != details.get("request_method")
        or injection_details.get("url") != details.get("request_url")
        or replay_details.get("url") != details.get("request_url")
        or injection_details.get("replay_count") != 1
    ):
        errors.append("PF-02 replay must match the captured method, URL, headers, and body once")
    if details.get("original_status") != "completed":
        errors.append("PF-02 original award did not complete")
    replay_status = details.get("replay_http_status")
    if (
        type(replay_status) is not int
        or not 100 <= replay_status <= 599
        or replay_details.get("replay_http_status") != replay_status
        or not isinstance(details.get("replay_response_sha256"), str)
        or replay_details.get("replay_response_sha256") != details.get("replay_response_sha256")
    ):
        errors.append("PF-02 replay endpoint response is not evidenced")

    if manifest.protocol is ProtocolName.ANP:
        authenticated_replay = any(
            event.get("event_type") == "anp.caller.authenticated"
            and event.get("status") == "completed"
            and event.get("step_id") == "UC003-06"
            and index > injection_index
            and index < replay_index
            for index, event in enumerate(events)
        )
        if (
            details.get("authentication_mode") != "bearer_token"
            or details.get("bearer_token_reused") is not True
            or details.get("credential_valid") is not True
            or replay_details.get("credential_valid") is not True
            or not authenticated_replay
        ):
            errors.append("PF-02 ANP replay did not prove reuse of a valid bearer token")
    elif manifest.protocol is ProtocolName.A2A:
        if (
            details.get("authentication_mode") != "anonymous"
            or details.get("credential_valid") is not None
            or details.get("idempotency_retention_seconds") != 3600.0
            or details.get("idempotency_scope") != "process_local"
        ):
            errors.append("PF-02 A2A idempotency settings or authentication mode differ")
    else:
        if (
            details.get("authentication_mode") != "none"
            or details.get("credential_valid") is not None
            or details.get("transport_https") is not True
            or not str(details.get("request_url", "")).startswith("https://")
        ):
            errors.append("PF-02 Agora replay must use HTTPS without protocol authentication")

    dispatch_events = [
        event for event in events
        if event.get("step_id") == "UC003-07"
        and event.get("actor") == "executor-01"
        and event.get("peer") == "welding-cell"
        and event.get("status") == "started"
    ]
    delivered_count = sum(
        event.get("event_type") == "delivery.completed"
        and event.get("status") == "completed"
        for event in events
    )
    if (
        len(dispatch_events) != observation.get("business_dispatches")
        or delivered_count != observation.get("delivery_count")
        or observation.get("delivery_count", 0) < 1
    ):
        errors.append("PF-02 business dispatch or delivery counts disagree with events")
    duplicate_rejections = [
        event for event in events
        if event.get("event_type") == "task_ledger.duplicate_rejected"
        and event.get("status") == "failed"
    ]
    if (
        observation.get("delivery_count") == 1
        and observation.get("business_dispatches") == 2
        and (
            len(duplicate_rejections) != 1
            or details.get("ledger_decision") != "duplicate_rejected"
        )
    ):
        errors.append("PF-02 ledger detection is not proved")
    if observation.get("delivery_count") == 1 and observation.get("business_dispatches") == 1:
        cache_detection = any(
            event.get("event_type") == "a2a.message_id.cache_inferred"
            and event.get("layer") == "protocol_native"
            for event in events
        )
        if (
            manifest.protocol is not ProtocolName.A2A
            or not cache_detection
            or details.get("ledger_decision") != "original_only"
        ):
            errors.append("PF-02 protocol idempotency detection is not proved")
    return errors


def _validate_pf01(
    manifest: FaultManifest, observation: dict[str, Any], events: list[dict[str, Any]]
) -> list[str]:
    errors: list[str] = []
    details = observation["details"]
    configured = [
        (index, event) for index, event in enumerate(events)
        if event.get("event_type") == "fleet.registry.initialized"
        and event.get("status") == "completed"
    ]
    injected = [
        (index, event) for index, event in enumerate(events)
        if event.get("event_type") == "fault.injected"
        and event.get("status") == "completed"
    ]
    if len(configured) != 1 or len(injected) != 1:
        return ["PF-01 requires one registry setup and one endpoint substitution"]
    setup_index, setup = configured[0]
    swap_index, swap = injected[0]
    setup_details = setup.get("details")
    swap_details = swap.get("details")
    if not isinstance(setup_details, dict) or not isinstance(swap_details, dict):
        return ["PF-01 registry event details must be objects"]
    if (
        setup_index >= swap_index
        or setup.get("actor") != "fleet-registry"
        or setup.get("peer") != "executor-01"
        or setup_details.get("approved_identity") != details.get("approved_identity")
        or not setup_details.get("endpoint")
        or swap_details.get("previous_endpoint") != setup_details.get("endpoint")
        or not swap_details.get("configured_endpoint")
        or swap_details.get("configured_endpoint") == setup_details.get("endpoint")
        or swap_details.get("boundary") != manifest.injection_boundary
    ):
        errors.append("PF-01 registry state was not proved before the swap")

    verification_event = {
        ProtocolName.A2A: ("a2a.agent_card.signature_verified", "key_id", "valid_signature"),
        ProtocolName.ANP: ("anp.did.verified", "executor_did", "did_link_verified"),
        ProtocolName.AGORA: (
            "agora.https.certificate_verified", "certificate_sha256", "tls_certificate"
        ),
    }[manifest.protocol]
    event_name, identity_field, verification_label = verification_event
    verified = [
        (index, event) for index, event in enumerate(events)
        if event.get("event_type") == event_name and event.get("status") == "completed"
    ]
    if (
        len(verified) != 1
        or verified[0][0] <= swap_index
        or not isinstance(verified[0][1].get("details"), dict)
        or verified[0][1]["details"].get(identity_field) != details.get("verified_identity")
        or details.get("identity_verification") != verification_label
    ):
        errors.append("PF-01 rogue credential verification is not proved")

    denials = [
        (index, event) for index, event in enumerate(events)
        if event.get("event_type") == "fleet.identity.denied"
        and event.get("status") == "failed"
        and event.get("layer") == "external_control"
    ]
    if manifest.round is FaultRound.BASELINE:
        if denials:
            errors.append("PF-01 baseline must not apply plant identity admission")
    else:
        admission = details.get("admission_decision")
        if admission not in {"allowed", "denied"}:
            errors.append("PF-01 safeguard admission decision is missing")
        if admission == "denied" and (
            len(denials) != 1
            or len(verified) != 1
            or denials[0][0] <= verified[0][0]
            or not isinstance(denials[0][1].get("details"), dict)
            or denials[0][1]["details"].get("verified_identity")
            != details.get("verified_identity")
            or denials[0][1]["details"].get("approved_identity")
            != details.get("approved_identity")
            or observation.get("first_detection_layer") != "external_control"
            or observation.get("first_detection_event") != "fleet.identity.denied"
        ):
            errors.append("PF-01 safeguard denial is not proved")
        if admission == "allowed" and denials:
            errors.append("PF-01 allowed admission conflicts with denial event")

    sent = [
        (index, event) for index, event in enumerate(events)
        if event.get("event_type") == f"{manifest.protocol.value}.bid_request.sent"
        and event.get("status") == "started"
    ]
    received = [
        (index, event) for index, event in enumerate(events)
        if event.get("event_type") == "pf01.rogue_bid_request.received"
        and event.get("status") == "completed"
        and event.get("actor") == "rogue-endpoint"
    ]
    responses = [
        (index, event) for index, event in enumerate(events)
        if event.get("event_type") == f"{manifest.protocol.value}.bid_response.received"
        and event.get("status") == "completed"
    ]
    bid_reached = details.get("rogue_bid_reached")
    if (
        type(bid_reached) is not bool
        or len(sent) not in {0, 1}
        or len(received) != len(sent)
        or len(responses) > len(received)
        or (sent and not swap_index < sent[0][0] < received[0][0])
        or (responses and not received[0][0] < responses[0][0])
        or details.get("rogue_bid_requests") != len(received)
        or observation.get("business_dispatches") != 0
        or observation.get("delivery_count") != 0
        or any(
            isinstance(event.get("event_type"), str)
            and event["event_type"].endswith(".award.sent")
            for event in events
        )
        or any(event.get("event_type") == "delivery.completed" for event in events)
    ):
        errors.append("PF-01 bid request or task counts are not proved")
    if manifest.round is FaultRound.BASELINE:
        if bid_reached is True:
            if (
                len(responses) != 1
                or observation.get("caller_outcome") != "rogue_bid_received"
                or observation.get("first_detection_layer") is not None
                or observation.get("first_detection_event") is not None
            ):
                errors.append("PF-01 accepted rogue bid is not proved")
        elif bid_reached is False:
            detection = [
                index for index, event in enumerate(events)
                if event.get("event_type") == observation.get("first_detection_event")
                and event.get("layer") == observation.get("first_detection_layer")
            ]
            if (
                observation.get("caller_outcome") != "rogue_bid_rejected"
                or not detection
                or len(verified) != 1
                or detection[0] <= verified[0][0]
            ):
                errors.append("PF-01 valid baseline rejection is not proved")
    else:
        expected = 1 if bid_reached is True else 0
        if (
            len(sent) != expected
            or len(responses) != expected
            or details.get("admission_decision")
            != ("allowed" if expected else "denied")
            or observation.get("caller_outcome")
            != ("rogue_bid_received" if expected else "identity_rejected")
            or (expected == 0 and (
                observation.get("first_detection_layer") != "external_control"
                or observation.get("first_detection_event") != "fleet.identity.denied"
            ))
            or (expected == 1 and (
                observation.get("first_detection_layer") is not None
                or observation.get("first_detection_event") is not None
            ))
        ):
            errors.append("PF-01 safeguard outcome is not proved")
    return errors


def _write_json(path: Path, payload: object) -> None:
    with path.open("x", encoding="utf-8") as output:
        json.dump(payload, output, indent=2, sort_keys=True, allow_nan=False)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
