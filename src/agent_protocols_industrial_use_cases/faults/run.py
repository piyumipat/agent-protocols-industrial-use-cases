"""Run one supported fault manifest into its exclusive evidence bundle."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agent_protocols_industrial_use_cases.domain.workflow import WorkflowEvents
from agent_protocols_industrial_use_cases.faults.common.evidence import (
    FaultObservation,
    FaultRunBundle,
)
from agent_protocols_industrial_use_cases.faults.common.manifest import (
    FaultId,
    FaultManifest,
    FaultRound,
    load_fault_manifest,
    load_fault_run_plan,
)
from agent_protocols_industrial_use_cases.faults.pf01.a2a import run_a2a_substitution
from agent_protocols_industrial_use_cases.faults.pf01.agora import run_agora_substitution
from agent_protocols_industrial_use_cases.faults.pf01.anp import run_anp_substitution
from agent_protocols_industrial_use_cases.faults.pf02.a2a import run_a2a_replay
from agent_protocols_industrial_use_cases.faults.pf02.agora import run_agora_replay
from agent_protocols_industrial_use_cases.faults.pf02.anp import run_anp_replay
from agent_protocols_industrial_use_cases.faults.pf06.a2a import run_a2a_crash
from agent_protocols_industrial_use_cases.faults.pf06.agora import run_agora_crash
from agent_protocols_industrial_use_cases.faults.pf06.anp import run_anp_crash
from agent_protocols_industrial_use_cases.faults.pf08.a2a import run_a2a_bid_only
from agent_protocols_industrial_use_cases.faults.pf08.agora import run_agora_bid_only
from agent_protocols_industrial_use_cases.faults.pf08.anp import run_anp_bid_only
from agent_protocols_industrial_use_cases.runtime.config import ProtocolName

PF01_TIMEOUT_SECONDS = 20
PF02_TIMEOUT_SECONDS = 20
PF06_TIMEOUT_SECONDS = 20
PF08_TIMEOUT_SECONDS = 20


def generate_batch_id() -> str:
    """Return a lowercase, unique suffix shared by one batch's run IDs."""

    return f"{datetime.now(UTC):%Y%m%dt%H%M%S}-{uuid4().hex[:8]}"


async def run_fault(manifest: FaultManifest, faults_root: Path) -> tuple[Path, FaultObservation]:
    """Dispatch only scenarios with a real injector and validity checks."""

    supported = {
        (FaultId.PF01, ProtocolName.A2A),
        (FaultId.PF01, ProtocolName.ANP),
        (FaultId.PF01, ProtocolName.AGORA),
        (FaultId.PF02, ProtocolName.A2A),
        (FaultId.PF02, ProtocolName.ANP),
        (FaultId.PF02, ProtocolName.AGORA),
        (FaultId.PF06, ProtocolName.A2A),
        (FaultId.PF06, ProtocolName.ANP),
        (FaultId.PF06, ProtocolName.AGORA),
        (FaultId.PF08, ProtocolName.A2A),
        (FaultId.PF08, ProtocolName.ANP),
        (FaultId.PF08, ProtocolName.AGORA),
    }
    if (manifest.fault, manifest.protocol) not in supported:
        raise ValueError(
            f"fault runner is not implemented for {manifest.fault.value}/{manifest.protocol.value}"
        )
    if manifest.round is FaultRound.SAFEGUARD and manifest.fault is not FaultId.PF01:
        raise ValueError("safeguard runs are currently implemented only for PF-01")
    with FaultRunBundle(faults_root, manifest) as bundle:
        events = WorkflowEvents(
            run_id=manifest.run_id,
            task_id=manifest.task_id,
            protocol=manifest.protocol.value,
            sink=bundle.event_sink,
        )
        if manifest.fault is FaultId.PF01 and manifest.protocol is ProtocolName.A2A:
            async with asyncio.timeout(PF01_TIMEOUT_SECONDS):
                observation = await run_a2a_substitution(manifest, events)
        elif manifest.fault is FaultId.PF01 and manifest.protocol is ProtocolName.ANP:
            async with asyncio.timeout(PF01_TIMEOUT_SECONDS):
                observation = await run_anp_substitution(manifest, events)
        elif manifest.fault is FaultId.PF01:
            async with asyncio.timeout(PF01_TIMEOUT_SECONDS):
                observation = await run_agora_substitution(manifest, events)
        elif manifest.fault is FaultId.PF02 and manifest.protocol is ProtocolName.A2A:
            async with asyncio.timeout(PF02_TIMEOUT_SECONDS):
                observation = await run_a2a_replay(manifest, events)
        elif manifest.fault is FaultId.PF02 and manifest.protocol is ProtocolName.ANP:
            async with asyncio.timeout(PF02_TIMEOUT_SECONDS):
                observation = await run_anp_replay(manifest, events)
        elif manifest.fault is FaultId.PF02:
            async with asyncio.timeout(PF02_TIMEOUT_SECONDS):
                observation = await run_agora_replay(manifest, events)
        elif manifest.fault is FaultId.PF06 and manifest.protocol is ProtocolName.A2A:
            async with asyncio.timeout(PF06_TIMEOUT_SECONDS):
                observation = await run_a2a_crash(manifest, events, bundle.directory)
        elif manifest.fault is FaultId.PF06 and manifest.protocol is ProtocolName.ANP:
            async with asyncio.timeout(PF06_TIMEOUT_SECONDS):
                observation = await run_anp_crash(manifest, events, bundle.directory)
        elif manifest.fault is FaultId.PF06:
            async with asyncio.timeout(PF06_TIMEOUT_SECONDS):
                observation = await run_agora_crash(manifest, events, bundle.directory)
        elif manifest.protocol is ProtocolName.A2A:
            async with asyncio.timeout(PF08_TIMEOUT_SECONDS):
                observation = await run_a2a_bid_only(manifest, events)
        elif manifest.protocol is ProtocolName.ANP:
            async with asyncio.timeout(PF08_TIMEOUT_SECONDS):
                observation = await run_anp_bid_only(manifest, events)
        else:
            async with asyncio.timeout(PF08_TIMEOUT_SECONDS):
                observation = await run_agora_bid_only(manifest, events)
        validation = bundle.complete(observation)
    if not validation.valid:
        raise RuntimeError(f"run evidence is invalid: {validation.errors}")
    return bundle.directory, observation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="one run manifest or a repetition plan")
    parser.add_argument("--faults-root", type=Path, default=Path("results/faults/generated"))
    options = parser.parse_args()
    document = json.loads(options.input.read_text(encoding="utf-8"))
    if isinstance(document, dict) and document.get("kind") == "fault_run_plan":
        plan = load_fault_run_plan(options.input)
        failed = False
        for manifest in plan.expand(generate_batch_id()):
            try:
                directory, observation = asyncio.run(run_fault(manifest, options.faults_root))
            except Exception as error:  # noqa: BLE001 - preserve the failed run and continue the batch.
                failed = True
                print(f"{manifest.run_id}: invalid attempt ({type(error).__name__}: {error})")
            else:
                print(f"{directory}: {observation.caller_outcome}")
        if failed:
            raise SystemExit(1)
    else:
        directory, observation = asyncio.run(
            run_fault(load_fault_manifest(options.input), options.faults_root)
        )
        print(f"{directory}: {observation.caller_outcome}")


if __name__ == "__main__":
    main()
