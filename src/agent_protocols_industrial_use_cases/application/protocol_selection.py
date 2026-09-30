"""Construct UC-003 inter-agent adapters from validated run manifests."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from urllib.parse import urlsplit

from agent_protocols.anp import ANPClient, DIDWbaAuthHeader

from agent_protocols_industrial_use_cases.application.coordinator import InterAgentPort
from agent_protocols_industrial_use_cases.domain.workflow import WorkflowEvents
from agent_protocols_industrial_use_cases.adapters.a2a import A2AAdapter
from agent_protocols_industrial_use_cases.adapters.agora import AgoraAdapter
from agent_protocols_industrial_use_cases.adapters.anp import ANPAdapter

from agent_protocols_industrial_use_cases.runtime.config import ProtocolMode, ProtocolName, RunManifest

type InterAgentAdapter = A2AAdapter | ANPAdapter | AgoraAdapter


class AdapterConfigurationError(ValueError):
    pass


def create_inter_agent_adapter(
    manifest: RunManifest,
    *,
    events: WorkflowEvents,
    anp_authenticators: Mapping[str, DIDWbaAuthHeader] | None = None,
    anp_client_factory: Callable[[str, DIDWbaAuthHeader], ANPClient] | None = None,
) -> InterAgentAdapter:
    """Create the protocol adapter selected by ``manifest``.

    ANP signing credentials are deliberately injected by the runtime and never stored in a
    manifest. A returned adapter is an async context manager and can be passed directly to the
    coordinator as its ``InterAgentPort``.
    """

    if manifest.protocol is ProtocolName.A2A:
        return A2AAdapter(manifest.endpoints, events=events)

    if manifest.protocol is ProtocolName.ANP:
        if anp_authenticators is None:
            raise AdapterConfigurationError(
                "ANP manifests require runtime DID-WBA authenticators"
            )
        if set(anp_authenticators) != set(manifest.executor_ids):
            raise AdapterConfigurationError(
                "ANP authenticators must cover every manifest Executor"
            )
        return ANPAdapter(
            {executor_id: _anp_domain(endpoint) for executor_id, endpoint in manifest.endpoints.items()},
            authenticators=anp_authenticators,
            events=events,
            client_factory=anp_client_factory or _default_anp_client_factory,
        )

    if manifest.protocol is ProtocolName.AGORA:
        return AgoraAdapter(
            manifest.endpoints,
            events=events,
            negotiate=manifest.protocol_mode is ProtocolMode.PROPOSAL,
            require_https=all(
                endpoint.startswith("https://") for endpoint in manifest.endpoints.values()
            ),
        )

    raise AdapterConfigurationError(f"unsupported protocol: {manifest.protocol}")


def _default_anp_client_factory(
    executor_id: str, authenticator: DIDWbaAuthHeader
) -> ANPClient:
    del executor_id
    return ANPClient(authenticator)


def _anp_domain(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if not parsed.hostname:
        raise AdapterConfigurationError(f"ANP endpoint has no hostname: {endpoint}")
    return parsed.netloc


def ensure_inter_agent_port(adapter: InterAgentAdapter) -> InterAgentPort:
    """Expose the common coordinator port while retaining the concrete adapter type."""

    return adapter
