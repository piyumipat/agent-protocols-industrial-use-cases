"""Run-scoped outcome MCP tool server for UC-003."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from agent_protocols.mcp import create_server, run_stdio
from mcp.server.mcpserver import MCPServer

from agent_protocols_industrial_use_cases.tools.mcp.payloads import (
    CoordinationOutcomePayload,
    OutcomeAcknowledgement,
)


def create_outcome_server(output_path: Path) -> MCPServer:
    server = create_server(
        "uc003-outcome",
        instructions="Persist the terminal outcome for one UC-003 experiment run.",
    )

    @server.tool()
    async def record_outcome(
        outcome: CoordinationOutcomePayload,
    ) -> OutcomeAcknowledgement:
        """Atomically persist one terminal coordination outcome."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
        temporary_path.write_text(
            json.dumps(outcome, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(output_path)
        return {"recorded": True, "task_id": outcome["task_id"]}

    return server


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(arguments: Sequence[str] | None = None) -> None:
    options = _parser().parse_args(arguments)
    server = create_outcome_server(options.output)
    asyncio.run(run_stdio(server))


if __name__ == "__main__":
    main()
