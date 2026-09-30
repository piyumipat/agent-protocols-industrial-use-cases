"""Welding Cell Agora adapter implementing the shared inter-agent port."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack
from types import TracebackType
from typing import Any, Self

from agent_protocols.agora import (
    AgoraClient,
    ProtocolDocument,
    propose_protocol,
)

from agent_protocols_industrial_use_cases.domain.models import (
    Award,
    BidResponse,
    DeliveryResult,
    TransportRequest,
)
from agent_protocols_industrial_use_cases.domain.workflow import EventLayer, StepId, WorkflowEvents
from agent_protocols_industrial_use_cases.adapters.payloads import (
    decode_bid_response,
    decode_delivery,
    encode_award,
    encode_bid_request,
    serialized_payload_bytes,
)

from .payloads import decode_body
from .protocol import TRANSPORT_PROTOCOL

AgoraClientFactory = Callable[[str, str], AgoraClient]


class AgoraAdapterError(RuntimeError):
    pass


class AgoraAdapter:
    def __init__(
        self,
        endpoints: Mapping[str, str],
        *,
        events: WorkflowEvents,
        protocol: ProtocolDocument = TRANSPORT_PROTOCOL,
        negotiate: bool = False,
        require_https: bool = True,
        client_factory: AgoraClientFactory | None = None,
    ) -> None:
        self._endpoints = dict(endpoints)
        self._events = events
        self._protocol = protocol
        self._negotiate = negotiate
        self._require_https = require_https
        self._client_factory = client_factory or self._https_client_factory
        self._stack: AsyncExitStack | None = None
        self._clients: dict[str, AgoraClient] = {}
        self._conversations: dict[str, str] = {}

    def _https_client_factory(self, executor_id: str, base_url: str) -> AgoraClient:
        del executor_id
        return AgoraClient(base_url, require_https=self._require_https)

    async def __aenter__(self) -> Self:
        if self._stack is not None:
            raise RuntimeError("AgoraAdapter is already connected")
        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            for executor_id, endpoint in self._endpoints.items():
                self._events.started(
                    StepId.REQUEST_BIDS,
                    actor="welding-cell",
                    peer=executor_id,
                    layer=EventLayer.PROTOCOL_NATIVE,
                    event_type="agora.wellknown.fetching",
                    details={"endpoint_source": "configuration"},
                )
                client = self._client_factory(executor_id, endpoint)
                active_client = await stack.enter_async_context(client)
                discovered = await active_client.discover()
                self._events.completed(
                    StepId.REQUEST_BIDS,
                    actor="welding-cell",
                    peer=executor_id,
                    layer=EventLayer.PROTOCOL_NATIVE,
                    event_type="agora.wellknown.fetched",
                    details={"protocol_hashes": tuple(discovered)},
                )
                if self._protocol.hash in discovered:
                    self._events.completed(
                        StepId.REQUEST_BIDS,
                        actor="welding-cell",
                        peer=executor_id,
                        layer=EventLayer.PROTOCOL_NATIVE,
                        event_type="agora.protocol_document.shared",
                        details={"protocol_hash": self._protocol.hash},
                    )
                elif self._negotiate:
                    result = await propose_protocol(
                        active_client,
                        self._protocol,
                        purpose="UC-003 transport bidding and award",
                    )
                    if not result:
                        raise AgoraAdapterError(
                            f"Protocol Document proposal rejected: {result.reason}"
                        )
                    self._events.completed(
                        StepId.REQUEST_BIDS,
                        actor="welding-cell",
                        peer=executor_id,
                        layer=EventLayer.PAPER_BASED,
                        event_type="agora.protocol_document.negotiated",
                        details={"protocol_hash": result.protocol_hash},
                    )
                else:
                    raise AgoraAdapterError(
                        f"Executor {executor_id} does not advertise the UC-003 Protocol Document"
                    )
                self._clients[executor_id] = active_client
        except BaseException as error:
            self._events.failed(
                StepId.REQUEST_BIDS,
                actor="welding-cell",
                layer=EventLayer.PROTOCOL_NATIVE,
                event_type="agora.discovery.failed",
                details={"error": type(error).__name__},
            )
            await stack.aclose()
            self._clients.clear()
            raise
        self._stack = stack
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._stack is not None:
            await self._stack.__aexit__(exc_type, exc, tb)
            self._stack = None
        self._clients.clear()
        self._conversations.clear()

    async def request_bid(
        self, executor_id: str, request: TransportRequest
    ) -> BidResponse:
        self._events.started(
            StepId.REQUEST_BIDS,
            actor="welding-cell",
            peer=executor_id,
            layer=EventLayer.PROTOCOL_NATIVE,
            event_type="agora.bid_request.sent",
            details={"serialized_bytes": serialized_payload_bytes(encode_bid_request(request))},
        )
        try:
            request_payload = encode_bid_request(request)
            response = await self._client(executor_id).exchange(
                request_payload, protocol=self._protocol, multiround=True
            )
            body = _response_body(response)
            if response.conversation_id is None:
                raise AgoraAdapterError("Agora bid did not create a conversation")
            self._conversations[executor_id] = response.conversation_id
            bid = decode_bid_response(body)
        except Exception as error:
            self._events.failed(
                StepId.SUBMIT_AND_COLLECT_BIDS,
                actor="welding-cell",
                peer=executor_id,
                layer=EventLayer.PROTOCOL_NATIVE,
                event_type="agora.bid_response.failed",
                details={"error": type(error).__name__},
            )
            raise
        self._events.completed(
            StepId.SUBMIT_AND_COLLECT_BIDS,
            actor="welding-cell",
            peer=executor_id,
            layer=EventLayer.PROTOCOL_NATIVE,
            event_type="agora.bid_response.received",
            details={"serialized_bytes": serialized_payload_bytes(body)},
        )
        return bid

    async def award(self, executor_id: str, award: Award) -> DeliveryResult:
        self._events.started(
            StepId.AWARD_TASK,
            actor="welding-cell",
            peer=executor_id,
            layer=EventLayer.PROTOCOL_NATIVE,
            event_type="agora.award.sent",
            details={"serialized_bytes": serialized_payload_bytes(encode_award(award))},
        )
        try:
            award_payload = encode_award(award)
            conversation_id = self._conversations.get(executor_id)
            if conversation_id is None:
                response = await self._client(executor_id).exchange(
                    award_payload, protocol=self._protocol, multiround=True
                )
                if response.conversation_id is not None:
                    self._conversations[executor_id] = response.conversation_id
            else:
                response = await self._client(executor_id).continue_conversation(
                    conversation_id, award_payload
                )
            response_body = _response_body(response)
            result = decode_delivery(response_body)
        except Exception as error:
            self._events.failed(
                StepId.EXECUTE_DELIVERY,
                actor="welding-cell",
                peer=executor_id,
                layer=EventLayer.PROTOCOL_NATIVE,
                event_type="agora.delivery.failed",
                details={"error": type(error).__name__},
            )
            raise
        self._events.completed(
            StepId.EXECUTE_DELIVERY,
            actor="welding-cell",
            peer=executor_id,
            layer=EventLayer.PROTOCOL_NATIVE,
            event_type="agora.delivery.received",
            details={
                "conversation_id": self._conversations.get(executor_id),
                "serialized_bytes": serialized_payload_bytes(response_body),
            },
        )
        return result

    def _client(self, executor_id: str) -> AgoraClient:
        if self._stack is None:
            raise RuntimeError("Use AgoraAdapter as an async context manager")
        try:
            return self._clients[executor_id]
        except KeyError as error:
            raise AgoraAdapterError(f"no Agora endpoint for {executor_id}") from error


def _response_body(response: Any) -> dict[str, Any]:
    if response.status != "success":
        raise AgoraAdapterError(response.error or "Agora exchange failed")
    return decode_body(response.body)
