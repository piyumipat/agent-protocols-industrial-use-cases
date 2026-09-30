from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from agent_protocols.anp import DIDWbaAuthHeader

from agent_protocols_industrial_use_cases.domain.workflow import InMemoryEventSink, WorkflowEvents
from agent_protocols_industrial_use_cases.adapters.a2a import A2AAdapter
from agent_protocols_industrial_use_cases.adapters.agora import AgoraAdapter
from agent_protocols_industrial_use_cases.adapters.anp import ANPAdapter
from agent_protocols_industrial_use_cases.runtime import (
    AdapterConfigurationError,
    FleetCondition,
    RunManifest,
    create_inter_agent_adapter,
    load_manifest,
)

MANIFESTS = Path(__file__).parents[2] / "tests" / "fixtures" / "pilot" / "manifests"


def _events(protocol: str) -> WorkflowEvents:
    return WorkflowEvents(
        run_id="runtime-adapter-test",
        task_id="task-runtime-adapter",
        protocol=protocol,
        sink=InMemoryEventSink(),
    )


def test_factory_builds_a2a_from_stable_manifest() -> None:
    manifest = load_manifest(MANIFESTS / "uc003-a2a-stable-pilot.json")

    adapter = create_inter_agent_adapter(manifest, events=_events("a2a"))

    assert isinstance(adapter, A2AAdapter)


def test_factory_builds_agora_proposal_adapter_for_dynamic_manifest() -> None:
    manifest = load_manifest(MANIFESTS / "uc003-agora-dynamic-pilot.json")

    adapter = create_inter_agent_adapter(manifest, events=_events("agora"))

    assert isinstance(adapter, AgoraAdapter)
    assert manifest.condition is FleetCondition.DYNAMIC


def test_factory_requires_runtime_credentials_for_anp() -> None:
    document = load_manifest(MANIFESTS / "uc003-a2a-stable-pilot.json").to_dict()
    document["protocol"] = "anp"
    document["endpoints"] = {
        executor_id: endpoint.replace("http://", "https://")
        for executor_id, endpoint in document["endpoints"].items()
    }
    manifest = RunManifest.from_dict(document)

    with pytest.raises(AdapterConfigurationError, match="authenticators"):
        create_inter_agent_adapter(manifest, events=_events("anp"))


def test_factory_builds_anp_with_complete_runtime_credentials() -> None:
    document = load_manifest(MANIFESTS / "uc003-a2a-stable-pilot.json").to_dict()
    document["protocol"] = "anp"
    document["endpoints"] = {
        executor_id: endpoint.replace("http://", "https://")
        for executor_id, endpoint in document["endpoints"].items()
    }
    manifest = RunManifest.from_dict(document)
    placeholder = cast(DIDWbaAuthHeader, object())

    adapter = create_inter_agent_adapter(
        manifest,
        events=_events("anp"),
        anp_authenticators={
            executor_id: placeholder for executor_id in manifest.executor_ids
        },
    )

    assert isinstance(adapter, ANPAdapter)
