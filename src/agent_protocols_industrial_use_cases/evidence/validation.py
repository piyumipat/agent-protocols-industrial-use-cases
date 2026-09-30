"""Integrity checks for persisted UC-003 experiment evidence."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast


class EvidenceValidationError(RuntimeError):
    """Persisted evidence does not represent a valid completed UC-003 run."""


@dataclass(frozen=True, slots=True)
class ValidationReport:
    valid: bool
    validated_rounds: int
    checks: tuple[str, ...]
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "validated_rounds": self.validated_rounds,
            "checks": list(self.checks),
            "errors": list(self.errors),
        }

    def require_valid(self) -> None:
        if not self.valid:
            raise EvidenceValidationError("; ".join(self.errors))


_CHECKS = (
    "workflow_step_order",
    "deterministic_winner",
    "single_delivery",
    "outcome_consistency",
    "protocol_evidence",
)


def validate_run_evidence(run_directory: Path) -> ValidationReport:
    """Validate one completed run directory without modifying it."""

    errors: list[str] = []
    outcomes: list[dict[str, Any]] = []
    try:
        manifest = _read_object(run_directory / "manifest.json")
        outcomes = _read_array(run_directory / "outcomes.json")
        events = _read_events(run_directory / "events.jsonl")
        rounds = _integer(manifest, "rounds")
        if len(outcomes) != rounds:
            errors.append(f"outcomes.json has {len(outcomes)} outcomes; expected {rounds}")
        expected_tasks = [
            _string(manifest, "task_id") if number == 1 else f"{_string(manifest, 'task_id')}-r{number:02d}"
            for number in range(1, rounds + 1)
        ]
        if [_string(outcome, "task_id") for outcome in outcomes] != expected_tasks:
            errors.append("outcome task IDs do not match the configured rounds")
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in events:
            grouped[_string(event, "task_id")].append(event)
            if event.get("run_id") != manifest.get("run_id"):
                errors.append("event run_id does not match manifest")
            if event.get("protocol") != manifest.get("protocol"):
                errors.append("event protocol does not match manifest")
        if set(grouped) != set(expected_tasks):
            errors.append("event task IDs do not match the configured rounds")

        for number, (task_id, outcome) in enumerate(
            zip(expected_tasks, outcomes, strict=False), start=1
        ):
            task_events = grouped.get(task_id, [])
            _validate_outcome(manifest, outcome, task_id, errors)
            _validate_workflow(manifest, outcome, task_events, task_id, errors)
            _validate_persisted_outcome(run_directory, outcome, number, rounds, errors)
            _validate_round_protocol(manifest, outcome, task_events, task_id, errors)
        if outcomes and _read_object(run_directory / "outcome.json") != outcomes[-1]:
            errors.append("outcome.json does not match the final round outcome")
        _validate_protocol_setup(manifest, events, errors)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        errors.append(f"malformed evidence: {error}")

    unique_errors = tuple(dict.fromkeys(errors))
    return ValidationReport(
        valid=not unique_errors,
        validated_rounds=0 if unique_errors else len(outcomes),
        checks=_CHECKS,
        errors=unique_errors,
    )


def _validate_outcome(
    manifest: Mapping[str, Any],
    outcome: Mapping[str, Any],
    task_id: str,
    errors: list[str],
) -> None:
    label = f"task {task_id}"
    bids = _mapping_list(outcome, "bids")
    no_bids = _mapping_list(outcome, "no_bids")
    timed_out = _string_list(outcome, "timed_out_executor_ids")
    executor_ids = _string_list(manifest, "executor_ids")
    if outcome.get("status") != "completed":
        errors.append(f"{label}: outcome is not completed")
    if not bids:
        errors.append(f"{label}: no bids were recorded")
        return
    winner = min(
        bids,
        key=lambda bid: (
            _number(bid, "eta_seconds"),
            _number(bid, "energy_cost"),
            _string(bid, "executor_id"),
        ),
    )
    winner_id = _string(outcome, "winner_id")
    if winner_id != _string(winner, "executor_id"):
        errors.append(f"{label}: winner does not match deterministic bid ordering")
    represented = [
        *(_string(bid, "executor_id") for bid in bids),
        *(_string(no_bid, "executor_id") for no_bid in no_bids),
        *timed_out,
    ]
    if Counter(represented) != Counter(executor_ids):
        errors.append(f"{label}: bid/no-bid/timeout partition is inconsistent")
    if any(_string(item, "task_id") != task_id for item in [*bids, *no_bids]):
        errors.append(f"{label}: response task IDs are inconsistent")
    delivery = _mapping(outcome, "delivery")
    if (
        delivery.get("status") != "completed"
        or delivery.get("task_id") != task_id
        or delivery.get("executor_id") != winner_id
    ):
        errors.append(f"{label}: delivery is inconsistent with the winner")


def _validate_workflow(
    manifest: Mapping[str, Any],
    outcome: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    task_id: str,
    errors: list[str],
) -> None:
    label = f"task {task_id}"
    if any(event.get("status") == "failed" for event in events):
        errors.append(f"{label}: failed workflow event recorded")
    anchors = [
        _one_timestamp(events, "UC003-01", "step.started", "started", errors, label),
        _one_timestamp(events, "UC003-01", "step.completed", "completed", errors, label),
        _one_timestamp(events, "UC003-02", "step.started", "started", errors, label),
        _one_timestamp(events, "UC003-02", "step.completed", "completed", errors, label),
        _one_timestamp(events, "UC003-05", "step.started", "started", errors, label),
        _one_timestamp(events, "UC003-05", "step.completed", "completed", errors, label),
        _one_timestamp(events, "UC003-06", "step.started", "started", errors, label),
        _one_timestamp(events, "UC003-06", "step.completed", "completed", errors, label),
        _one_timestamp(events, "UC003-08", "step.started", "started", errors, label),
        _one_timestamp(events, "UC003-08", "step.completed", "completed", errors, label),
    ]
    if all(value is not None for value in anchors):
        values = cast(list[int], anchors)
        if values != sorted(values):
            errors.append(f"{label}: workflow steps are out of order")

    for executor_id in _string_list(manifest, "executor_ids"):
        status_start = _peer_timestamp(
            events, "UC003-03", "step.started", executor_id, "actor"
        )
        status_end = _available_status_timestamp(events, executor_id)
        bid_start = _peer_timestamp(events, "UC003-04", "step.started", executor_id, "actor")
        bid_end = _bid_completion_timestamp(events, executor_id)
        if None in {status_start, status_end, bid_start, bid_end}:
            errors.append(f"{label}: incomplete bid workflow for {executor_id}")
        elif not cast(int, status_start) <= cast(int, status_end) <= cast(
            int, bid_start
        ) <= cast(int, bid_end):
            errors.append(f"{label}: bid workflow is out of order for {executor_id}")

    deliveries = [
        event
        for event in events
        if event.get("step_id") == "UC003-07"
        and event.get("event_type") == "delivery.completed"
        and event.get("status") == "completed"
    ]
    if len(deliveries) != 1 or deliveries[0].get("actor") != outcome.get("winner_id"):
        errors.append(f"{label}: expected exactly one winner delivery")
    elif anchors[6] is not None and anchors[7] is not None:
        delivery_time = int(deliveries[0]["monotonic_ns"])
        if not anchors[6] <= delivery_time <= anchors[7]:
            errors.append(f"{label}: delivery occurred outside the award step")


def _validate_persisted_outcome(
    run_directory: Path,
    outcome: Mapping[str, Any],
    number: int,
    rounds: int,
    errors: list[str],
) -> None:
    round_outcome = _read_object(run_directory / f"outcome-round-{number:02d}.json")
    mcp_name = "mcp-outcome.json" if rounds == 1 else f"mcp-outcome-round-{number:02d}.json"
    mcp_outcome = _read_object(run_directory / mcp_name)
    if round_outcome != outcome or mcp_outcome != outcome:
        errors.append(f"round {number}: persisted outcome copies differ")


def _validate_round_protocol(
    manifest: Mapping[str, Any],
    outcome: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    task_id: str,
    errors: list[str],
) -> None:
    protocol = _string(manifest, "protocol")
    executor_ids = _string_list(manifest, "executor_ids")
    winner_id = _string(outcome, "winner_id")
    expected = {
        f"{protocol}.bid_request.sent": (executor_ids, "started"),
        f"{protocol}.bid_response.received": (executor_ids, "completed"),
        f"{protocol}.award.sent": ([winner_id], "started"),
        f"{protocol}.delivery.received": ([winner_id], "completed"),
    }
    for event_type, (peers, status) in expected.items():
        actual = [
            str(event.get("peer"))
            for event in events
            if event.get("event_type") == event_type
            and event.get("status") == status
            and event.get("layer") == "protocol_native"
        ]
        if Counter(actual) != Counter(peers):
            errors.append(f"task {task_id}: invalid {event_type} peer coverage")


def _validate_protocol_setup(
    manifest: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    errors: list[str],
) -> None:
    protocol = _string(manifest, "protocol")
    executor_ids = _string_list(manifest, "executor_ids")
    required: list[tuple[str, str]]
    if protocol == "a2a":
        required = [("a2a.agent_card.fetched", "peer")]
    elif protocol == "anp":
        required = [
            ("anp.discovery.completed", "peer"),
            ("anp.did.verified", "peer"),
        ]
    elif manifest.get("condition") == "stable":
        required = [
            ("agora.wellknown.fetched", "peer"),
            ("agora.protocol_document.shared", "peer"),
        ]
        if _has_event(events, "agora.protocol_document.negotiated"):
            errors.append("stable Agora evidence unexpectedly contains negotiation")
    else:
        required = [
            ("agora.wellknown.fetched", "peer"),
            ("agora.protocol_document.proposed", "actor"),
            ("agora.protocol_document.negotiated", "peer"),
        ]
        if _has_event(events, "agora.protocol_document.shared"):
            errors.append("dynamic Agora evidence unexpectedly uses a pre-shared protocol")
    for event_type, identity_field in required:
        identities = [
            str(event.get(identity_field))
            for event in events
            if event.get("event_type") == event_type and event.get("status") == "completed"
        ]
        if Counter(identities) != Counter(executor_ids):
            errors.append(f"invalid {event_type} Executor coverage")


def _one_timestamp(
    events: Sequence[Mapping[str, Any]],
    step_id: str,
    event_type: str,
    status: str,
    errors: list[str],
    label: str,
) -> int | None:
    matches = [
        int(event["monotonic_ns"])
        for event in events
        if event.get("step_id") == step_id
        and event.get("event_type") == event_type
        and event.get("status") == status
    ]
    if len(matches) != 1:
        errors.append(f"{label}: expected one {step_id} {event_type}")
        return None
    return matches[0]


def _peer_timestamp(
    events: Sequence[Mapping[str, Any]],
    step_id: str,
    event_type: str,
    identity: str,
    identity_field: str,
) -> int | None:
    matches = [
        int(event["monotonic_ns"])
        for event in events
        if event.get("step_id") == step_id
        and event.get("event_type") == event_type
        and event.get(identity_field) == identity
    ]
    return matches[0] if len(matches) == 1 else None


def _available_status_timestamp(
    events: Sequence[Mapping[str, Any]], executor_id: str
) -> int | None:
    matches = [
        int(event["monotonic_ns"])
        for event in events
        if event.get("step_id") == "UC003-03"
        and event.get("event_type") == "mcp.executor_status.returned"
        and event.get("actor") == executor_id
        and isinstance(event.get("details"), Mapping)
        and isinstance(cast(Mapping[str, Any], event["details"]).get("available"), bool)
    ]
    return matches[0] if len(matches) == 1 else None


def _bid_completion_timestamp(
    events: Sequence[Mapping[str, Any]], executor_id: str
) -> int | None:
    matches = [
        int(event["monotonic_ns"])
        for event in events
        if event.get("step_id") == "UC003-04"
        and event.get("event_type") in {"bid.submitted", "bid.no_bid"}
        and event.get("actor") == executor_id
        and event.get("status") == "completed"
    ]
    return matches[0] if len(matches) == 1 else None


def _has_event(events: Sequence[Mapping[str, Any]], event_type: str) -> bool:
    return any(event.get("event_type") == event_type for event in events)


def _read_events(path: Path) -> list[dict[str, Any]]:
    return [cast(dict[str, Any], json.loads(line)) for line in path.read_text().splitlines()]


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return cast(dict[str, Any], value)


def _read_array(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text())
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise TypeError(f"expected JSON object array: {path}")
    return cast(list[dict[str, Any]], value)


def _mapping(document: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = document.get(key)
    if not isinstance(value, dict):
        raise TypeError(f"{key} must be an object")
    return cast(dict[str, Any], value)


def _mapping_list(document: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    value = document.get(key)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise TypeError(f"{key} must be an object array")
    return cast(list[dict[str, Any]], value)


def _string(document: Mapping[str, Any], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _string_list(document: Mapping[str, Any], key: str) -> list[str]:
    value = document.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError(f"{key} must be a string array")
    return cast(list[str], value)


def _integer(document: Mapping[str, Any], key: str) -> int:
    value = document.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer")
    return value


def _number(document: Mapping[str, Any], key: str) -> float:
    value = document.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{key} must be a number")
    return float(value)
