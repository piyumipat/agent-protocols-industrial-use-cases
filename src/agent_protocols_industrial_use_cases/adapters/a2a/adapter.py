"""Welding Cell A2A adapter implementing the shared inter-agent port."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack
from types import TracebackType
from typing import Self

from agent_protocols.a2a import (
    A2AClient,
    Message,
    Role,
    StreamResponse,
    new_data_message,
)

from agent_protocols_industrial_use_cases.domain.models import (
    Award,
    BidResponse,
    DeliveryResult,
    TransportRequest,
)
from agent_protocols_industrial_use_cases.domain.workflow import EventLayer, StepId, WorkflowEvents

from .payloads import (
    decode_bid_response,
    decode_delivery,
    encode_award,
    encode_bid_request,
    message_payload,
    serialized_payload_bytes,
)

A2AClientFactory = Callable[[str, str], A2AClient]


class A2AAdapterError(RuntimeError):
    pass


def _default_client_factory(executor_id: str, endpoint: str) -> A2AClient:
    del executor_id
    return A2AClient(
        endpoint,
        streaming=True,
        accepted_output_modes=["application/json"],
    )


class A2AAdapter:
    def __init__(
        self,
        endpoints: Mapping[str, str],
        *,
        events: WorkflowEvents,
        client_factory: A2AClientFactory = _default_client_factory,
    ) -> None:
        self._endpoints = dict(endpoints)
        self._events = events
        self._client_factory = client_factory
        self._stack: AsyncExitStack | None = None
        self._clients: dict[str, A2AClient] = {}

    async def __aenter__(self) -> Self:
        if self._stack is not None:
            raise RuntimeError("A2AAdapter is already connected")
        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            for executor_id, endpoint in self._endpoints.items():
                client = self._client_factory(executor_id, endpoint)
                self._events.started(
                    StepId.REQUEST_BIDS,
                    actor="welding-cell",
                    peer=executor_id,
                    layer=EventLayer.PROTOCOL_NATIVE,
                    event_type="a2a.agent_card.fetching",
                    details={"endpoint_source": "configuration"},
                )
                self._clients[executor_id] = await stack.enter_async_context(client)
                self._events.completed(
                    StepId.REQUEST_BIDS,
                    actor="welding-cell",
                    peer=executor_id,
                    layer=EventLayer.PROTOCOL_NATIVE,
                    event_type="a2a.agent_card.fetched",
                )
        except BaseException:
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

    async def request_bid(
        self,
        executor_id: str,
        request: TransportRequest,
    ) -> BidResponse:
        client = self._client(executor_id)
        self._events.started(
            StepId.REQUEST_BIDS,
            actor="welding-cell",
            peer=executor_id,
            layer=EventLayer.PROTOCOL_NATIVE,
            event_type="a2a.bid_request.sent",
            details={"serialized_bytes": serialized_payload_bytes(encode_bid_request(request))},
        )
        try:
            message = await self._direct_response(
                client,
                new_data_message(encode_bid_request(request), role=Role.ROLE_USER),
            )
            response_payload = message_payload(message)
            response = decode_bid_response(response_payload)
        except Exception as error:
            self._events.failed(
                StepId.SUBMIT_AND_COLLECT_BIDS,
                actor="welding-cell",
                peer=executor_id,
                layer=EventLayer.PROTOCOL_NATIVE,
                event_type="a2a.bid_response.failed",
                details={"error": type(error).__name__},
            )
            raise
        self._events.completed(
            StepId.SUBMIT_AND_COLLECT_BIDS,
            actor="welding-cell",
            peer=executor_id,
            layer=EventLayer.PROTOCOL_NATIVE,
            event_type="a2a.bid_response.received",
            details={"serialized_bytes": serialized_payload_bytes(response_payload)},
        )
        return response

    async def award(self, executor_id: str, award: Award) -> DeliveryResult:
        client = self._client(executor_id)
        self._events.started(
            StepId.AWARD_TASK,
            actor="welding-cell",
            peer=executor_id,
            layer=EventLayer.PROTOCOL_NATIVE,
            event_type="a2a.award.sent",
            details={"serialized_bytes": serialized_payload_bytes(encode_award(award))},
        )
        delivery_message: Message | None = None
        task_id: str | None = None
        context_id: str | None = None
        try:
            async for response in client.send_message(
                new_data_message(encode_award(award), role=Role.ROLE_USER)
            ):
                response_task_id, response_context_id = _protocol_ids(response)
                task_id = response_task_id or task_id
                context_id = response_context_id or context_id
                candidate = _response_message(response)
                if candidate is not None:
                    delivery_message = candidate
            if delivery_message is None:
                raise A2AAdapterError("A2A award returned no delivery result")
            response_payload = message_payload(delivery_message)
            result = decode_delivery(response_payload)
        except Exception as error:
            self._events.failed(
                StepId.EXECUTE_DELIVERY,
                actor="welding-cell",
                peer=executor_id,
                layer=EventLayer.PROTOCOL_NATIVE,
                event_type="a2a.delivery.failed",
                details={"error": type(error).__name__},
            )
            raise
        self._events.completed(
            StepId.EXECUTE_DELIVERY,
            actor="welding-cell",
            peer=executor_id,
            layer=EventLayer.PROTOCOL_NATIVE,
            event_type="a2a.delivery.received",
            details={
                "a2a_task_id": task_id,
                "a2a_context_id": context_id,
                "serialized_bytes": serialized_payload_bytes(response_payload),
            },
        )
        return result

    def _client(self, executor_id: str) -> A2AClient:
        if self._stack is None:
            raise RuntimeError("Use A2AAdapter as an async context manager")
        try:
            return self._clients[executor_id]
        except KeyError as error:
            raise A2AAdapterError(f"no A2A endpoint for {executor_id}") from error

    @staticmethod
    async def _direct_response(client: A2AClient, message: Message) -> Message:
        async for response in client.send_message(message):
            if response.HasField("message"):
                return response.message
        raise A2AAdapterError("A2A request returned no direct Message")


def _response_message(response: StreamResponse) -> Message | None:
    if response.HasField("message"):
        return response.message
    if response.HasField("task") and response.task.status.HasField("message"):
        return response.task.status.message
    if response.HasField("status_update") and response.status_update.status.HasField("message"):
        return response.status_update.status.message
    return None


def _protocol_ids(response: StreamResponse) -> tuple[str | None, str | None]:
    if response.HasField("task"):
        return response.task.id, response.task.context_id
    if response.HasField("status_update"):
        return response.status_update.task_id, None
    if response.HasField("artifact_update"):
        return response.artifact_update.task_id, None
    return None, None
