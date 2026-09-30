from __future__ import annotations

from agent_protocols_industrial_use_cases.faults.common.manifest import FaultRunPlan
from agent_protocols_industrial_use_cases.faults.run import generate_batch_id
from agent_protocols_industrial_use_cases.runtime.config import ProtocolName


def test_pf01_plan_expands_five_runs_per_protocol() -> None:
    plan = FaultRunPlan.from_dict(
        {
            "kind": "fault_run_plan",
            "schema_version": 1,
            "task_id": "fault-task-001",
            "fault": "pf01",
            "round": "baseline",
            "protocols": ["a2a", "anp", "agora"],
            "repetitions": 5,
            "attempt": 1,
            "reference_kit_version": "0.2.0",
            "reference_kit_revision": "1222b3792c1d347d949b7184d5131a0551ff97bb",
            "use_case_revision": "source-tree-sha256:abc123",
            "injection_boundary": "configured_endpoint_before_discovery",
            "enabled_controls": {
                "a2a": ["task_ledger", "correlated_event_recording", "signed_agent_cards"],
                "anp": ["task_ledger", "correlated_event_recording"],
                "agora": ["task_ledger", "correlated_event_recording"],
            },
            "parameters": {"agora": {"protocol_source_sha256": "digest"}},
        }
    )

    manifests = plan.expand("batch01")

    assert len(manifests) == 15
    assert [manifest.run_id for manifest in manifests[:5]] == [
        f"pf01-a2a-baseline-batch01-r{repetition:02d}"
        for repetition in range(1, 6)
    ]
    assert [manifest.repetition for manifest in manifests[:5]] == [1, 2, 3, 4, 5]
    assert all(manifest.attempt == 1 for manifest in manifests)
    assert [manifest.protocol for manifest in manifests[5:10]] == [
        ProtocolName.ANP
    ] * 5
    assert [manifest.protocol for manifest in manifests[10:]] == [
        ProtocolName.AGORA
    ] * 5
    assert manifests[-1].parameters == {"protocol_source_sha256": "digest"}
    assert plan.expand("batch02")[0].run_id != manifests[0].run_id


def test_run_plan_adds_attempt_suffix_only_for_retries() -> None:
    document = {
        "kind": "fault_run_plan",
        "schema_version": 1,
        "task_id": "fault-task-001",
        "fault": "pf01",
        "round": "baseline",
        "protocols": ["a2a"],
        "repetitions": 1,
        "attempt": 2,
        "reference_kit_version": "0.2.0",
        "reference_kit_revision": "1222b3792c1d347d949b7184d5131a0551ff97bb",
        "use_case_revision": "source-tree-sha256:abc123",
        "injection_boundary": "configured_endpoint_before_discovery",
        "enabled_controls": {
            "a2a": ["task_ledger", "correlated_event_recording", "signed_agent_cards"]
        },
        "parameters": {},
    }

    manifest = FaultRunPlan.from_dict(document).expand("batch01")[0]

    assert manifest.run_id == "pf01-a2a-baseline-batch01-r01-a02"
    assert manifest.attempt == 2


def test_generated_batch_id_is_lowercase_and_filename_safe() -> None:
    batch_id = generate_batch_id()

    assert batch_id == batch_id.lower()
    assert all(character.isalnum() or character in "-_" for character in batch_id)
