"""Record the simulated Executor registry before substituting its URL."""

from __future__ import annotations

from agent_protocols_industrial_use_cases.domain.workflow import EventLayer, StepId, WorkflowEvents
from agent_protocols_industrial_use_cases.faults.common.manifest import FaultManifest


def substitute_endpoint(
    manifest: FaultManifest,
    events: WorkflowEvents,
    *,
    genuine_url: str,
    rogue_url: str,
    approved_identity: str,
) -> dict[str, str]:
    executor_id = "executor-01"
    registry = {executor_id: genuine_url}
    events.completed(
        StepId.REQUEST_BIDS,
        actor="fleet-registry",
        peer=executor_id,
        layer=EventLayer.EXTERNAL_CONTROL,
        event_type="fleet.registry.initialized",
        details={
            "endpoint": registry[executor_id],
            "approved_identity": approved_identity,
        },
    )
    previous_url = registry[executor_id]
    registry[executor_id] = rogue_url
    events.completed(
        StepId.REQUEST_BIDS,
        actor="fault-injector",
        peer=executor_id,
        layer=EventLayer.EXTERNAL_CONTROL,
        event_type="fault.injected",
        details={
            "boundary": manifest.injection_boundary,
            "previous_endpoint": previous_url,
            "configured_endpoint": registry[executor_id],
        },
    )
    return registry
