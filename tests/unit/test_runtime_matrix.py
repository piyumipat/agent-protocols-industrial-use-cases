from __future__ import annotations

from collections import Counter
from pathlib import Path

from agent_protocols_industrial_use_cases.runtime.matrix import build_schedule

ROOT = Path(__file__).parents[2]


def test_frozen_matrix_schedule_is_complete_unique_and_balanced() -> None:
    schedule = build_schedule(ROOT / "tests/fixtures/pilot/config/pilot_config.json")

    assert len(schedule) == 264
    assert len({run.run_id for run in schedule}) == 264
    assert Counter(run.run_kind for run in schedule) == {
        "warmup": 24,
        "measured": 240,
    }
    assert Counter(run.fleet_size for run in schedule) == {
        1: 66,
        3: 66,
        5: 66,
        10: 66,
    }
    for fleet_size in (1, 3, 5, 10):
        measured = [
            run
            for run in schedule
            if run.fleet_size == fleet_size and run.run_kind == "measured"
        ]
        positions = {
            (run.protocol, run.condition): Counter(
                index % 6
                for index, candidate in enumerate(measured)
                if (candidate.protocol, candidate.condition)
                == (run.protocol, run.condition)
            )
            for run in measured[:6]
        }
        assert all(max(counts.values()) - min(counts.values()) <= 1 for counts in positions.values())
