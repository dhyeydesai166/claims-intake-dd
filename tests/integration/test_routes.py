"""HTTP integration tests for POST /notifications.

Exercises the service through FastAPI TestClient, not submit_notification.

After the HTTP layer lands, routes.py is expected to expose overridable
dependencies named get_policy_client and get_repository. Tests bind those
with app.dependency_overrides. If the names are missing, collection or
setup fails until HTTP is merged — that is expected.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from claims.api.routes import app, get_policy_client, get_repository
from claims.policy_client import LookupFailureReason, StubPolicyClient
from claims.repository import NotificationRepository

DATA = Path(__file__).resolve().parents[2] / "data"
CLAIM_REF = re.compile(r"^CLM-\d{4}-\d{6}$")
ENDPOINT = "/notifications"

# Rule codes from contract §6. A parse failure must not return one of these.
RULE_CODES = frozenset(
    {
        "POLICY_NOT_FOUND",
        "LOSS_BEFORE_INCEPTION",
        "LOSS_AFTER_EXPIRY",
        "AMOUNT_EXCEEDS_LIMIT",
        "TYPE_NOT_COVERED",
        "DUPLICATE_NOTIFICATION",
        "POLICY_CANCELLED",
    }
)

# §5: every listed field is present on the matching code.
DETAIL_FIELDS: dict[str, tuple[str, ...]] = {
    "MALFORMED_REQUEST": ("problems",),
    "POLICY_NOT_FOUND": ("policy_number",),
    "DUPLICATE_NOTIFICATION": ("claim_reference",),
    "POLICY_CANCELLED": ("policy_number", "loss_date", "cancellation_date"),
    "LOSS_BEFORE_INCEPTION": ("policy_number", "loss_date", "effective_date"),
    "LOSS_AFTER_EXPIRY": ("policy_number", "loss_date", "expiry_date"),
    "AMOUNT_EXCEEDS_LIMIT": ("policy_number", "estimated_amount", "limit"),
    "TYPE_NOT_COVERED": ("policy_number", "claim_type", "permitted_claim_types"),
    "POLICY_MASTER_INVALID_RESPONSE": ("dependency", "retryable"),
    "POLICY_MASTER_UNAVAILABLE": ("dependency", "retryable"),
    "POLICY_LOOKUP_TIMEOUT": ("dependency", "retryable"),
}

LOOKUP_FAILURES: tuple[tuple[LookupFailureReason, int, str], ...] = (
    ("unparsable", 502, "POLICY_MASTER_INVALID_RESPONSE"),
    ("unreachable", 503, "POLICY_MASTER_UNAVAILABLE"),
    ("timeout", 504, "POLICY_LOOKUP_TIMEOUT"),
)


def _load(name: str) -> dict[str, dict[str, Any]]:
    rows = json.loads((DATA / name).read_text())
    return {row["id"]: row["payload"] for row in rows}


VALID = _load("fnol_valid.json")
INVALID = _load("fnol_invalid.json")


@pytest.fixture
def repository() -> NotificationRepository:
    return NotificationRepository()


@pytest.fixture
def client(
    policy_client: StubPolicyClient, repository: NotificationRepository
) -> Iterator[TestClient]:
    # Override names the HTTP layer must expose (get_policy_client, get_repository).
    app.dependency_overrides[get_policy_client] = lambda: policy_client
    app.dependency_overrides[get_repository] = lambda: repository
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def post_notification(client: TestClient, payload: dict[str, Any]) -> Response:
    return cast(Response, client.post(ENDPOINT, json=payload))


def assert_error_envelope(response: Response, *, status: int, code: str) -> dict[str, Any]:
    """Assert §6 status, §5 envelope, and the detail fields listed for this code."""
    assert response.status_code == status
    body = response.json()
    assert "error" in body
    error = body["error"]
    assert isinstance(error, dict)
    assert error["code"] == code
    assert isinstance(error.get("message"), str) and error["message"]
    detail = error.get("detail")
    assert isinstance(detail, dict)
    for field in DETAIL_FIELDS[code]:
        assert field in detail, f"§5 detail field {field!r} missing for {code}"
    return error


def assert_malformed_problems(detail: dict[str, Any]) -> list[dict[str, Any]]:
    problems = detail["problems"]
    assert isinstance(problems, list) and problems
    for item in problems:
        assert isinstance(item, dict)
        assert isinstance(item.get("field"), str) and item["field"]
        assert isinstance(item.get("problem"), str) and item["problem"]
    return problems


def test_accepted_notification_returns_201_recorded(client: TestClient) -> None:
    """§3 success: 201, claim_reference CLM-YYYY-NNNNNN, status recorded."""
    response = post_notification(client, VALID["VALID-01"])
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "recorded"
    assert CLAIM_REF.fullmatch(body["claim_reference"])


def test_v1_policy_not_found_is_422(client: TestClient) -> None:
    """§6 POLICY_NOT_FOUND / V-1 (INVALID-01). 422, not 5xx; no dependency in detail."""
    payload = INVALID["INVALID-01"]
    error = assert_error_envelope(
        post_notification(client, payload), status=422, code="POLICY_NOT_FOUND"
    )
    assert error["detail"]["policy_number"] == payload["policy_number"]
    assert "dependency" not in error["detail"]


def test_v2_loss_before_inception_is_422(client: TestClient) -> None:
    """§6 LOSS_BEFORE_INCEPTION / V-2 (INVALID-02)."""
    payload = INVALID["INVALID-02"]
    error = assert_error_envelope(
        post_notification(client, payload), status=422, code="LOSS_BEFORE_INCEPTION"
    )
    assert error["detail"]["policy_number"] == payload["policy_number"]
    assert error["detail"]["loss_date"] == payload["loss_date"]
    assert error["detail"]["effective_date"] == "2026-03-15"


def test_v3_loss_after_expiry_is_422(client: TestClient) -> None:
    """§6 LOSS_AFTER_EXPIRY / V-3 (INVALID-03)."""
    payload = INVALID["INVALID-03"]
    error = assert_error_envelope(
        post_notification(client, payload), status=422, code="LOSS_AFTER_EXPIRY"
    )
    assert error["detail"]["policy_number"] == payload["policy_number"]
    assert error["detail"]["loss_date"] == payload["loss_date"]
    assert error["detail"]["expiry_date"] == "2026-02-28"


def test_v4_amount_exceeds_limit_is_422(client: TestClient) -> None:
    """§6 AMOUNT_EXCEEDS_LIMIT / V-4 (INVALID-04)."""
    payload = INVALID["INVALID-04"]
    error = assert_error_envelope(
        post_notification(client, payload), status=422, code="AMOUNT_EXCEEDS_LIMIT"
    )
    assert error["detail"]["policy_number"] == payload["policy_number"]
    assert error["detail"]["estimated_amount"] == "14500.00"
    assert error["detail"]["limit"] == "10000.00"


def test_v5_type_not_covered_is_422(client: TestClient) -> None:
    """§6 TYPE_NOT_COVERED / V-5 (INVALID-05)."""
    payload = INVALID["INVALID-05"]
    error = assert_error_envelope(
        post_notification(client, payload), status=422, code="TYPE_NOT_COVERED"
    )
    assert error["detail"]["policy_number"] == payload["policy_number"]
    assert error["detail"]["claim_type"] == payload["claim_type"]
    permitted = error["detail"]["permitted_claim_types"]
    assert isinstance(permitted, list)
    assert "liability" in permitted


def test_v6_duplicate_notification_is_409(client: TestClient) -> None:
    """§6 DUPLICATE_NOTIFICATION / V-6: record VALID-01, then INVALID-06."""
    first = post_notification(client, VALID["VALID-01"])
    assert first.status_code == 201
    claim_reference = first.json()["claim_reference"]

    error = assert_error_envelope(
        post_notification(client, INVALID["INVALID-06"]),
        status=409,
        code="DUPLICATE_NOTIFICATION",
    )
    assert error["detail"]["claim_reference"] == claim_reference
    assert CLAIM_REF.fullmatch(error["detail"]["claim_reference"])


def test_v7_policy_cancelled_is_422(client: TestClient) -> None:
    """§6 POLICY_CANCELLED / V-7 (INVALID-07)."""
    payload = INVALID["INVALID-07"]
    error = assert_error_envelope(
        post_notification(client, payload), status=422, code="POLICY_CANCELLED"
    )
    assert error["detail"]["policy_number"] == payload["policy_number"]
    assert error["detail"]["loss_date"] == payload["loss_date"]
    assert error["detail"]["cancellation_date"] == "2026-02-01"


def test_missing_required_field_is_400_malformed_request(client: TestClient) -> None:
    """§6 MALFORMED_REQUEST / R-1: required field absent. Not a rule code. Not FastAPI 422."""
    payload = dict(VALID["VALID-01"])
    del payload["estimated_amount"]
    response = post_notification(client, payload)

    assert response.status_code != 422
    error = assert_error_envelope(response, status=400, code="MALFORMED_REQUEST")
    assert error["code"] not in RULE_CODES

    body = response.json()
    assert not _looks_like_fastapi_validation_error(body)
    problems = assert_malformed_problems(error["detail"])
    assert any(item["field"] == "estimated_amount" for item in problems)


def test_extra_field_is_400_malformed_request(client: TestClient) -> None:
    """§2.2 / §6 MALFORMED_REQUEST: unknown field is refused, not accepted and ignored."""
    payload = dict(VALID["VALID-01"])
    payload["unexpected_field"] = "should-not-be-ignored"
    response = post_notification(client, payload)

    assert response.status_code != 201
    error = assert_error_envelope(response, status=400, code="MALFORMED_REQUEST")
    problems = assert_malformed_problems(error["detail"])
    assert any(item["field"] == "unexpected_field" for item in problems)


@pytest.mark.parametrize(
    "reason, status, code",
    LOOKUP_FAILURES,
    ids=["unparsable", "unreachable", "timeout"],
)
def test_policy_lookup_failed_maps_to_5xx(
    client: TestClient,
    policy_client: StubPolicyClient,
    reason: LookupFailureReason,
    status: int,
    code: str,
) -> None:
    """§6 policy-master 5xx via StubPolicyClient.fail_with. Not 4xx, not POLICY_NOT_FOUND."""
    policy_client.fail_with = reason
    response = post_notification(client, VALID["VALID-01"])

    assert response.status_code == status
    assert 500 <= response.status_code <= 599
    assert response.status_code not in {400, 409, 422}

    error = assert_error_envelope(response, status=status, code=code)
    assert error["code"] != "POLICY_NOT_FOUND"
    assert error["detail"]["dependency"] == "policy_master"
    assert isinstance(error["detail"]["retryable"], bool)


def test_policy_not_found_is_distinguishable_from_lookup_failures(
    client: TestClient, policy_client: StubPolicyClient
) -> None:
    """§6: POLICY_NOT_FOUND is 422 with no dependency; lookup failures are distinct 5xx."""
    not_found = post_notification(client, INVALID["INVALID-01"])
    error = assert_error_envelope(not_found, status=422, code="POLICY_NOT_FOUND")
    assert "dependency" not in error["detail"]
    assert 400 <= not_found.status_code < 500

    seen: dict[str, tuple[int, str]] = {}
    for reason, status, code in LOOKUP_FAILURES:
        policy_client.fail_with = reason
        response = post_notification(client, VALID["VALID-01"])
        lookup_error = assert_error_envelope(response, status=status, code=code)
        seen[reason] = (response.status_code, lookup_error["code"])
        assert 500 <= response.status_code <= 599
        assert response.status_code != 422
        assert lookup_error["code"] != "POLICY_NOT_FOUND"
        assert lookup_error["detail"]["dependency"] == "policy_master"

    policy_client.fail_with = None
    statuses = {status for status, _ in seen.values()}
    codes = {code for _, code in seen.values()}
    assert seen["unparsable"] == (502, "POLICY_MASTER_INVALID_RESPONSE")
    assert seen["unreachable"] == (503, "POLICY_MASTER_UNAVAILABLE")
    assert seen["timeout"] == (504, "POLICY_LOOKUP_TIMEOUT")
    assert len(statuses) == 3
    assert len(codes) == 3
    assert "POLICY_NOT_FOUND" not in codes


def _looks_like_fastapi_validation_error(body: dict[str, Any]) -> bool:
    """Bare FastAPI 422: top-level detail list, no §5 error envelope."""
    return "error" not in body and isinstance(body.get("detail"), list)
