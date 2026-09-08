# Claims Intake Service: API Contract

Version 0.4. Owned by the claims intake team. Consumed by the claims portal team.

This document is the authority on what the service accepts, what it returns, and under what conditions it refuses. Where the code and this document disagree, the document is correct and the code is a defect.

Sections 1 through 3 are fixed. Do not edit them.

## 1. Purpose and scope

The claims intake service accepts a first notice of loss from the claims portal, validates it against the policy master and a table of business rules, and either records a notification and issues a claim reference or refuses the submission with a specific reason.

**In scope.** Accepting a notification, validating it, and recording it. Issuing a claim reference. Reporting the reason a notification was refused.

**Out of scope.** Adjusting, reserving, payment, and any decision about coverage beyond the rules in section 4. The service decides whether a notification is well formed and admissible. It does not decide whether the claim will be paid.

**The policy master is a dependency, not part of this service.** The service reads policy records from it and does not write to it. A policy that cannot be read is a condition this contract specifies, and it is specified separately from a policy that does not exist, because the two require different action from the caller.

**Compatibility.** Adding a field to a response is a compatible change and callers must ignore fields they do not recognize. Adding a new error code is a compatible change and callers must fall through to default handling for a code they do not recognize. Changing the meaning of an existing code, removing a field, or changing a status code for an existing condition is not compatible and does not happen without a version increment agreed with the portal team.

## 2. Request



### 2.1 Endpoint

```
POST /notifications
Content-Type: application/json
```



### 2.2 Body


| Field              | Type    | Required | Notes                                                         |
| ------------------ | ------- | -------- | ------------------------------------------------------------- |
| `policy_number`    | string  | yes      | Identifier as held in the policy master. Not empty.           |
| `loss_date`        | string  | yes      | Calendar date, `YYYY-MM-DD`.                                  |
| `claim_type`       | string  | yes      | One of the values in 2.3. Not empty.                          |
| `estimated_amount` | decimal | yes      | United States dollars, two decimal places. Greater than zero. |
| `description`      | string  | no       | Free text. Absent and `null` are equivalent.                  |


The service rejects a body carrying a field not listed above. A misspelled field name is a defect in the caller's code, and accepting the payload with the field ignored would record a notification built from data the caller did not send.

### 2.3 Claim type vocabulary

`collision`, `theft`, `glass`, `liability`, `weather`.

Which of these are admissible on a given notification depends on the product the policy is written on. The vocabulary is fixed by this contract. The permitted subset is a property of the policy record and is evaluated by rule `V-5`.

### 2.4 Well formed against acceptable

A request that cannot be interpreted is refused with status `400`. This means the body was not valid JSON, a required field was absent, a field carried a value of the wrong type, or a field was present that this contract does not define. The caller's code is wrong.

A request that was interpreted and whose content is not admissible is refused with status `422`. The caller's data is wrong, and a person needs to see the reason.

This split is stated here once and holds without exception everywhere else in this document.

## 3. Success response

A notification that passes every rule in section 4 is recorded and the service responds:

```
201 Created
Content-Type: application/json

{
  "claim_reference": "CLM-2026-000317",
  "status": "recorded"
}
```

`claim_reference` matches the pattern `CLM-YYYY-NNNNNN`, where `YYYY` is the calendar year in which the notification was recorded and `NNNNNN` is a zero padded sequence. A claim reference is unique across all recorded notifications and is never reissued. It is the value the claims handler quotes and the value every downstream system keys on.

`status` is `recorded` on every success response this contract defines. It exists because the portal displays it and because a future state that is not `recorded` is foreseeable. Callers must not treat it as constant.

A refused notification is never recorded and no claim reference is issued. There is no partial outcome: either a notification exists with a reference, or nothing was written.

## 4. Validation



### 4.1 Evaluation order

Rules are evaluated in this sequence: `V-1`, `V-6`, `V-7`, `V-2`, `V-3`, `V-4`, `V-5`. Evaluation stops at the first rule that fails, that rule's code is returned, and no later rule is evaluated.

Identifiers do not imply order. An identifier records when a rule was converted, not its precedence. This sequence is the only statement of evaluation order in this contract, and a rule added later takes its place on the merits of its condition rather than by its number.

Each rule is placed ahead of any rule that would give a misleading answer if it ran first. `V-1` is first: every other rule compares the notification against a field on a policy, and there is no policy to compare against when the number is not found (WI-0142, AC-4). `V-6` is next: when the loss is already recorded, the handler needs the claim reference that exists, not a verdict on a second submission (OP-4). `V-7` runs before `V-3` because a cancelled policy keeps its original `expiry_date`, so a loss after cancellation often falls after expiry too, and `LOSS_AFTER_EXPIRY` would send the handler to the wrong system to investigate (WI-0158, AC-4).

More than one rule can fail and the caller is told one reason: the code of the first rule to fail, with no list or count of the others. A caller who corrects that fault and resubmits may be refused again with a different code.

### 4.2 Rule table


