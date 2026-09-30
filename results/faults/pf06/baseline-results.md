# PF-06 baseline results

**Valid runs:** 20 · **Validation:** 20/20

| Protocol / baseline type | Runs | Caller outcome and recovery check | Delivery count |
|---|---:|---|---:|
| A2A type 1: Task ID not received | 5 | `A2AClientError`; `GetTask` not attempted | 0/5 |
| A2A type 2: Task ID received | 5 | `A2AClientError`; `GetTask` returned `TaskNotFoundError` in all five | 0/5 |
| ANP | 5 | `ServerDisconnectedError`; no award-status method in this profile | 0/5 |
| Agora | 5 | `RemoteProtocolError`; no award-status method in this profile | 0/5 |

All 20 runs verified durable acceptance, a crash before delivery, and successful restart.
Task status remained unresolved; the harness did not retry the award.
The delivery audit file remained empty, but it has no writer; the crash before simulated delivery
is the basis for the zero-delivery result.

The A2A type 1, ANP, and Agora runs are in batch `20260925t100213-0be0543a`. The A2A type 2
repetitions use the [A2A Task-ID baseline plan](../../../inputs/faults/profiles/pf06/pf06-a2a-task-id-baseline-plan.json)
and batch `20260925t102944-ba09c2f0`. Each resolved manifest and ordered evidence is in its result
bundle.

**Use-case source fingerprints:** A2A type 2 `ab051523a0543bf6a479969925da3b64bf83d18ca32ce4aa597cd4e467566aed`;
A2A type 1, ANP, and Agora `d8e3fec66c0cf704fa2d7d0e8a3d2019c62ae23e1e26c820fffd41f8c43081b2`.
**Agora source capture SHA-256:** `bebe0985db954704aac2c5731c30c09ef3b10c19179a2f9be95b2debcb354f8e`.
