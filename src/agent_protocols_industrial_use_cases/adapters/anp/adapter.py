"""Welding Cell ANP adapter implementing the shared inter-agent port."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack
from types import TracebackType
from typing import Any, Self

from agent_protocols.anp import ANPClient, DIDWbaAuthHeader, VerifiedAgent

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

from .payloads import decode_text, encode_text
from .server import TRANSPORT_METHOD

ANPClientFactory = Callable[[str, DIDWbaAuthHeader], ANPClient]
ANPAgentAdmission = Callable[[str, VerifiedAgent], None]


class ANPAdapterError(RuntimeError):
    pass


def _default_client_factory(
    executor_id: str, authenticator: DIDWbaAuthHeader
) -> ANPClient:
    del executor_id
    return ANPClient(authenticator)


class ANPAdapter:
    def __init__(
        self,
        domains: Mapping[str, str],
        *,
        authenticators: Mapping[str, DIDWbaAuthHeader],
        events: WorkflowEvents,
        client_factory: ANPClientFactory = _default_client_factory,
        agent_admission: ANPAgentAdmission | None = None,
    ) -> None:
        if set(domains) != set(authenticators):
            raise ValueError("each ANP domain requires one caller authenticator")
        self._domains = dict(domains)
        self._authenticators = dict(authenticators)
        self._events = events
        self._client_factory = client_factory
        self._agent_admission = agent_admission
        self._stack: AsyncExitStack | None = None
        self._clients: dict[str, ANPClient] = {}
        self._agents: dict[str, VerifiedAgent] = {}

    async def __aenter__(self) -> Self:
        if self._stack is not None:
            raise RuntimeError("ANPAdapter is already connected")
        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            for executor_id, domain in self._domains.items():
                self._events.started(
                    StepId.REQUEST_BIDS,
                    actor="welding-cell",
                    peer=executor_id,
                    layer=EventLayer.PROTOCOL_NATIVE,
                    event_type="anp.discovery.started",
                    details={"endpoint_source": "configuration", "domain": domain},
                )
                client = self._client_factory(
                    executor_id, self._authenticators[executor_id]
                )
                active_client = await stack.enter_async_context(client)
                agents = await active_client.discover(domain)
                agent = _select_executor(executor_id, agents)
                self._clients[executor_id] = active_client
                self._agents[executor_id] = agent
                self._events.completed(
                    StepId.REQUEST_BIDS,
                    actor="welding-cell",
                    peer=executor_id,
                    layer=EventLayer.PROTOCOL_NATIVE,
                    event_type="anp.discovery.completed",
                    details={"description_url": agent.description_url},
                )
                self._events.completed(
                    StepId.REQUEST_BIDS,
                    actor="welding-cell",
                    peer=executor_id,
                    layer=EventLayer.PROTOCOL_NATIVE,
                    event_type="anp.did.verified",
                    details={"executor_did": agent.did},
                )
                if self._agent_admission is not None:
                    self._agent_admission(executor_id, agent)
        except BaseException as error:
            self._events.failed(
                StepId.REQUEST_BIDS,
                actor="welding-cell",
                layer=EventLayer.PROTOCOL_NATIVE,
                event_type="anp.discovery.failed",
                details={"error": type(error).__name__},
            )
            await stack.aclose()
            self._clients.clear()
            self._agents.clear()
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
        self._agents.clear()

    async def request_bid(
        self, executor_id: str, request: TransportRequest
    ) -> BidResponse:
        self._events.started(
            StepId.REQUEST_BIDS,
            actor="welding-cell",
            peer=executor_id,
            layer=EventLayer.PROTOCOL_NATIVE,
            event_type="anp.bid_request.sent",
            details={"serialized_bytes": serialized_payload_bytes(encode_bid_request(request))},
        )
        try:
            response = await self._call(executor_id, encode_bid_request(request))
            bid = decode_bid_response(response)
        except Exception as error:
            self._events.failed(
                StepId.SUBMIT_AND_COLLECT_BIDS,
                actor="welding-cell",
                peer=executor_id,
                layer=EventLayer.PROTOCOL_NATIVE,
                event_type="anp.bid_response.failed",
                details={"error": type(error).__name__},
            )
            raise
        self._events.completed(
            StepId.SUBMIT_AND_COLLECT_BIDS,
            actor="welding-cell",
            peer=executor_id,
            layer=EventLayer.PROTOCOL_NATIVE,
            event_type="anp.bid_response.received",
            details={"serialized_bytes": serialized_payload_bytes(response)},
        )
        return bid

    async def award(self, executor_id: str, award: Award) -> DeliveryResult:
        self._events.started(
            StepId.AWARD_TASK,
            actor="welding-cell",
            peer=executor_id,
            layer=EventLayer.PROTOCOL_NATIVE,
            event_type="anp.award.sent",
            details={"serialized_bytes": serialized_payload_bytes(encode_award(award))},
        )
        try:
            response = await self._call(executor_id, encode_award(award))
            result = decode_delivery(response)
        except Exception as error:
            self._events.failed(
                StepId.EXECUTE_DELIVERY,
                actor="welding-cell",
                peer=executor_id,
                layer=EventLayer.PROTOCOL_NATIVE,
                event_type="anp.delivery.failed",
                details={"error": type(error).__name__},
            )
            raise
        self._events.completed(
            StepId.EXECUTE_DELIVERY,
            actor="welding-cell",
            peer=executor_id,
            layer=EventLayer.PROTOCOL_NATIVE,
            event_type="anp.delivery.received",
            details={
                "executor_did": self._agent(executor_id).did,
                "serialized_bytes": serialized_payload_bytes(response),
            },
        )
        return result

    async def _call(self, executor_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        client = self._client(executor_id)
        result = await client.call_text(
            self._agent(executor_id), TRANSPORT_METHOD, encode_text(payload)
        )
        return decode_text(result)

    def _client(self, executor_id: str) -> ANPClient:
        if self._stack is None:
            raise RuntimeError("Use ANPAdapter as an async context manager")
        try:
            return self._clients[executor_id]
        except KeyError as error:
            raise ANPAdapterError(f"no ANP domain for {executor_id}") from error

    def _agent(self, executor_id: str) -> VerifiedAgent:
        try:
            return self._agents[executor_id]
        except KeyError as error:
            raise ANPAdapterError(f"no discovered ANP agent for {executor_id}") from error


def _select_executor(executor_id: str, agents: list[VerifiedAgent]) -> VerifiedAgent:
    matches = [agent for agent in agents if agent.description.get("name") == executor_id]
    if len(matches) != 1:
        raise ANPAdapterError(
            f"expected one discovered ANP agent named {executor_id}, got {len(matches)}"
        )
    return matches[0]