| ID  | Condition                                                                            | Code                     | Status |
| --- | ------------------------------------------------------------------------------------ | ------------------------ | ------ |
| V-1 | `policy_number` exists in the policy master                                          | `POLICY_NOT_FOUND`       | 422    |
| V-2 | `loss_date` >= policy `effective_date`                                               | `LOSS_BEFORE_INCEPTION`  | 422    |
| V-3 | `loss_date` <= policy `expiry_date`                                                  | `LOSS_AFTER_EXPIRY`      | 422    |
| V-4 | `estimated_amount` <= policy `limit`                                                 | `AMOUNT_EXCEEDS_LIMIT`   | 422    |
| V-5 | `claim_type` permitted on the policy's product                                       | `TYPE_NOT_COVERED`       | 422    |
| V-6 | No recorded notification has the same `policy_number`, `loss_date`, and `claim_type` | `DUPLICATE_NOTIFICATION` | 409    |
| V-7 | policy `cancellation_date` is null, or `loss_date` < policy `cancellation_date`      | `POLICY_CANCELLED`       | 422    |


Boundaries are inclusive as written. A loss on the inception date is covered (WI-0142, AC-3). An amount equal to the limit is within cover. `V-7` is the one exception: a loss on the cancellation date is not covered, because cancellation takes effect at the start of that day (WI-0158, AC-2).

`V-6` matches on `policy_number`, `loss_date`, and `claim_type` only, and against recorded notifications only. `estimated_amount` and `description` are not compared, and a submission that was refused was never recorded, so there is nothing for a later one to duplicate (WI-0151, AC-3).

### 4.3 Interpretation of request values

- `policy_number` must match the policy master exactly, including case. A number that differs only in case is not found and fails `V-1`. An empty `policy_number` is refused under section 2.4 with `MALFORMED_REQUEST`.
- `claim_type` must be one of the five values in section 2.3. Any other value is refused under section 2.4 with `MALFORMED_REQUEST`. An empty `claim_type` is refused the same way. `V-5` only checks whether a valid type is permitted on the policy's product.
- `estimated_amount` must have no more than two decimal places. A value with more precision is refused under section 2.4 with `MALFORMED_REQUEST`. It is never rounded. Zero and negative amounts are also refused under section 2.4 with `MALFORMED_REQUEST`.

### 4.4 Request validation

Shape checks in section 2.4 and 4.3 run before any rule in 4.1. They are not business rules. They use one identifier:

| ID  | When it fails                                              | Code                 | Status |
| --- | ---------------------------------------------------------- | -------------------- | ------ |
| R-1 | The request is not well formed (section 2.4 and 4.3)       | `MALFORMED_REQUEST`  | 400    |

A payload refused by `R-1` is never evaluated against `V-1` through `V-7`.

## 5. Error envelope

Every refusal returns the same shape, whatever caused it:

```json
{
  "error": {
    "code": "LOSS_BEFORE_INCEPTION",
    "message": "The loss date is earlier than the policy effective date.",
    "detail": {}
  }
}
```

`code` is stable and is the only part a caller may branch on. Every value it can take is listed in section 6. The meaning of a code never changes. A new code may be added, and a caller that does not recognize one falls through to its default handling.

`message` is not stable. It is written for a person, may be reworded in any release, and must be displayed rather than parsed. Logic written against message text will break on a change that alters nothing else.

`detail` is always present and always an object. The table below is the full guarantee for each code. Every listed field is present. A field not listed for a code is not present unless section 1 later adds it. Types below are what the caller parses.

| Code | Field | Type | What it holds |
| --- | --- | --- | --- |
| `MALFORMED_REQUEST` | `problems` | array of objects | One object per field fault. Each object has `field` (string: the key in the body, or the unknown key) and `problem` (string: why that key failed). |
| `POLICY_NOT_FOUND` | `policy_number` | string | The number from the request. |
| `DUPLICATE_NOTIFICATION` | `claim_reference` | string | The `CLM-YYYY-NNNNNN` of the record that already exists (WI-0151, AC-2). |
| `POLICY_CANCELLED` | `policy_number` | string | The number from the request. |
| `POLICY_CANCELLED` | `loss_date` | string | The loss date from the request, `YYYY-MM-DD`. |
| `POLICY_CANCELLED` | `cancellation_date` | string | The policy cancellation date, `YYYY-MM-DD`. |
| `LOSS_BEFORE_INCEPTION` | `policy_number` | string | The number from the request. |
| `LOSS_BEFORE_INCEPTION` | `loss_date` | string | The loss date from the request, `YYYY-MM-DD`. |
| `LOSS_BEFORE_INCEPTION` | `effective_date` | string | The policy effective date, `YYYY-MM-DD`. |
| `LOSS_AFTER_EXPIRY` | `policy_number` | string | The number from the request. |
| `LOSS_AFTER_EXPIRY` | `loss_date` | string | The loss date from the request, `YYYY-MM-DD`. |
| `LOSS_AFTER_EXPIRY` | `expiry_date` | string | The policy expiry date, `YYYY-MM-DD`. |
| `AMOUNT_EXCEEDS_LIMIT` | `policy_number` | string | The number from the request. |
| `AMOUNT_EXCEEDS_LIMIT` | `estimated_amount` | string | The amount from the request, two decimal places. |
| `AMOUNT_EXCEEDS_LIMIT` | `limit` | string | The policy limit, two decimal places. |
| `TYPE_NOT_COVERED` | `policy_number` | string | The number from the request. |
| `TYPE_NOT_COVERED` | `claim_type` | string | The type from the request. |
| `TYPE_NOT_COVERED` | `permitted_claim_types` | array of strings | The types the policy product allows. |
| `INTERNAL_ERROR` | *(none)* | | `detail` is `{}`. |
| `POLICY_MASTER_INVALID_RESPONSE` | `dependency` | string | Always `policy_master`. |
| `POLICY_MASTER_INVALID_RESPONSE` | `retryable` | boolean | Whether a retry of the same request is worth attempting. |
| `POLICY_MASTER_UNAVAILABLE` | `dependency` | string | Always `policy_master`. |
| `POLICY_MASTER_UNAVAILABLE` | `retryable` | boolean | Whether a retry of the same request is worth attempting. |
| `POLICY_LOOKUP_TIMEOUT` | `dependency` | string | Always `policy_master`. |
| `POLICY_LOOKUP_TIMEOUT` | `retryable` | boolean | Whether a retry of the same request is worth attempting. |

