"""JSON payload contracts at the UC-003 MCP boundary."""

from __future__ import annotations

from typing_extensions import TypedDict

from agent_protocols_industrial_use_cases.domain.models import CoordinationOutcome


class InventoryPayload(TypedDict):
    part_id: str
    available_quantity: int
    source_zone: str


class ExecutorStatusPayload(TypedDict):
    executor_id: str
    available: bool
    position: str
    energy_level: float


class BidPayload(TypedDict):
    task_id: str
    executor_id: str
    eta_seconds: float
    energy_cost: float


class NoBidPayload(TypedDict):
    task_id: str
    executor_id: str
    reason: str


class DeliveryPayload(TypedDict):
    task_id: str
    executor_id: str
    status: str
    started_at: str | None
    completed_at: str | None


class CoordinationOutcomePayload(TypedDict):
    task_id: str
    status: str
    winner_id: str | None
    bids: list[BidPayload]
    no_bids: list[NoBidPayload]
    timed_out_executor_ids: list[str]
    delivery: DeliveryPayload | None


class OutcomeAcknowledgement(TypedDict):
    recorded: bool
    task_id: str


def encode_outcome(outcome: CoordinationOutcome) -> CoordinationOutcomePayload:
    delivery = outcome.delivery
    delivery_payload: DeliveryPayload | None = None
    if delivery is not None:
        delivery_payload = {
            "task_id": delivery.task_id,
            "executor_id": delivery.executor_id,
            "status": delivery.status.value,
            "started_at": (
                None if delivery.started_at is None else delivery.started_at.isoformat()
            ),
            "completed_at": (
                None if delivery.completed_at is None else delivery.completed_at.isoformat()
            ),
        }

    return {
        "task_id": outcome.task_id,
        "status": outcome.status.value,
        "winner_id": outcome.winner_id,
        "bids": [
            {
                "task_id": bid.task_id,
                "executor_id": bid.executor_id,
                "eta_seconds": bid.eta_seconds,
                "energy_cost": bid.energy_cost,
            }
            for bid in outcome.bids
        ],
        "no_bids": [
            {
                "task_id": no_bid.task_id,
                "executor_id": no_bid.executor_id,
                "reason": no_bid.reason,
            }
            for no_bid in outcome.no_bids
        ],
        "timed_out_executor_ids": list(outcome.timed_out_executor_ids),
        "delivery": delivery_payload,
    }
