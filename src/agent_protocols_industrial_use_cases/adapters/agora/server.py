"""Agora Executor Agent server assembly for UC-003."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Protocol

from agent_protocols.agora import (
    ProtocolDocument,
    ProtocolProposalDecision,
    ProtocolRegistry,
    create_server,
    handle_protocol_proposal,
)
from starlette.applications import Starlette

from agent_protocols_industrial_use_cases.domain.executor import ExecutorService
from agent_protocols_industrial_use_cases.domain.workflow import EventLayer, StepId
from agent_protocols_industrial_use_cases.adapters.payloads import (
    decode_award,
    decode_bid_request,
    encode_bid_response,
    encode_delivery,
)

from .payloads import decode_body


class AgoraProposalPolicy(Protocol):
    def __call__(
        self,
        document: ProtocolDocument,
        purpose: str,
    ) -> bool | ProtocolProposalDecision | Awaitable[bool | ProtocolProposalDecision]: ...


def create_executor_app(
    service: ExecutorService,
    *,
    protocol: ProtocolDocument,
    pre_shared: bool = True,
    allow_proposals: bool = False,
    proposal_policy: AgoraProposalPolicy | None = None,
) -> Starlette:
    """Build an Agora server with optional paper-based PD proposal handling."""

    if allow_proposals and proposal_policy is None:
        raise ValueError("proposal_policy is required when proposals are enabled")
    transport_protocol = protocol
    registry = (
        ProtocolRegistry((transport_protocol,)) if pre_shared else ProtocolRegistry()
    )

    async def handle(
        body: str | dict[str, object],
        protocol: ProtocolDocument | None,
        conversation_id: str | None,
    ) -> str | dict[str, object]:
        del conversation_id
        if allow_proposals and proposal_policy is not None:
            proposal_result = await handle_protocol_proposal(
                body, registry, proposal_policy
            )
            if proposal_result is not None:
                service.events.completed(
                    StepId.REQUEST_BIDS,
                    actor=service.executor_id,
                    peer="welding-cell",
                    layer=EventLayer.PAPER_BASED,
                    event_type="agora.protocol_document.proposed",
                    details=proposal_result,
                )
                return proposal_result
        if protocol is None or protocol.hash != transport_protocol.hash:
            raise ValueError("UC-003 Protocol Document is required")
        payload = decode_body(body)
        operation = payload.get("operation")
        if operation == "request_bid":
            service.events.completed(
                StepId.SUBMIT_AND_COLLECT_BIDS,
                actor=service.executor_id,
                peer="welding-cell",
                layer=EventLayer.PROTOCOL_NATIVE,
                event_type="agora.protocol_document.accepted",
                details={"protocol_hash": transport_protocol.hash},
            )
            response = await service.assess_and_bid(decode_bid_request(payload))
            return encode_bid_response(response)
        if operation == "award":
            result = await service.accept_award(decode_award(payload))
            return encode_delivery(result)
        raise ValueError(f"unsupported UC-003 operation: {operation!r}")

    return create_server(handle, protocols=registry, base_path="/agora")
