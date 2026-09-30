"""JSON text envelope for UC-003 over ANP's typed string method."""

from __future__ import annotations

import json
from typing import Any

from agent_protocols_industrial_use_cases.adapters.payloads import PayloadError


class ANPPayloadError(PayloadError):
    pass


def encode_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def decode_text(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as error:
        raise ANPPayloadError("ANP payload must be valid JSON") from error
    if not isinstance(payload, dict):
        raise ANPPayloadError("ANP payload must contain a JSON object")
    return payload
