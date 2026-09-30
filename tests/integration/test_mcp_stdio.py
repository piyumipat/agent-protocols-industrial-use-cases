from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp import StdioServerParameters

from agent_protocols_industrial_use_cases.tools.mcp.clients import (
    ExecutorStatusMCPClient,
    InventoryMCPClient,
    OutcomeMCPClient,
)
from tests.unit.test_mcp_ports import successful_outcome

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = PROJECT_ROOT / "inputs" / "main" / "fixtures"


def server_parameters(script_name: str, *arguments: str) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=[
            str(PROJECT_ROOT / "src" / "agent_protocols_industrial_use_cases" / "tools" / "mcp" / script_name),
            *arguments,
        ],
        cwd=PROJECT_ROOT,
        env={"PYTHONPATH": str(PROJECT_ROOT / "src")},
    )


async def test_all_services_over_real_stdio_subprocesses() -> None:
    inventory_target = server_parameters(
        "inventory_server.py",
        "--fixture",
        str(FIXTURES / "inventory.json"),
    )
    status_target = server_parameters(
        "status_server.py",
        "--fixture",
        str(FIXTURES / "executors.json"),
    )

    async with asyncio.timeout(10):
        async with InventoryMCPClient(inventory_target) as inventory:
            item = await inventory.locate_part("part-42")
    async with asyncio.timeout(10):
        async with ExecutorStatusMCPClient(status_target) as statuses:
            status = await statuses.get_status("executor-01")

    with TemporaryDirectory() as directory:
        output_path = Path(directory) / "outcome.json"
        outcome_target = server_parameters(
            "outcome_server.py",
            "--output",
            str(output_path),
        )
        async with asyncio.timeout(10):
            async with OutcomeMCPClient(outcome_target) as outcomes:
                await outcomes.record_outcome(successful_outcome())
        persisted = json.loads(output_path.read_text(encoding="utf-8"))

    assert item.source_zone == "zone-a"
    assert status.available
    assert persisted["task_id"] == "task-001"
