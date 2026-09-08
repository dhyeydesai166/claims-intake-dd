"""Rule tests from docs/api-contract.md §4 and docs/requirements-brief.md.

Shape is already guaranteed. These tests are the specification for V-1..V-7.
"""

from __future__ import annotations

import pytest

from claims.models import NotificationRequest, Policy, RuleFailure
from claims.policy_client import PolicyLookupFailed, StubPolicyClient
from claims.repository import NotificationRepository
from claims.service import (
    evaluate_amount_within_limit,
    evaluate_claim_type_covered,
    evaluate_loss_after_inception,
    evaluate_loss_before_expiry,
    evaluate_not_cancelled,
    evaluate_notification,
    submit_notification,
)


def notice(**overrides: object) -> NotificationRequest:
    payload: dict[str, object] = {
        "policy_number": "MOT-4471",
        "loss_date": "2026-04-02",
        "claim_type": "collision",
        "estimated_amount": "4200.00",
    }
    payload.update(overrides)
    return NotificationRequest.model_validate(payload)


def policy(**overrides: object) -> Policy:
    payload: dict[str, object] = {
        "policy_number": "MOT-4471",
        "product": "personal_auto_standard",
        "effective_date": "2026-03-01",
        "expiry_date": "2027-02-28",
        "cancellation_date": None,
        "limit": "50000.00",
        "permitted_claim_types": ["collision", "theft"],
    }
    payload.update(overrides)
    return Policy.model_validate(payload)


def assert_failure(result: RuleFailure | None, rule: str, code: str) -> RuleFailure:
    assert result is not None
    assert result.rule == rule
    assert result.code == code
    return result


@pytest.mark.parametrize(
    "loss_date, should_fail",
    [
        ("2026-02-28", True),   # before inception
        ("2026-03-01", False),  # on inception — covered (AC-3)
        ("2026-03-02", False),  # after inception
    ],
    ids=["before_inception", "on_inception", "after_inception"],
)
def test_v2_loss_against_inception(loss_date: str, should_fail: bool) -> None:
    """V-2: loss_date >= effective_date (WI-0142)."""
    result = evaluate_loss_after_inception(notice(loss_date=loss_date), policy())
    if should_fail:
        assert_failure(result, "V-2", "LOSS_BEFORE_INCEPTION")
    else:
        assert result is None


@pytest.mark.parametrize(
    "loss_date, should_fail",
    [
        ("2027-02-27", False),  # before expiry
        ("2027-02-28", False),  # on expiry — covered
        ("2027-03-01", True),   # after expiry
    ],
    ids=["before_expiry", "on_expiry", "after_expiry"],
)
def test_v3_loss_against_expiry(loss_date: str, should_fail: bool) -> None:
    """V-3: loss_date <= expiry_date."""
    result = evaluate_loss_before_expiry(notice(loss_date=loss_date), policy())
    if should_fail:
        assert_failure(result, "V-3", "LOSS_AFTER_EXPIRY")
    else:
        assert result is None


@pytest.mark.parametrize(
    "amount, should_fail",
    [
        ("49999.99", False),  # under
        ("50000.00", False),  # on limit — covered
        ("50000.01", True),   # over
    ],
    ids=["under_limit", "on_limit", "over_limit"],
)
def test_v4_amount_against_limit(amount: str, should_fail: bool) -> None:
    """V-4: estimated_amount <= limit."""
    result = evaluate_amount_within_limit(notice(estimated_amount=amount), policy())
    if should_fail:
        assert_failure(result, "V-4", "AMOUNT_EXCEEDS_LIMIT")
    else:
        assert result is None


@pytest.mark.parametrize(
    "claim_type, should_fail",
    [
        ("collision", False),  # in the permitted set
        ("theft", False),      # other permitted value
        ("glass", True),       # valid vocabulary, not on this product
    ],
    ids=["permitted_collision", "permitted_theft", "not_permitted_glass"],
)
def test_v5_claim_type_against_product(claim_type: str, should_fail: bool) -> None:
    """V-5: claim_type permitted on the policy product."""
    result = evaluate_claim_type_covered(notice(claim_type=claim_type), policy())
    if should_fail:
        assert_failure(result, "V-5", "TYPE_NOT_COVERED")
    else:
        assert result is None


