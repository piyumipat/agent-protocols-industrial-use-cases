"""Protocol-neutral UC-003 payload schemas and validation."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from agent_protocols_industrial_use_cases.domain.models import (
    Award,
    Bid,
    BidResponse,
    DeliveryResult,
    DeliveryStatus,
    NoBid,
    TransportRequest,
)


class PayloadError(ValueError):
    pass


def serialized_payload_bytes(payload: object) -> int:
    """Return the UTF-8 size of the canonical application JSON payload."""

    return len(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )


def _string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PayloadError(f"{key} must be a non-empty string")
    return value


def _integer(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not float(value).is_integer()
    ):
        raise PayloadError(f"{key} must be an integer")
    return int(value)


def _number(payload: dict[str, Any], key: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise PayloadError(f"{key} must be a number")
    return float(value)


def _datetime_or_none(payload: dict[str, Any], key: str) -> datetime | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise PayloadError(f"{key} must be an ISO 8601 timestamp or null")
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise PayloadError(f"{key} must be an ISO 8601 timestamp or null") from error


def encode_bid_request(request: TransportRequest) -> dict[str, Any]:
    return {
        "operation": "request_bid",
        "task_id": request.task_id,
        "part_id": request.part_id,
        "quantity": request.quantity,
        "source_zone": request.source_zone,
        "destination": request.destination,
        "urgency": request.urgency,
    }


def decode_bid_request(payload: dict[str, Any]) -> TransportRequest:
    if payload.get("operation") != "request_bid":
        raise PayloadError("expected request_bid operation")
    return TransportRequest(
        task_id=_string(payload, "task_id"),
        part_id=_string(payload, "part_id"),
        quantity=_integer(payload, "quantity"),
        source_zone=_string(payload, "source_zone"),
        destination=_string(payload, "destination"),
        urgency=_string(payload, "urgency"),
    )


def encode_bid_response(response: BidResponse) -> dict[str, Any]:
    if isinstance(response, Bid):
        return {
            "response": "bid",
            "task_id": response.task_id,
            "executor_id": response.executor_id,
            "eta_seconds": response.eta_seconds,
            "energy_cost": response.energy_cost,
        }
    return {
        "response": "no_bid",
        "task_id": response.task_id,
        "executor_id": response.executor_id,
        "reason": response.reason,
    }


def decode_bid_response(payload: dict[str, Any]) -> BidResponse:
    response_type = payload.get("response")
    if response_type == "bid":
        return Bid(
            task_id=_string(payload, "task_id"),
            executor_id=_string(payload, "executor_id"),
            eta_seconds=_number(payload, "eta_seconds"),
            energy_cost=_number(payload, "energy_cost"),
        )
    if response_type == "no_bid":
        return NoBid(
            task_id=_string(payload, "task_id"),
            executor_id=_string(payload, "executor_id"),
            reason=_string(payload, "reason"),
        )
    raise PayloadError("expected bid or no_bid response")


def encode_award(award: Award) -> dict[str, Any]:
    return {
        "operation": "award",
        "task_id": award.task_id,
        "executor_id": award.executor_id,
    }


def decode_award(payload: dict[str, Any]) -> Award:
    if payload.get("operation") != "award":
        raise PayloadError("expected award operation")
    return Award(
        task_id=_string(payload, "task_id"),
        executor_id=_string(payload, "executor_id"),
    )


def encode_delivery(result: DeliveryResult) -> dict[str, Any]:
    return {
        "response": "delivery_result",
        "task_id": result.task_id,
        "executor_id": result.executor_id,
        "status": result.status.value,
        "started_at": None if result.started_at is None else result.started_at.isoformat(),
        "completed_at": (
            None if result.completed_at is None else result.completed_at.isoformat()
        ),
    }


def decode_delivery(payload: dict[str, Any]) -> DeliveryResult:
    if payload.get("response") != "delivery_result":
        raise PayloadError("expected delivery_result response")
    try:
        status = DeliveryStatus(_string(payload, "status"))
    except ValueError as error:
        raise PayloadError("unknown delivery status") from error
    return DeliveryResult(
        task_id=_string(payload, "task_id"),
        executor_id=_string(payload, "executor_id"),
        status=status,
        started_at=_datetime_or_none(payload, "started_at"),
        completed_at=_datetime_or_none(payload, "completed_at"),
    )
