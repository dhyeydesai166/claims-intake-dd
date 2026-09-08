# Payload Triage

Every payload in `data/fnol_edge.json` classified against `docs/api-contract.md` as you have completed it. The classification records what the contract says the service does, which is not always what the payload obviously violates.

Fill one row per payload. Where a payload is accepted, leave the rule, code, and status columns as `-`.

## Classification

| Payload | Outcome | Rule | Code | Status |
| --- | --- | --- | --- | --- |
| EDGE-01 | Accepted | - | - | - |
| EDGE-02 | Accepted | - | - | - |
| EDGE-03 | Accepted | - | - | - |
| EDGE-04 | Rejected | V-7 | `POLICY_CANCELLED` | 422 |
| EDGE-05 | Rejected | V-2 | `LOSS_BEFORE_INCEPTION` | 422 |
| EDGE-06 | Rejected | V-4 | `AMOUNT_EXCEEDS_LIMIT` | 422 |
| EDGE-07 | Rejected | V-1 | `POLICY_NOT_FOUND` | 422 |
| EDGE-08 | Rejected | R-1 | `MALFORMED_REQUEST` | 400 |
| EDGE-09 | Rejected | V-5 | `TYPE_NOT_COVERED` | 422 |
| EDGE-10 | Rejected | V-7 | `POLICY_CANCELLED` | 422 |
| EDGE-11 | Rejected | R-1 | `MALFORMED_REQUEST` | 400 |
| EDGE-12 | Rejected | R-1 | `MALFORMED_REQUEST` | 400 |

## Decision log

Three payloads cannot be classified against the contract as it shipped, because the contract left a decision unmade. For each one, record the ambiguity, the decision, its authority, and the alternative you rejected.

A decision recorded here and nowhere else has not been made. Amend `docs/api-contract.md` so that a reader of the contract alone could not arrive at the other reading.

### Decision 1

**Payload.** EDGE-07.

**The ambiguity.** Section 2.2 says `policy_number` is the identifier "as held in the policy master". It does not say whether case matters. EDGE-07 sends `mot-4471` and the master holds `MOT-4471`, so the contract allowed two answers: not found, or found after correcting the case.

**Decision.** The number must match exactly, including case. EDGE-07 fails `V-1` and returns `POLICY_NOT_FOUND` at 422.

**Authority.** Section 2.2, "as held in the policy master", and section 1, which says the policy master is a dependency this service reads and does not own.

**Rejected alternative.** Convert the number to upper case before looking it up. This is wrong, not merely looser. The service would search for a value the caller never sent, and the numbering belongs to the master rather than to this service. If the master ever held two numbers differing only in case, the service would return the wrong policy and record a claim against it. The looser form is also permanent, because tightening it later would break callers.

**Contract amended.** Section 4.3.

### Decision 2

**Payload.** EDGE-11.

**The ambiguity.** EDGE-11 sends `claim_type` of `flood`, which is not one of the five values in section 2.3. Section 2.2 treats the vocabulary as part of the request shape, which points to 400. Section 2.3 gives claim type checking to `V-5`, which points to 422. Both readings were available.

**Decision.** A `claim_type` outside the section 2.3 vocabulary is refused under section 2.4 with `MALFORMED_REQUEST` at 400. `V-5` is not evaluated.

**Authority.** Section 2.3 says the vocabulary is fixed by this contract and that `V-5` evaluates the permitted subset. `V-5` is about the subset, not about which words are claim types.

**Rejected alternative.** Return `TYPE_NOT_COVERED` at 422. That tells the handler the product does not cover flood, which is untrue, because the service does not recognise flood as a claim type at all. It also sends the wrong person to fix it. A 422 asks a claims handler to correct the client's data, but the fault is in the portal's code, which section 2.4 assigns to 400.

**Contract amended.** Section 4.3.

### Decision 3

**Payload.** EDGE-12.

**The ambiguity.** EDGE-12 sends `estimated_amount` of `3499.999`. Section 2.2 says two decimal places but does not say what happens when more arrive. The amount is inside the policy limit either way, so the contract allowed refusing it or rounding it.

**Decision.** An `estimated_amount` with more than two decimal places is refused under section 2.4 with `MALFORMED_REQUEST` at 400. It is never rounded.

**Authority.** Section 2.2 fixes two decimal places. The same section refuses fields it does not define because accepting them "would record a notification built from data the caller did not send".

**Rejected alternative.** Round to `3500.00` and carry on. The recorded amount would then differ from the amount submitted, and section 3 makes the recorded notification the value every downstream system keys on. A silent change to a money figure is invisible to the caller and cannot be reconciled later.

**Contract amended.** Section 4.3.

## Day 2 reconciliation

Compared what `NotificationRequest` refuses in `tests/unit/test_models.py` with section 6.

Already in section 6 as `MALFORMED_REQUEST` / 400: missing field, wrong type, unknown field, `claim_type` not in the vocabulary, too many decimal places.

Missing from section 6, but refused by the model: empty `policy_number`, empty `claim_type`, amount of zero, amount below zero. These are still `MALFORMED_REQUEST` / 400 (section 2.4). No new code.

Amended section 4.3 and the `MALFORMED_REQUEST` row in section 6.