"""Deterministic UC-003 business policies."""

from collections.abc import Iterable

from .models import Bid


def select_winner(bids: Iterable[Bid]) -> Bid | None:
    """Select lowest ETA, then energy cost, then Executor ID."""

    return min(
        bids,
        key=lambda bid: (bid.eta_seconds, bid.energy_cost, bid.executor_id),
        default=None,
    )

