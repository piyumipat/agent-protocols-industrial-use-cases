"""A2A envelope handling around the shared UC-003 payload schema."""

from __future__ import annotations

from typing import Any

from agent_protocols.a2a import Message
from google.protobuf.json_format import MessageToDict

from agent_protocols_industrial_use_cases.adapters.payloads import (
    PayloadError,
    decode_award,
    decode_bid_request,
    decode_bid_response,
    decode_delivery,
    encode_award,
    encode_bid_request,
    encode_bid_response,
    encode_delivery,
    serialized_payload_bytes,
)

A2APayloadError = PayloadError

__all__ = [
    "A2APayloadError",
    "decode_award",
    "decode_bid_request",
    "decode_bid_response",
    "decode_delivery",
    "encode_award",
    "encode_bid_request",
    "encode_bid_response",
    "encode_delivery",
    "message_payload",
    "serialized_payload_bytes",
]


def message_payload(message: Message) -> dict[str, Any]:
    data_parts = [part for part in message.parts if part.WhichOneof("content") == "data"]
    if len(data_parts) != 1:
        raise A2APayloadError("A2A Message must contain exactly one structured data Part")
    payload = MessageToDict(data_parts[0].data, preserving_proto_field_name=True)
    if not isinstance(payload, dict):
        raise A2APayloadError("A2A data Part must contain an object")
    return payload
