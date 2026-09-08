"""Rule evaluation and notification submission.

This module owns the decision. It does not know it was reached over HTTP, which
is why it can be tested by calling a function with a typed object and asserting on
the result with no server running. It does not know where notifications are
stored either. It knows the rules.

`evaluate_policy_exists` ships written. It is the pattern every other rule
follows: take the notification and whatever it needs, decide, and return a
`ValidationOutcome` that names the rule and carries the values the decision was
made on. Nothing prints, nothing raises for an ordinary refusal, and nothing
reaches for a status code, because a status code is a fact about HTTP and this
module does not know about HTTP.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from claims.models import (
    AcceptedNotification,
    ErrorCode,
    NotificationRequest,
    Policy,
    RuleFailure,
    RuleId,
)
from claims.policy_client import PolicyClient, PolicyNotFound, PolicyRecord
from claims.repository import NotificationRepository


@dataclass(frozen=True)
class ValidationOutcome:
    accepted: bool
    claim_reference: str | None = None
    failure: RuleFailure | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def _policy_from_record(record: PolicyRecord) -> Policy:
    return Policy(
        policy_number=record.policy_number,
        product=record.product,
        effective_date=record.effective_date,
        expiry_date=record.expiry_date,
        cancellation_date=record.cancellation_date,
        limit=record.limit,
        permitted_claim_types=tuple(record.permitted_claim_types),  # type: ignore[arg-type]
    )


def evaluate_policy_exists(
    notification: NotificationRequest,
    policy_client: PolicyClient,
) -> ValidationOutcome:
    """V-1. The policy must exist in the policy master.

    This rule is different from the others in one way that matters: it is the only
    one that reaches outside the service, so it is the only one that can fail for
    a reason that is not the caller's fault. `PolicyNotFound` is caught here and
    turned into an ordinary refusal, because a policy that does not exist is a
    fact about the caller's data. `PolicyLookupFailed` is deliberately not caught,
    because the caller did nothing wrong and the HTTP layer has to be able to tell
    the two apart. Contract section 6 fixes what each becomes.

    V-1 short circuits. Every other rule compares against a field on a policy, and
    if there is no policy there is nothing to compare against. Reporting
    LOSS_BEFORE_INCEPTION for a policy number that does not exist is not merely
    unhelpful, it is a false statement about the client's data (WI-0142, AC-4).
    """
    try:
        policy_client.get_policy(notification.policy_number)
    except PolicyNotFound:
        return ValidationOutcome(
            accepted=False,
            failure=RuleFailure(rule=RuleId("V-1"), code=ErrorCode("POLICY_NOT_FOUND")),
            detail={"policy_number": notification.policy_number},
        )
    return ValidationOutcome(accepted=True)


def evaluate_loss_after_inception(
    notification: NotificationRequest, policy: Policy
) -> RuleFailure | None:
    """V-2. The loss must not precede policy inception.

    The boundary is stated in contract section 4.2 and in WI-0142 AC-3. A loss on
    the inception date is covered.
    """
    if notification.loss_date < policy.effective_date:
        return RuleFailure(rule=RuleId("V-2"), code=ErrorCode("LOSS_BEFORE_INCEPTION"))
    return None


def evaluate_loss_before_expiry(
    notification: NotificationRequest, policy: Policy
) -> RuleFailure | None:
    """V-3. The loss must not fall after the policy expiry date."""
    if notification.loss_date > policy.expiry_date:
        return RuleFailure(rule=RuleId("V-3"), code=ErrorCode("LOSS_AFTER_EXPIRY"))
    return None


def evaluate_amount_within_limit(
    notification: NotificationRequest, policy: Policy
) -> RuleFailure | None:
    """V-4. The estimated amount must not exceed the policy limit.

    An amount equal to the limit is within cover, per contract section 4.2.
    """
    if notification.estimated_amount > policy.limit:
        return RuleFailure(rule=RuleId("V-4"), code=ErrorCode("AMOUNT_EXCEEDS_LIMIT"))
    return None


def evaluate_claim_type_covered(
    notification: NotificationRequest, policy: Policy
) -> RuleFailure | None:
    """V-5. The claim type must be permitted on the policy's product."""
    if notification.claim_type not in policy.permitted_claim_types:
        return RuleFailure(rule=RuleId("V-5"), code=ErrorCode("TYPE_NOT_COVERED"))
    return None


def evaluate_not_cancelled(
    notification: NotificationRequest, policy: Policy
) -> RuleFailure | None:
    if (
        policy.cancellation_date is not None
        and notification.loss_date >= policy.cancellation_date
    ):
        return RuleFailure(rule=RuleId("V-7"), code=ErrorCode("POLICY_CANCELLED"))
    return None


PolicyRule = Callable[[NotificationRequest, Policy], RuleFailure | None]

# V-1 needs the policy client; V-6 needs the repository. They run in
# submit_notification so POLICY_RULES stays a pure (notification, policy)
# table and contract section 4.1 order still holds: V-1, V-6, then this tuple.
POLICY_RULES: tuple[tuple[str, PolicyRule], ...] = (
    ("V-7", evaluate_not_cancelled),
    ("V-2", evaluate_loss_after_inception),
    ("V-3", evaluate_loss_before_expiry),
    ("V-4", evaluate_amount_within_limit),
    ("V-5", evaluate_claim_type_covered),
)


def evaluate_notification(
    notification: NotificationRequest, policy: Policy
) -> RuleFailure | None:
    """Evaluate every rule and return the outcome the caller sees.

    A notification can violate several rules at once and the caller sees one
    reason, so the order this function evaluates in is a caller-visible behavior.
    It is fixed by contract section 4.1 and by nothing else. If you find yourself
    choosing an order here, the contract is incomplete and the fix belongs there.
    """
    for _rule_id, rule_fn in POLICY_RULES:
        failure = rule_fn(notification, policy)
        if failure is not None:
            return failure
    return None


def submit_notification(
    notification: NotificationRequest,
    policy_client: PolicyClient,
    repository: NotificationRepository,
) -> ValidationOutcome:
    """Validate, and record only if every rule passed.

    Nothing is written before the decision is made. A notification is either
    recorded with a claim reference or it does not exist, and there is no state in
    between for a later reader to interpret.
    """
    try:
        record = policy_client.get_policy(notification.policy_number)
    except PolicyNotFound:
        return ValidationOutcome(
            accepted=False,
            failure=RuleFailure(rule=RuleId("V-1"), code=ErrorCode("POLICY_NOT_FOUND")),
        )
    # PolicyLookupFailed is not caught (contract section 6).

    existing = repository.find_matching(
        notification.policy_number,
        notification.loss_date,
        notification.claim_type,
    )
    if existing is not None:
        return ValidationOutcome(
            accepted=False,
            failure=RuleFailure(rule=RuleId("V-6"), code=ErrorCode("DUPLICATE_NOTIFICATION")),
            detail={"claim_reference": existing.claim_reference},
        )

    failure = evaluate_notification(notification, _policy_from_record(record))
    if failure is not None:
        return ValidationOutcome(accepted=False, failure=failure)

    saved = repository.record(AcceptedNotification.model_validate(notification.model_dump()))
    return ValidationOutcome(accepted=True, claim_reference=saved.claim_reference)
