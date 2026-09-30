"""Fault-run input and evidence invariants."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_protocols_industrial_use_cases.domain.workflow import EventLayer, StepId, WorkflowEvents
from agent_protocols_industrial_use_cases.faults.common.evidence import (
    FaultEvidenceError,
    FaultObservation,
    FaultRunBundle,
    validate_fault_bundle,
)
from agent_protocols_industrial_use_cases.faults.common.manifest import (
    INJECTION_BOUNDARY,
    FaultId,
    FaultManifest,
    FaultManifestError,
    FaultRound,
)
from agent_protocols_industrial_use_cases.faults.run import run_fault
from agent_protocols_industrial_use_cases.runtime.config import ProtocolName


def _manifest(*, controls: tuple[str, ...] | None = None) -> FaultManifest:
    return FaultManifest(
        schema_version=1,
        run_id="pf02-a2a-baseline-r01-a01",
        task_id="fault-task-001",
        fault=FaultId.PF02,
        protocol=ProtocolName.A2A,
        round=FaultRound.BASELINE,
        repetition=1,
        attempt=1,
        specification_version="A2A 1.0.1",
        reference_kit_version="0.2.0",
        reference_kit_revision="1222b3792c1d347d949b7184d5131a0551ff97bb",
        use_case_revision="draft",
        injection_boundary=INJECTION_BOUNDARY[FaultId.PF02],
        enabled_controls=controls or (
            "task_ledger", "correlated_event_recording", "message_idempotency",
        ),
    )

def test_a2a_replay_manifest_requires_selected_protocol_mechanism() -> None:
    with pytest.raises(FaultManifestError, match="messageId idempotency"):
        _manifest(controls=("task_ledger", "correlated_event_recording"))


async def test_complete_bundle_is_valid_and_cannot_be_overwritten(tmp_path: Path) -> None:
    manifest = _manifest()
    directory, _ = await run_fault(manifest, tmp_path)
    assert validate_fault_bundle(directory).valid
    assert (directory / "validation.json").exists()
    with (
        pytest.raises(FaultEvidenceError, match="cannot create run bundle"),
        FaultRunBundle(tmp_path, manifest),
    ):
        pass


def test_pf08_validates_allowed_award_even_if_executor_work_starts(tmp_path: Path) -> None:
    manifest = FaultManifest(
        schema_version=1,
        run_id="pf08-a2a-baseline-r01-a01",
        task_id="fault-task-001",
        fault=FaultId.PF08,
        protocol=ProtocolName.A2A,
        round=FaultRound.BASELINE,
        repetition=1,
        attempt=1,
        specification_version="A2A 1.0.1",
        reference_kit_version="0.2.0",
        reference_kit_revision="published-v0.2.0",
        use_case_revision="development",
        injection_boundary=INJECTION_BOUNDARY[FaultId.PF08],
        enabled_controls=("task_ledger", "correlated_event_recording"),
    )
    with FaultRunBundle(tmp_path, manifest) as bundle:
        events = WorkflowEvents(
            run_id=manifest.run_id,
            task_id=manifest.task_id,
            protocol=manifest.protocol.value,
            sink=bundle.event_sink,
        )
        events.completed(
            StepId.AWARD_TASK,
            actor="fault-injector",
            peer="observer-01",
            layer=EventLayer.EXTERNAL_CONTROL,
            event_type="fault.injected",
        )
        events.completed(
            StepId.SUBMIT_AND_COLLECT_BIDS,
            actor="executor-01",
            peer="observer-01",
            layer=EventLayer.EXTERNAL_CONTROL,
            event_type="policy.bid.allowed",
        )
        events.completed(
            StepId.AWARD_TASK,
            actor="executor-01",
            peer="observer-01",
            layer=EventLayer.EXTERNAL_CONTROL,
            event_type="policy.award.allowed",
        )
        result = bundle.complete(
            FaultObservation(
                injection_count=1,
                caller_outcome="bid_allowed;award_allowed",
                first_detection_layer=None,
                first_detection_event=None,
                business_dispatches=1,
                delivery_count=1,
                details={
                    "verified_caller": "observer-01",
                    "verified_requests": 2,
                    "bid_policy_decision": "allowed",
                    "award_policy_decision": "allowed",
                },
            )
        )
    assert result.valid, result.errors


async def test_different_replay_body_invalidates_run(tmp_path: Path) -> None:
    manifest = _manifest()
    directory, _ = await run_fault(manifest, tmp_path)
    observation_path = directory / "observation.json"
    observation = json.loads(observation_path.read_text(encoding="utf-8"))
    observation["details"]["replay_request_sha256"] = "0" * 64
    observation_path.write_text(json.dumps(observation), encoding="utf-8")
    result = validate_fault_bundle(directory)
    assert not result.valid
    assert "PF-02 replay must match the captured method, URL, headers, and body once" in result.errors