The JSON examples in 5.1 through 5.3 illustrate this table. They are not a second specification.

### 5.1 A rule failure

```json
{
  "error": {
    "code": "LOSS_BEFORE_INCEPTION",
    "message": "The loss date is earlier than the policy effective date.",
    "detail": {
      "policy_number": "MOT-4479",
      "loss_date": "2026-02-20",
      "effective_date": "2026-03-15"
    }
  }
}
```

### 5.2 A request that could not be interpreted

```json
{
  "error": {
    "code": "MALFORMED_REQUEST",
    "message": "The request could not be interpreted.",
    "detail": {
      "problems": [
        { "field": "estimated_amount", "problem": "required field absent" },
        { "field": "clam_type", "problem": "field not defined by this contract" }
      ]
    }
  }
}
```

### 5.3 A policy master that did not answer

```json
{
  "error": {
    "code": "POLICY_LOOKUP_TIMEOUT",
    "message": "The policy master did not respond in time.",
    "detail": {
      "dependency": "policy_master",
      "retryable": true
    }
  }
}
```

The three `detail` objects differ because their causes differ. A rule failure compares one value from the request against one from the policy, so it carries both and the policy they came from. An uninterpretable request never reached a policy, so it carries field faults instead, and it is the only case that reports more than one fault at once: section 4.1 governs rule evaluation, and a request refused under section 2.4 is never evaluated against rules. A dependency failure carries no request data at all, because nothing in the request was wrong; it names the dependency and whether a retry is worth attempting.



## 6. Status code mapping

Every failure this service can produce appears below. Each code maps to exactly one status, and no condition has two codes.

| Code | Status | Condition |
| --- | --- | --- |
| `MALFORMED_REQUEST` | 400 | `R-1`. The body was not valid JSON, a required field was absent, a field carried the wrong type, a field this contract does not define was present (section 2.4), a `claim_type` was outside the section 2.3 vocabulary, an `estimated_amount` had more than two decimal places (section 4.3), `policy_number` or `claim_type` was empty, or `estimated_amount` was not greater than zero. |
| `DUPLICATE_NOTIFICATION` | 409 | `V-6`. The loss event is already recorded. |
| `POLICY_NOT_FOUND` | 422 | `V-1`. The policy master answered and holds no policy with that number. |
| `POLICY_CANCELLED` | 422 | `V-7`. |
| `LOSS_BEFORE_INCEPTION` | 422 | `V-2`. |
| `LOSS_AFTER_EXPIRY` | 422 | `V-3`. |
| `AMOUNT_EXCEEDS_LIMIT` | 422 | `V-4`. |
| `TYPE_NOT_COVERED` | 422 | `V-5`. |
| `INTERNAL_ERROR` | 500 | The service failed for a reason not covered above. |
| `POLICY_MASTER_INVALID_RESPONSE` | 502 | The policy master answered with something this service could not parse. |
| `POLICY_MASTER_UNAVAILABLE` | 503 | The policy master could not be reached. |
| `POLICY_LOOKUP_TIMEOUT` | 504 | The policy lookup did not complete within the time allowed. |

The policy master produces four conditions and they are not one failure. A master that answers and reports no match is a fact about the caller's data, so it is `POLICY_NOT_FOUND` at 422 and a person must correct the policy number. The other three are facts about the system: the request was valid, nothing about it needs changing, and the same request may succeed later. They are kept apart rather than collapsed into one code because the reason is what the operator has to act on.

A 4xx response must not be retried unchanged, because the submission will be refused again. A 5xx response may be retried unchanged. A caller that cannot tell the two apart either retries a refusal forever or discards a notification that would have succeeded.

Responses generated before a request reaches this service, such as a 404 for an unknown path or a 405 for a method this contract does not define, do not carry an error code and are not part of this mapping.