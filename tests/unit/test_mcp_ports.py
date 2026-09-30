from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from agent_protocols_industrial_use_cases.domain.models import (
    Bid,
    CoordinationOutcome,
    CoordinationStatus,
    DeliveryResult,
    DeliveryStatus,
    ExecutorStatus,
    InventoryItem,
)
from agent_protocols_industrial_use_cases.domain.workflow import InMemoryEventSink, WorkflowEvents
from agent_protocols_industrial_use_cases.tools.mcp.clients import (
    ExecutorStatusMCPClient,
    InventoryMCPClient,
    MCPToolError,
    OutcomeMCPClient,
)
from agent_protocols_industrial_use_cases.tools.mcp.inventory_server import create_inventory_server
from agent_protocols_industrial_use_cases.tools.mcp.outcome_server import create_outcome_server
from agent_protocols_industrial_use_cases.tools.mcp.status_server import create_status_server


def successful_outcome() -> CoordinationOutcome:
    timestamp = datetime(2026, 9, 18, 10, 0, tzinfo=UTC)
    return CoordinationOutcome(
        task_id="task-001",
        status=CoordinationStatus.COMPLETED,
        winner_id="executor-01",
        bids=(Bid("task-001", "executor-01", 8, 4),),
        no_bids=(),
        timed_out_executor_ids=(),
        delivery=DeliveryResult(
            task_id="task-001",
            executor_id="executor-01",
            status=DeliveryStatus.COMPLETED,
            started_at=timestamp,
            completed_at=timestamp,
        ),
    )


class MCPPortTests(unittest.IsolatedAsyncioTestCase):
    async def test_inventory_client_round_trip_and_tool_error(self) -> None:
        server = create_inventory_server(
            {"part-42": InventoryItem("part-42", 10, "zone-a")}
        )
        sink = InMemoryEventSink()
        events = WorkflowEvents(
            run_id="run-001",
            task_id="task-001",
            protocol="test",
            sink=sink,
        )

        async with InventoryMCPClient(server, events=events) as client:
            item = await client.locate_part("part-42")
            self.assertEqual(item, InventoryItem("part-42", 10, "zone-a"))
            with self.assertRaises(MCPToolError):
                await client.locate_part("unknown")

        self.assertIn("mcp.inventory.returned", [event.event_type for event in sink.events])
        self.assertIn("mcp.inventory.failed", [event.event_type for event in sink.events])

    async def test_executor_status_client_round_trip(self) -> None:
        expected = ExecutorStatus("executor-01", True, "aisle-1", 82)
        server = create_status_server({expected.executor_id: expected})

        async with ExecutorStatusMCPClient(server) as client:
            status = await client.get_status("executor-01")

        self.assertEqual(status, expected)

    async def test_outcome_client_persists_structured_result(self) -> None:
        with TemporaryDirectory() as directory:
            output_path = Path(directory) / "outcome.json"
            server = create_outcome_server(output_path)
            outcome = successful_outcome()

            async with OutcomeMCPClient(server) as client:
                await client.record_outcome(outcome)

            persisted = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(persisted["task_id"], "task-001")
        self.assertEqual(persisted["winner_id"], "executor-01")
        self.assertEqual(persisted["delivery"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
