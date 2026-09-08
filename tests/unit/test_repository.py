"""Store accepted notices and look up duplicates. Contract §3 and WI-0151."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import pytest

from claims.models import AcceptedNotification, NotificationRequest
from claims.repository import NotificationRepository

CLAIM_REF = re.compile(r"^CLM-\d{4}-\d{6}$")


@pytest.fixture
def repo() -> NotificationRepository:
    return NotificationRepository()


@pytest.fixture
def notice() -> NotificationRequest:
    return NotificationRequest.model_validate(
        {
            "policy_number": "MOT-4471",
            "loss_date": "2026-04-02",
            "claim_type": "collision",
            "estimated_amount": "4200.00",
        }
    )


@pytest.fixture
def accepted(notice: NotificationRequest) -> AcceptedNotification:
    return AcceptedNotification.model_validate(notice.model_dump())


def test_record_issues_a_claim_reference(
    repo: NotificationRepository, accepted: AcceptedNotification
) -> None:
    saved = repo.record(accepted)
    assert CLAIM_REF.match(saved.claim_reference)
    assert saved.policy_number == accepted.policy_number
    assert saved.loss_date == accepted.loss_date
    assert saved.claim_type == accepted.claim_type


def test_two_records_get_different_references(
    repo: NotificationRepository, accepted: AcceptedNotification
) -> None:
    first = repo.record(accepted)
    second = repo.record(
        AcceptedNotification.model_validate(
            {
                "policy_number": "MOT-4472",
                "loss_date": "2026-03-18",
                "claim_type": "theft",
                "estimated_amount": "12500.00",
            }
        )
    )
    assert first.claim_reference != second.claim_reference
    assert CLAIM_REF.match(second.claim_reference)


def test_same_three_fields_is_a_duplicate(
    repo: NotificationRepository, accepted: AcceptedNotification
) -> None:
    saved = repo.record(accepted)
    found = repo.find_matching(accepted.policy_number, accepted.loss_date, accepted.claim_type)
    assert found is not None
    assert found.claim_reference == saved.claim_reference


@pytest.mark.parametrize(
    "policy_number, loss_date, claim_type",
    [
        ("MOT-9999", date(2026, 4, 2), "collision"),
        ("MOT-4471", date(2025, 1, 1), "collision"),
        ("MOT-4471", date(2026, 4, 2), "theft"),
    ],
    ids=["different_policy", "different_date", "different_type"],
)
def test_two_of_three_is_not_a_duplicate(
    repo: NotificationRepository,
    accepted: AcceptedNotification,
    policy_number: str,
    loss_date: date,
    claim_type: str,
) -> None:
    repo.record(accepted)
    assert repo.find_matching(policy_number, loss_date, claim_type) is None


def test_rejected_request_cannot_be_recorded(
    repo: NotificationRepository, notice: NotificationRequest
) -> None:
    unpassed: Any = notice
    with pytest.raises(TypeError):
        repo.record(unpassed)
    assert repo.find_matching(notice.policy_number, notice.loss_date, notice.claim_type) is None


def test_rejected_notice_is_not_a_duplicate_when_later_accepted(
    repo: NotificationRepository,
    notice: NotificationRequest,
    accepted: AcceptedNotification,
) -> None:
    assert repo.find_matching(notice.policy_number, notice.loss_date, notice.claim_type) is None
    saved = repo.record(accepted)
    assert CLAIM_REF.match(saved.claim_reference)
