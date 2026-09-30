# PF-01 baseline results

**Batch:** `20260925t082833-02e00206` · **Valid runs:** 5 per protocol · **Validation:** 15/15

| Protocol | Identity verified | Verified identity approved for `executor-01`? | Rogue bid received | Deliveries |
|---|---|---:|---:|---:|
| A2A | Signed Agent Card | No | 5/5 | 0 |
| ANP | DID link | No | 5/5 | 0 |
| Agora | HTTPS certificate | No | 5/5 | 0 |

Every run accepted the valid credential presented by the rogue endpoint without checking it
against the plant-approved identity for `executor-01`. The Welding Cell received the rogue bid in
all 15 runs. No award or delivery was attempted.

The one-file input is [`pf01-baseline-plan.json`](../../../inputs/faults/profiles/pf01/pf01-baseline-plan.json). Each
resolved per-run manifest and its ordered evidence are in the corresponding result bundle.

**Use-case source:** Historical source revision `7fcdb4e98222d239df45fe18c43134179b302c33`; source-tree SHA-256
`b5b76e52436a162be7b1a12fc4392c30477d4e74c4b41e0319bd99996922a2c2`.
The plan was aligned with the preserved per-run manifests after the batch; those manifests are the
original run record.
**Agora source capture SHA-256:** `bebe0985db954704aac2c5731c30c09ef3b10c19179a2f9be95b2debcb354f8e`.
