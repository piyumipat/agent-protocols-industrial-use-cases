"""Valid bid-only A2A caller must be denied before award dispatch."""

from __future__ import annotations

from pathlib import Path

from agent_protocols_industrial_use_cases.faults.common.manifest import (
    INJECTION_BOUNDARY,
    FaultId,
    FaultManifest,
    FaultRound,
)
from agent_protocols_industrial_use_cases.faults.pf08.policy import BidOnlyPolicy
from agent_protocols_industrial_use_cases.faults.run import run_fault
from agent_protocols_industrial_use_cases.runtime.config import ProtocolName


async def test_a2a_records_bid_allow_and_award_deny(tmp_path: Path) -> None:
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
    _, observation = await run_fault(manifest, tmp_path)
    assert observation.details["verified_requests"] == 2
    assert observation.details["bid_policy_decision"] == "allowed"
    assert observation.details["award_policy_decision"] == "denied"
    assert observation.details["bid_allowed"] is True


async def test_a2a_allowed_award_is_recorded_as_valid_result(
    tmp_path: Path, monkeypatch
) -> None:
    original_init = BidOnlyPolicy.__init__

    def allow_award(self, events, *, caller: str = "observer-01") -> None:
        original_init(self, events, caller=caller)
        self.permissions[caller] = frozenset({"request_bid", "award"})

    monkeypatch.setattr(BidOnlyPolicy, "__init__", allow_award)
    manifest = FaultManifest(
        schema_version=1,
        run_id="pf08-a2a-baseline-allowed-award-r01-a01",
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
    _, observation = await run_fault(manifest, tmp_path)
    assert observation.details["bid_policy_decision"] == "allowed"
    assert observation.details["award_policy_decision"] == "allowed"
