"""Events that mark the replay boundary and prove the transmitted replay."""

from __future__ import annotations

from agent_protocols_industrial_use_cases.domain.workflow import EventLayer, StepId, WorkflowEvents


def mark_replay_injected(
    events: WorkflowEvents,
    *,
    executor_id: str,
    boundary: str,
    method: str,
    url: str,
    original_request_sha256: str,
) -> None:
    events.completed(
        StepId.AWARD_TASK,
        actor="fault-injector",
        peer=executor_id,
        layer=EventLayer.EXTERNAL_CONTROL,
        event_type="fault.injected",
        details={
            "boundary": boundary,
            "method": method,
            "url": url,
            "original_request_sha256": original_request_sha256,
            "replay_count": 1,
        },
    )


def record_replay_completed(
    events: WorkflowEvents,
    *,
    executor_id: str,
    method: str,
    url: str,
    replay_request_sha256: str,
    replay_response_sha256: str,
    replay_http_status: int,
    request_matches_original: bool,
    credential_valid: bool | None,
) -> None:
    events.completed(
        StepId.AWARD_TASK,
        actor="fault-observer",
        peer=executor_id,
        layer=EventLayer.EXTERNAL_CONTROL,
        event_type="fault.replay.completed",
        details={
            "method": method,
            "url": url,
            "replay_request_sha256": replay_request_sha256,
            "replay_response_sha256": replay_response_sha256,
            "replay_http_status": replay_http_status,
            "request_matches_original": request_matches_original,
            "credential_valid": credential_valid,
        },
    )
