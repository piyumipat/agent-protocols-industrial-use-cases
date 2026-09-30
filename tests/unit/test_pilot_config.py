from __future__ import annotations

import json
from pathlib import Path

from agent_protocols_industrial_use_cases.runtime.config import load_manifest

ROOT = Path(__file__).parents[2]


def test_frozen_pilot_config_covers_all_six_cells() -> None:
    config = json.loads((ROOT / "tests/fixtures/pilot/config/pilot_config.json").read_text())

    assert config["status"] == "frozen"
    assert config["revision"] == 3
    assert config["fleet_sizes"] == [1, 3, 5, 10]
    assert config["warmup_runs"] == 1
    assert config["measured_runs"] == 10
    assert config["rounds_per_run"] == 5
    assert config["response_deadline_seconds"] == 5.0
    assert config["use_case_commit"] == "d6bd8d9a748fac84dd9f4a76b49a80e8875f76da"
    assert len(config["cells"]) == 6
    assert len(set(config["cells"])) == 6
    for relative_path in config["cells"]:
        manifest = load_manifest(ROOT / "tests/fixtures/pilot" / relative_path)
        assert manifest.response_deadline_seconds == config["response_deadline_seconds"]
        assert manifest.use_case_commit == config["use_case_commit"]
