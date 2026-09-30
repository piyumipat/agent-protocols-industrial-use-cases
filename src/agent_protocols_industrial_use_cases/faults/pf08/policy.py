"""Independent bid-only caller policy used by PF-08 baseline runs."""

from __future__ import annotations

import json
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from agent_protocols_industrial_use_cases.domain.workflow import EventLayer, StepId, WorkflowEvents


class BidOnlyPolicy:
    """One isolated policy entry and short-lived test credential."""

    def __init__(self, events: WorkflowEvents, *, caller: str = "observer-01") -> None:
        self.events = events
        self.caller = caller
        self.permissions = {caller: frozenset({"request_bid"})}
        self._secret = secrets.token_bytes(32)
        self.verified_requests = 0
        self.bid_decision: str | None = None
        self.award_decision: str | None = None
        self.bid_allowed = False
        self.award_denied = False

    def credential(self) -> str:
        return jwt.encode(
            {
                "sub": self.caller,
                "iss": "uc003-fault-fixture",
                "exp": datetime.now(UTC) + timedelta(minutes=5),
            },
            self._secret,
            algorithm="HS256",
        )

    def verify(self, authorization: str | None) -> str:
        if authorization is None or not authorization.startswith("Bearer "):
            raise ValueError("bearer credential required")
        payload = jwt.decode(
            authorization.removeprefix("Bearer "),
            self._secret,
            algorithms=["HS256"],
            issuer="uc003-fault-fixture",
        )
        caller = payload.get("sub")
        if caller != self.caller:
            raise ValueError("unexpected caller")
        self.verified_requests += 1
        return str(caller)

    def authorize(self, caller: str, operation: str) -> bool:
        allowed = operation in self.permissions.get(caller, frozenset())
        step = StepId.SUBMIT_AND_COLLECT_BIDS if operation == "request_bid" else StepId.AWARD_TASK
        if operation == "award":
            self.events.completed(
                step,
                actor="fault-injector",
                peer=caller,
                layer=EventLayer.EXTERNAL_CONTROL,
                event_type="fault.injected",
            )
        decision = "allowed" if allowed else "denied"
        if operation == "request_bid":
            self.bid_decision = decision
            self.bid_allowed = allowed
            event_type = f"policy.bid.{decision}"
        else:
            self.award_decision = decision
            self.award_denied = not allowed
            event_type = f"policy.award.{decision}"
        if allowed:
            self.events.completed(
                step,
                actor="executor-01",
                peer=caller,
                layer=EventLayer.EXTERNAL_CONTROL,
                event_type=event_type,
            )
        else:
            self.events.failed(
                step,
                actor="executor-01",
                peer=caller,
                layer=EventLayer.EXTERNAL_CONTROL,
                event_type=event_type,
            )
        return allowed


def _operation(value: Any) -> str | None:
    if isinstance(value, dict):
        operation = value.get("operation")
        if isinstance(operation, str) and operation in {"request_bid", "award"}:
            return operation
        for nested in value.values():
            found = _operation(nested)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _operation(nested)
            if found is not None:
                return found
    return None


class BidOnlyGateway(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, policy: BidOnlyPolicy) -> None:
        super().__init__(app)
        self.policy = policy

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method != "POST":
            return await call_next(request)
        try:
            caller = self.policy.verify(request.headers.get("authorization"))
            operation = _operation(json.loads(await request.body()))
            if operation is None:
                return JSONResponse({"error": "Unknown action"}, status_code=400)
            if not self.policy.authorize(caller, operation):
                return JSONResponse({"error": "Award not permitted"}, status_code=403)
        except (ValueError, jwt.PyJWTError):
            return JSONResponse({"error": "Invalid credential"}, status_code=401)
        return await call_next(request)
