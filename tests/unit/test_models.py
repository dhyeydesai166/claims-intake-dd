"""Shape checks for incoming and stored claims. Not business rules."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from claims.models import (
    ClaimRecord,
    ErrorCode,
    NotificationRequest,
    Policy,
    RuleFailure,
    RuleId,
)

DATA = Path(__file__).resolve().parents[2] / "data"
GOOD = {
    "policy_number": "MOT-4471",
    "loss_date": "2026-04-02",
    "claim_type": "collision",
    "estimated_amount": "4200.00",
}


def _load(name: str) -> dict[str, dict[str, Any]]:
    rows = json.loads((DATA / name).read_text())
    return {row["id"]: row["payload"] for row in rows}


EDGE = _load("fnol_edge.json")
INVALID = _load("fnol_invalid.json")


def test_good_request_parses() -> None:
    n = NotificationRequest.model_validate(GOOD)
    assert n.policy_number == "MOT-4471"
    assert n.loss_date == date(2026, 4, 2)
    assert n.claim_type == "collision"
    assert n.estimated_amount == Decimal("4200.00")
    assert n.description is None


@pytest.mark.parametrize(
    "claim_type",
    ["collision", "theft", "glass", "liability", "weather"],
    ids=["collision", "theft", "glass", "liability", "weather"],
)
def test_each_claim_type_parses(claim_type: str) -> None:
    payload = {**GOOD, "claim_type": claim_type}
    assert NotificationRequest.model_validate(payload).claim_type == claim_type


@pytest.mark.parametrize(
    "missing",
    ["policy_number", "loss_date", "claim_type", "estimated_amount"],
    ids=[
        "missing_policy_number",
        "missing_loss_date",
        "missing_claim_type",
        "missing_estimated_amount",
    ],
)
def test_missing_required_field_is_rejected(missing: str) -> None:
    payload = {k: v for k, v in GOOD.items() if k != missing}
    with pytest.raises(ValidationError):
        NotificationRequest.model_validate(payload)


@pytest.mark.parametrize(
    "field, value",
    [
        ("policy_number", ""),
        ("claim_type", ""),
        ("claim_type", "flood"),
        ("estimated_amount", "3499.999"),
        ("estimated_amount", "0.00"),
        ("estimated_amount", "-1.00"),
        ("loss_date", "02-04-2026"),
        ("policy_number", 4471),
        ("extra", "nope"),
    ],
    ids=[
        "empty_policy_number",
        "empty_claim_type",
        "unknown_claim_type",
        "too_many_decimals",
        "amount_zero",
        "amount_negative",
        "bad_loss_date",
        "policy_number_not_string",
        "unknown_field",
    ],
)
def test_bad_field_is_rejected(field: str, value: object) -> None:
    payload = {**GOOD, field: value}
    with pytest.raises(ValidationError):
        NotificationRequest.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [GOOD, {**GOOD, "description": None}],
    ids=["description_absent", "description_null"],
)
def test_description_absent_or_null_is_none(payload: dict[str, Any]) -> None:
    assert NotificationRequest.model_validate(payload).description is None


@pytest.mark.parametrize("payload_id", ["EDGE-08", "EDGE-11", "EDGE-12"])
def test_malformed_edge_payloads_fail(payload_id: str) -> None:
    payload = EDGE[payload_id]
    with pytest.raises(ValidationError):
        NotificationRequest.model_validate(payload)


@pytest.mark.parametrize(
    "payload_id",
    [
        "EDGE-01",
        "EDGE-02",
        "EDGE-03",
        "EDGE-04",
        "EDGE-05",
        "EDGE-06",
        "EDGE-07",
        "EDGE-09",
        "EDGE-10",
    ],
)
def test_other_edge_payloads_parse(payload_id: str) -> None:
    NotificationRequest.model_validate(EDGE[payload_id])


@pytest.mark.parametrize(
    "payload_id",
    [
        "INVALID-01",
        "INVALID-02",
        "INVALID-03",
        "INVALID-04",
        "INVALID-05",
        "INVALID-06",
        "INVALID-07",
    ],
)
def test_invalid_file_payloads_parse(payload_id: str) -> None:
    NotificationRequest.model_validate(INVALID[payload_id])


POLICY = {
    "policy_number": "MOT-4471",
    "product": "personal_auto_standard",
    "effective_date": "2026-03-01",
    "expiry_date": "2027-02-28",
    "cancellation_date": None,
    "limit": "50000.00",
    "permitted_claim_types": ["collision", "theft"],
}


def test_policy_with_no_cancellation_date() -> None:
    policy = Policy.model_validate(POLICY)
    assert policy.cancellation_date is None


@pytest.mark.parametrize(
    "field, value",
    [
        ("policy_number", ""),
        ("policy_number", 4471),
        ("product", ""),
        ("effective_date", "03-01-2026"),
        ("limit", "0.00"),
        ("limit", "50000.999"),
        ("permitted_claim_types", []),
        ("permitted_claim_types", ["flood"]),
        ("extra", "nope"),
    ],
    ids=[
        "empty_policy_number",
        "policy_number_not_string",
        "empty_product",
        "bad_effective_date",
        "limit_zero",
        "limit_too_many_decimals",
        "empty_permitted_types",
        "unknown_permitted_type",
        "unknown_field",
    ],
)
def test_bad_policy_is_rejected(field: str, value: object) -> None:
    payload = {**POLICY, field: value}
    with pytest.raises(ValidationError):
        Policy.model_validate(payload)


def test_rule_failure_cannot_be_changed() -> None:
    failure = RuleFailure(rule=RuleId("V-1"), code=ErrorCode("POLICY_NOT_FOUND"))
    assert failure.rule == "V-1"
    assert failure.code == "POLICY_NOT_FOUND"
    frozen: Any = failure
    with pytest.raises(FrozenInstanceError):
        frozen.code = "X"


def test_claim_record_stores_reference() -> None:
    record = ClaimRecord(
        claim_reference="CLM-2026-000001",
        policy_number="MOT-4471",
        loss_date=date(2026, 4, 2),
        claim_type="collision",
        estimated_amount=Decimal("4200.00"),
        description=None,
    )
    assert record.claim_reference == "CLM-2026-000001"


@pytest.mark.parametrize(
    "field, value",
    [
        ("claim_reference", "CLM-26-1"),
        ("claim_type", "flood"),
        ("estimated_amount", "0.00"),
    ],
    ids=["bad_claim_reference", "unknown_claim_type", "amount_zero"],
)
def test_bad_claim_record_is_rejected(field: str, value: object) -> None:
    payload = {
        "claim_reference": "CLM-2026-000001",
        "policy_number": "MOT-4471",
        "loss_date": date(2026, 4, 2),
        "claim_type": "collision",
        "estimated_amount": Decimal("4200.00"),
        "description": None,
        field: value,
    }
    with pytest.raises(ValidationError):
        ClaimRecord.model_validate(payload)
