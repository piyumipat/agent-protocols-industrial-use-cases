"""ANP Executor Agent publication and authenticated RPC handling for UC-003."""

from __future__ import annotations

import re
from collections.abc import Callable
from urllib.parse import SplitResult, urlsplit

from agent_protocols.anp import (
    DidWbaVerifier,
    E1Identity,
    TextMethod,
    create_agent_description,
    create_anp_server,
    create_discovery_collection,
    create_e1_identity,
    create_openrpc_interface,
)
from aiohttp import web

from agent_protocols_industrial_use_cases.domain.executor import ExecutorService
from agent_protocols_industrial_use_cases.domain.workflow import EventLayer, StepId
from agent_protocols_industrial_use_cases.adapters.payloads import (
    decode_award,
    decode_bid_request,
    encode_bid_response,
    encode_delivery,
)

from .payloads import decode_text, encode_text

TRANSPORT_METHOD = TextMethod(
    "coordinate_transport",
    "payload",
    "result",
    "Request a transport bid or award transport work",
)


def create_executor_identity(executor_id: str, origin: str) -> E1Identity:
    parts = _origin_parts(origin)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", executor_id):
        raise ValueError("executor_id must be safe for an ANP publication path")
    description_url = f"{origin}/agents/{executor_id}/description.json"
    return create_e1_identity(
        str(parts.hostname),
        port=parts.port,
        path_segments=["agents", executor_id],
        agent_description_url=description_url,
    )


def create_executor_app(
    service: ExecutorService,
    *,
    origin: str,
    verifier: DidWbaVerifier,
    authorize_did: Callable[[str], bool],
    identity: E1Identity | None = None,
) -> web.Application:
    """Publish one discoverable Executor with one authenticated UC-003 method."""

    parts = _origin_parts(origin)
    domain = parts.netloc
    executor_id = service.executor_id
    selected_identity = identity or create_executor_identity(executor_id, origin)
    description_url = f"{origin}/agents/{executor_id}/description.json"
    interface_url = f"{origin}/agents/{executor_id}/openrpc.json"
    rpc_url = f"{origin}/agents/{executor_id}/rpc"
    interface = create_openrpc_interface(
        rpc_url=rpc_url,
        title=f"{executor_id} UC-003 transport API",
        method=TRANSPORT_METHOD,
    )
    description = create_agent_description(
        url=description_url,
        name=executor_id,
        did=selected_identity.did,
        description="UC-003 transport Executor Agent.",
        interfaces=[
            {
                "type": "StructuredInterface",
                "protocol": "openrpc",
                "url": interface_url,
            }
        ],
    )
    discovery = create_discovery_collection(domain, [description])

    async def handle_transport(value: str, caller_did: str) -> str:
        payload = decode_text(value)
        operation = payload.get("operation")
        if operation not in {"request_bid", "award"}:
            raise ValueError(f"unsupported UC-003 operation: {operation!r}")
        step = (
            StepId.SUBMIT_AND_COLLECT_BIDS
            if operation == "request_bid"
            else StepId.AWARD_TASK
        )
        service.events.completed(
            step,
            actor=executor_id,
            peer=caller_did,
            layer=EventLayer.PROTOCOL_NATIVE,
            event_type="anp.caller.authenticated",
            details={"caller_did": caller_did},
        )
        service.events.completed(
            step,
            actor=executor_id,
            peer=caller_did,
            layer=EventLayer.EXTERNAL_CONTROL,
            event_type="anp.caller.authorized",
            details={"caller_did": caller_did},
        )
        if operation == "request_bid":
            response = await service.assess_and_bid(decode_bid_request(payload))
            return encode_text(encode_bid_response(response))
        result = await service.accept_award(decode_award(payload))
        return encode_text(encode_delivery(result))

    return create_anp_server(
        agent_description=description,
        discovery_collection=discovery,
        did_documents={selected_identity.did: selected_identity.document},
        openrpc_interface=interface,
        verifier=verifier,
        authorize_did=authorize_did,
        text_method=TRANSPORT_METHOD,
        handle_text=handle_transport,
    )


def _origin_parts(origin: str) -> SplitResult:
    parts = urlsplit(origin)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.path
        or parts.query
        or parts.fragment
        or parts.username
        or parts.password
    ):
        raise ValueError("ANP origin must be an HTTPS origin without a path")
    return parts
