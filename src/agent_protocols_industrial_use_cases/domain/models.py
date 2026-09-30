"""Immutable values shared by every UC-003 protocol variant."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


@dataclass(frozen=True, slots=True)
class MaterialNeed:
    task_id: str
    part_id: str
    quantity: int
    destination: str
    urgency: str

    def __post_init__(self) -> None:
        for field_name in ("task_id", "part_id", "destination", "urgency"):
            _require_text(getattr(self, field_name), field_name)
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")


@dataclass(frozen=True, slots=True)
class InventoryItem:
    part_id: str
    available_quantity: int
    source_zone: str

    def __post_init__(self) -> None:
        _require_text(self.part_id, "part_id")
        _require_text(self.source_zone, "source_zone")
        if self.available_quantity < 0:
            raise ValueError("available_quantity must not be negative")


@dataclass(frozen=True, slots=True)
class TransportRequest:
    task_id: str
    part_id: str
    quantity: int
    source_zone: str
    destination: str
    urgency: str


@dataclass(frozen=True, slots=True)
class ExecutorStatus:
    executor_id: str
    available: bool
    position: str
    energy_level: float

    def __post_init__(self) -> None:
        _require_text(self.executor_id, "executor_id")
        _require_text(self.position, "position")
        if not 0 <= self.energy_level <= 100:
            raise ValueError("energy_level must be between 0 and 100")


@dataclass(frozen=True, slots=True)
class Bid:
    task_id: str
    executor_id: str
    eta_seconds: float
    energy_cost: float

    def __post_init__(self) -> None:
        if self.eta_seconds < 0:
            raise ValueError("eta_seconds must not be negative")
        if self.energy_cost < 0:
            raise ValueError("energy_cost must not be negative")


@dataclass(frozen=True, slots=True)
class NoBid:
    task_id: str
    executor_id: str
    reason: str


BidResponse = Bid | NoBid


@dataclass(frozen=True, slots=True)
class Award:
    task_id: str
    executor_id: str


class DeliveryStatus(StrEnum):
    COMPLETED = "completed"
    DUPLICATE_REJECTED = "duplicate_rejected"


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    task_id: str
    executor_id: str
    status: DeliveryStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None


class CoordinationStatus(StrEnum):
    COMPLETED = "completed"
    NO_AWARD = "no_award"


@dataclass(frozen=True, slots=True)
class CoordinationOutcome:
    task_id: str
    status: CoordinationStatus
    winner_id: str | None
    bids: tuple[Bid, ...]
    no_bids: tuple[NoBid, ...]
    timed_out_executor_ids: tuple[str, ...]
    delivery: DeliveryResult | None

