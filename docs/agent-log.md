# Day 3 agent decision log

## 1. Accepted: V-6 outside POLICY_RULES

**Produced:** A `POLICY_RULES` table of functions that take `(notification, policy)` only: V-7, V-2, V-3, V-4, V-5. Duplicate detection uses `repository.find_matching` inside `submit_notification`.

**Decision:** Accept. Do not put a repository lookup in `POLICY_RULES`.

**Reason:** Contract section 4.1 requires evaluation order V-1, V-6, V-7, V-2, V-3, V-4, V-5. V-6 is WI-0151 (match on recorded `policy_number`, `loss_date`, and `claim_type`). `evaluate_notification` must take only a notification and a policy and perform no I/O (Day 3 C3). A `find_matching` call inside `POLICY_RULES` would mix deciding with persistence and would make `evaluate_notification` depend on the store.

## 2. Rejected: catching PolicyLookupFailed in submit_notification

**Produced:** A version of `submit_notification` that caught lookup failures (or a broad `except`) and returned a `RuleFailure` / 4xx-style outcome.

**Decision:** Reject. Catch only `PolicyNotFound`. Let `PolicyLookupFailed` propagate with `reason` intact (`timeout`, `unreachable`, `unparsable`).

**Reason:** Contract section 6: `POLICY_NOT_FOUND` is 422 (the master answered and the caller's number is wrong). `POLICY_MASTER_INVALID_RESPONSE` (502), `POLICY_MASTER_UNAVAILABLE` (503), and `POLICY_LOOKUP_TIMEOUT` (504) are system faults. Catching `PolicyLookupFailed` would hide those from Day 4 routes and would fail the tests that require the exception for all three reasons.

## Gate observation (Step 8)

On PR #1, a failing required GitHub Actions job `checks` (pytest) left Merge pull request greyed out. Merge was blocked until a revert restored a green `checks` job.
