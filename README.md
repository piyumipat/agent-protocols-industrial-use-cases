# Agent Protocols Industrial Use Cases

This repository publishes the implementation and experiment materials for the intralogistics transport allocation use case reported in **Agent Communication Protocols for Industrial Ecosystems**. It compares A2A, ANP, and Agora for one shared agent workflow, with MCP as its common tool layer. It also documents selected protocol fault experiments.

The code uses the public [Agent Protocols Reference Kit](https://github.com/piyumipat/agent-protocols-reference-kit) as a dependency. This repository documents the experiment implementation and observed behavior; consult the Reference Kit and the [official protocol specifications](#protocol-documentation) for protocol behavior beyond this implementation.

## Experiment documentation

- [Main coordination experiment](docs/main-experiment.md): workflow, design, measurements, results, and limits.
- [Fault experiment](docs/fault-experiment.md): fault profiles, safeguards, observations, and limits.

Frozen inputs are under [`inputs/`](inputs/). Curated results from the reported runs are under [`results/`](results/). Raw run-level evidence is not included.

## Repository structure

```text
src/agent_protocols_industrial_use_cases/
├── domain/       workflow entities, rules, and task ledger
├── application/  coordination workflow and protocol selection
├── adapters/     A2A, ANP, and Agora implementations
├── tools/mcp/    shared inventory, status, and outcome tools
├── runtime/      configuration, process control, and experiment runner
├── faults/       protocol fault harnesses
├── evidence/     run evidence writing and validation
└── analysis/     measurements and CSV summaries
tests/            unit, integration, contract, and fault tests
inputs/           frozen experiment configurations, fixtures, and fault profiles
results/          published measurements and fault summaries
docs/             main coordination and fault experiment reports
```

The main measured-round dataset is in `results/main/`; fault summaries are grouped under
`results/faults/`. New run artifacts should go to the ignored `results/generated/` and
`results/faults/generated/` paths described below.

## Development

The project requires Python 3.13 and [uv](https://docs.astral.sh/uv/). Run the following commands
from the repository root.

### Set up the environment

```sh
uv sync --locked
```

This creates the project environment from `uv.lock` and installs the project and development
dependencies.

### Run the test suite

```sh
uv run --locked pytest
```

The suite includes unit, integration, contract, and fault tests. Integration tests start local
processes and use loopback sockets.

### Re-run the main coordination matrix

The published configuration schedules 264 isolated runs: 24 warm-ups and 240 measured runs. The
measured runs contain 1,200 coordination rounds. This is a full experiment and takes longer than
the tests. It writes run evidence, a schedule, progress information, and CSV summaries to the paths
below; it leaves the curated results in `results/main/` unchanged. Before rerunning, update each
manifest's `use_case_commit` and `lockfile_sha256` to identify the current checkout and `uv.lock`;
the checked-in values record the historical experiment. See the [main experiment](docs/main-experiment.md)
for the provenance details.

```sh
uv run --locked python -m agent_protocols_industrial_use_cases.runtime.matrix \
  --config inputs/main/config/replication-20260925-config.json \
  --results-root results/generated/main/runs \
  --summary-root results/generated/main/summaries
```

Generated files under `results/generated/` are ignored by Git. If a matrix run is interrupted, run
the same command again with the same output paths to resume validated work. Choose new output paths
for a fresh run.

### Re-run the fault experiment

The fault plans are in [`inputs/faults/profiles/`](inputs/faults/profiles/). They cover baseline
runs for PF-01, PF-02, PF-06, and PF-08, plus the PF-01 identity-admission safeguard and a second
PF-06 A2A variant. Each plan writes per-run evidence beneath `results/faults/generated/`.
Before rerunning, update each plan's `use_case_revision` to identify the checkout being run; the
checked-in value records historical provenance. See [Re-running the fault profiles](docs/fault-experiment.md#re-running-the-fault-profiles)
for the commands and [fault experiment](docs/fault-experiment.md) for validity rules and result
interpretation.

### Explore the published materials

- Frozen experiment configuration and fixtures: [`inputs/main/`](inputs/main/)
- Fault profiles and source snapshots: [`inputs/faults/`](inputs/faults/)
- Published measured-round data and summaries: [`results/main/`](results/main/)
- Retained per-fault outcome summaries: [`results/faults/`](results/faults/)

New runs go to the generated-output paths above; they do not replace the published result files.

## Protocol documentation

- [Agent Protocols Reference Kit](https://github.com/piyumipat/agent-protocols-reference-kit)
- [A2A specification](https://a2a-protocol.org/latest/specification/)
- [ANP specifications](https://github.com/agent-network-protocol/AgentNetworkProtocol)
- [Agora Working Standard](https://agoraprotocol.org/docs/protocol/specification)
- [Model Context Protocol](https://modelcontextprotocol.io/specification/)

## License and citation

Original project material is released under the [MIT License](LICENSE). Third-party specifications, dependencies, and assets retain their own terms. Citation metadata is provided in [`CITATION.cff`](CITATION.cff).