@pytest.mark.parametrize(
    "loss_date, cancellation_date, should_fail",
    [
        ("2026-01-31", "2026-02-01", False),  # before cancel
        ("2026-02-01", "2026-02-01", True),   # on cancel — not covered (AC-2)
        ("2026-02-02", "2026-02-01", True),   # after cancel
        ("2026-04-02", None, False),          # absent cancel — rule does not apply (AC-3)
    ],
    ids=["before_cancel", "on_cancel", "after_cancel", "no_cancellation_date"],
)
def test_v7_loss_against_cancellation(
    loss_date: str, cancellation_date: str | None, should_fail: bool
) -> None:
    """V-7: cancellation (WI-0158)."""
    result = evaluate_not_cancelled(
        notice(loss_date=loss_date),
        policy(cancellation_date=cancellation_date),
    )
    if should_fail:
        assert_failure(result, "V-7", "POLICY_CANCELLED")
    else:
        assert result is None


def test_v7_beats_v3_when_both_would_fail() -> None:
    """WI-0158 AC-4: cancelled and after original expiry → POLICY_CANCELLED, not LOSS_AFTER_EXPIRY."""
    failure = evaluate_notification(
        notice(loss_date="2027-03-01"),
        policy(cancellation_date="2026-02-01", expiry_date="2027-02-28"),
    )
    assert_failure(failure, "V-7", "POLICY_CANCELLED")


def test_v1_unknown_policy_is_not_found() -> None:
    """V-1: policy exists (WI-0142 AC-4). Via submit, because it needs the client."""
    outcome = submit_notification(
        notice(policy_number="MOT-0000"),
        StubPolicyClient(),
        NotificationRepository(),
    )
    assert outcome.accepted is False
    assert outcome.claim_reference is None
    assert_failure(outcome.failure, "V-1", "POLICY_NOT_FOUND")


def test_v1_case_mismatch_is_not_found() -> None:
    """V-1: policy_number must match the master exactly, including case."""
    outcome = submit_notification(
        notice(policy_number="mot-4471"),
        StubPolicyClient(),
        NotificationRepository(),
    )
    assert outcome.accepted is False
    assert_failure(outcome.failure, "V-1", "POLICY_NOT_FOUND")


@pytest.mark.parametrize(
    "reason",
    ["timeout", "unreachable", "unparsable"],
    ids=["timeout", "unreachable", "unparsable"],
)
def test_policy_lookup_failed_propagates(reason: str) -> None:
    """PolicyLookupFailed is not a rule outcome and must leave submit uncaught."""
    client = StubPolicyClient(fail_with=reason)  # type: ignore[arg-type]
    with pytest.raises(PolicyLookupFailed) as caught:
        submit_notification(notice(), client, NotificationRepository())
    assert caught.value.reason == reason


def test_v6_same_three_fields_is_duplicate() -> None:
    """V-6: duplicate (WI-0151). Via submit, because it needs the repository."""
    repo = NotificationRepository()
    client = StubPolicyClient()
    first = submit_notification(notice(), client, repo)
    assert first.accepted is True
    assert first.claim_reference is not None
    second = submit_notification(notice(estimated_amount="100.00"), client, repo)
    assert second.accepted is False
    assert_failure(second.failure, "V-6", "DUPLICATE_NOTIFICATION")
    assert second.detail["claim_reference"] == first.claim_reference


@pytest.mark.parametrize(
    "overrides",
    [
        {"policy_number": "MOT-4472", "loss_date": "2026-03-18", "claim_type": "theft"},
        {"loss_date": "2026-04-03"},
        {"claim_type": "theft"},
    ],
    ids=["different_policy", "different_date", "different_type"],
)
def test_v6_two_of_three_is_not_duplicate(overrides: dict[str, str]) -> None:
    """V-6: a match on only two of the three fields is not a duplicate."""
    repo = NotificationRepository()
    client = StubPolicyClient()
    first = submit_notification(notice(), client, repo)
    assert first.accepted is True
    second = submit_notification(notice(**overrides), client, repo)
    assert second.accepted is True
    assert second.claim_reference != first.claim_reference


def test_v6_rejected_notice_is_not_a_duplicate() -> None:
    """WI-0151 AC-3: a refusal is not recorded, so a later accept is the first save."""
    repo = NotificationRepository()
    client = StubPolicyClient()
    refused = submit_notification(notice(policy_number="MOT-0000"), client, repo)
    assert refused.accepted is False
    accepted = submit_notification(notice(), client, repo)
    assert accepted.accepted is True
    assert accepted.claim_reference is not None