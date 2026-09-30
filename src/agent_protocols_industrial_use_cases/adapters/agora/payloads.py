"""Validation of Agora JSON bodies for UC-003."""

from __future__ import annotations

from typing import Any

from agent_protocols_industrial_use_cases.adapters.payloads import PayloadError


class AgoraPayloadError(PayloadError):
    pass


def decode_body(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AgoraPayloadError("Agora UC-003 body must be a JSON object")
    return value
