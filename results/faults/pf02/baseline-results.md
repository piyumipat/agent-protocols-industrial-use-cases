# PF-02 baseline results

**Batch:** `20260925t083748-da4ba39a` · **Valid runs:** 5 per protocol · **Validation:** 15/15

| Protocol | Replay response | First detection | Business dispatches | Deliveries |
|---|---:|---|---:|---:|
| A2A | HTTP 200 | Message ID cache (inferred) | 1/1 | 1/1 |
| ANP | HTTP 200 | Task ledger | 2/2 | 1/1 |
| Agora | HTTP 200 | Task ledger | 2/2 | 1/1 |

All repetitions replayed the original award request and retained exactly one delivery. For A2A,
the cache returned the original response without a second business dispatch; this is inferred from
the response and dispatch count, not an explicit rejection event. For ANP and Agora, the replay
reached business logic and the task ledger prevented a second delivery.

The one-file input is [`pf02-baseline-plan.json`](../../../inputs/faults/profiles/pf02/pf02-baseline-plan.json). Each
resolved manifest and its ordered evidence are in the corresponding result bundle.

**Use-case source:** source-tree SHA-256
`9f4a4db6797e37d27318974ed53a9b4b15a46c85b21a06c5d4d3c297360a5bf7`.
**Agora source capture SHA-256:** `bebe0985db954704aac2c5731c30c09ef3b10c19179a2f9be95b2debcb354f8e`.
