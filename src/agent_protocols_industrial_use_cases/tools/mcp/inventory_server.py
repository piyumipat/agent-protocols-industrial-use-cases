"""Inventory MCP tool server for UC-003."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path

from agent_protocols.mcp import create_server, run_stdio
from mcp.server.mcpserver import MCPServer

from agent_protocols_industrial_use_cases.domain.models import InventoryItem
from agent_protocols_industrial_use_cases.tools.mcp.fixtures import load_inventory
from agent_protocols_industrial_use_cases.tools.mcp.payloads import InventoryPayload


def create_inventory_server(items: Mapping[str, InventoryItem]) -> MCPServer:
    server = create_server(
        "uc003-inventory",
        instructions="Locate parts in the deterministic UC-003 inventory fixture.",
    )

    @server.tool()
    async def locate_part(part_id: str) -> InventoryPayload:
        """Return availability and source zone for one part."""
        item = items.get(part_id)
        if item is None:
            raise ValueError(f"unknown part: {part_id}")
        return {
            "part_id": item.part_id,
            "available_quantity": item.available_quantity,
            "source_zone": item.source_zone,
        }

    return server


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    return parser


def main(arguments: Sequence[str] | None = None) -> None:
    options = _parser().parse_args(arguments)
    server = create_inventory_server(load_inventory(options.fixture))
    asyncio.run(run_stdio(server))


if __name__ == "__main__":
    main()
