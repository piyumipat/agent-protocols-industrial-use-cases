"""Executor-status MCP tool server for UC-003."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path

from agent_protocols.mcp import create_server, run_stdio
from mcp.server.mcpserver import MCPServer

from agent_protocols_industrial_use_cases.domain.models import ExecutorStatus
from agent_protocols_industrial_use_cases.tools.mcp.fixtures import load_executor_statuses
from agent_protocols_industrial_use_cases.tools.mcp.payloads import ExecutorStatusPayload


def create_status_server(statuses: Mapping[str, ExecutorStatus]) -> MCPServer:
    server = create_server(
        "uc003-executor-status",
        instructions="Return deterministic UC-003 Executor status snapshots.",
    )

    @server.tool()
    async def get_status(executor_id: str) -> ExecutorStatusPayload:
        """Return availability, position, and energy for one Executor."""
        status = statuses.get(executor_id)
        if status is None:
            raise ValueError(f"unknown Executor: {executor_id}")
        return {
            "executor_id": status.executor_id,
            "available": status.available,
            "position": status.position,
            "energy_level": status.energy_level,
        }

    return server


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    return parser


def main(arguments: Sequence[str] | None = None) -> None:
    options = _parser().parse_args(arguments)
    server = create_status_server(load_executor_statuses(options.fixture))
    asyncio.run(run_stdio(server))


if __name__ == "__main__":
    main()
