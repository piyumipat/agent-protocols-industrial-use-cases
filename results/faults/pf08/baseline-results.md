# PF-08 baseline results

**Batch:** `20260925t115710-6b959078` · **Valid runs:** 5 per protocol · **Validation:** 15/15

| Protocol | Caller authentication | Bid permission and response | Award permission and response |
|---|---|---|---|
| A2A | Signed JWT for `observer-01` | Allowed; bid returned (5/5) | Denied; `A2AClientError` (5/5) |
| ANP | DID-WBA for `observer-01` | Allowed; bid returned (5/5) | Denied; `ANPCallError` (5/5) |
| Agora | External signed JWT for `observer-01` | Allowed; bid returned (5/5) | Denied; `HTTPStatusError` (5/5) |

All 15 attempts authenticated both requests and recorded both permission decisions. Every run
matched the expected behavior: the bid was allowed and the award denied. The tested user-side
permission policy behaved consistently across the three protocol adapters; this does not mean the
protocols themselves define the same authorization rule. No safeguard round is needed under the
frozen outcome rule. Dispatch and delivery counts were zero, as contextual evidence.

The one-file input is [`pf08-baseline-plan.json`](../../../inputs/faults/profiles/pf08/pf08-baseline-plan.json). Each
resolved per-run manifest and its ordered evidence are in the corresponding result bundle.

**Use-case source:** source-tree SHA-256
`8be92666409a1321d04b84a50cb04ef5d3db9f7adac7fc70987e734fd680c2d2`.
**Agora source capture SHA-256:** `bebe0985db954704aac2c5731c30c09ef3b10c19179a2f9be95b2debcb354f8e`.
