# PF-01 safeguard results

**Batch:** `20260925t123430-bccd8001` · **Valid attempts:** 5 per protocol · **Successful safeguard outcomes:** 15/15

| Protocol | Identity verified | Verified identity approved for `executor-01`? | Rejected before bid | Rogue bids received | Dispatches | Deliveries |
|---|---|---:|---:|---:|---:|---:|
| A2A | Signed Agent Card | No | 5/5 | 0/5 | 0 | 0 |
| ANP | DID link | No | 5/5 | 0/5 | 0 | 0 |
| Agora | HTTPS certificate | No | 5/5 | 0/5 | 0 | 0 |

All 15 attempts met the validity conditions: the rogue endpoint was substituted and its distinct
identity verified. The fleet-admission control rejected each endpoint before sending a bid request,
so all 15 safeguard outcomes succeeded. No Executor business dispatch or delivery occurred.

The one-file input is [`pf01-safeguard-plan.json`](../../../inputs/faults/profiles/pf01/pf01-safeguard-plan.json). Each
resolved per-run manifest and its ordered evidence are in the corresponding result bundle.

**Use-case source:** Historical source revision `a80a384`; source-tree SHA-256
`4f7842b80e3dc4336a751a57ac961337fa98c08dfe83cc80689566398224c990`.
**Agora source capture SHA-256:** `bebe0985db954704aac2c5731c30c09ef3b10c19179a2f9be95b2debcb354f8e`.
