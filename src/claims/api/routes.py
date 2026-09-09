"""HTTP surface for the claims intake service.

This layer does three things and no more: it parses the request, it calls the
service, and it maps the outcome to a status code. It holds no rule logic. A rule
that appears here is a rule the service layer cannot be tested for.

Day 4 lab. Implement against `docs/api-contract.md` sections 5 and 6.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from claims.models import NotificationRequest
from claims.policy_client import PolicyClient, PolicyLookupFailed, StubPolicyClient
from claims.repository import NotificationRepository
from claims.service import ValidationOutcome, submit_notification

app = FastAPI(title="Claims Intake Service")

_default_policy_client = StubPolicyClient()
_default_repository = NotificationRepository()

# Contract section 6: each code maps to exactly one status.
STATUS_BY_CODE: dict[str, int] = {
    "MALFORMED_REQUEST": 400,
    "DUPLICATE_NOTIFICATION": 409,
    "POLICY_NOT_FOUND": 422,
    "POLICY_CANCELLED": 422,
    "LOSS_BEFORE_INCEPTION": 422,
    "LOSS_AFTER_EXPIRY": 422,
    "AMOUNT_EXCEEDS_LIMIT": 422,
    "TYPE_NOT_COVERED": 422,
    "INTERNAL_ERROR": 500,
    "POLICY_MASTER_INVALID_RESPONSE": 502,
    "POLICY_MASTER_UNAVAILABLE": 503,
    "POLICY_LOOKUP_TIMEOUT": 504,
}

MESSAGE_BY_CODE: dict[str, str] = {
    "MALFORMED_REQUEST": "The request could not be interpreted.",
    "DUPLICATE_NOTIFICATION": "A notification for this loss event is already recorded.",
    "POLICY_NOT_FOUND": "No policy exists with that number.",
    "POLICY_CANCELLED": "The policy was cancelled on or before the loss date.",
    "LOSS_BEFORE_INCEPTION": "The loss date is earlier than the policy effective date.",
    "LOSS_AFTER_EXPIRY": "The loss date is later than the policy expiry date.",
    "AMOUNT_EXCEEDS_LIMIT": "The estimated amount exceeds the policy limit.",
    "TYPE_NOT_COVERED": "The claim type is not permitted on this policy.",
    "INTERNAL_ERROR": "The service failed for a reason not covered by a named refusal.",
    "POLICY_MASTER_INVALID_RESPONSE": (
        "The policy master answered with something this service could not parse."
    ),
    "POLICY_MASTER_UNAVAILABLE": "The policy master could not be reached.",
    "POLICY_LOOKUP_TIMEOUT": "The policy master did not respond in time.",
}

# PolicyLookupFailed.reason -> (section 6 code, retryable). Status from STATUS_BY_CODE.
LOOKUP_FAILURE_BY_REASON: dict[str, tuple[str, bool]] = {
    "unparsable": ("POLICY_MASTER_INVALID_RESPONSE", False),
    "unreachable": ("POLICY_MASTER_UNAVAILABLE", True),
    "timeout": ("POLICY_LOOKUP_TIMEOUT", True),
}


def get_policy_client() -> PolicyClient:
    """Default policy client. Tests override this via app.dependency_overrides."""
    return _default_policy_client


def get_repository() -> NotificationRepository:
    """Default in-memory store. Tests override this via app.dependency_overrides."""
    return _default_repository


def _envelope(code: str, detail: dict[str, Any]) -> JSONResponse:
    return JSONResponse(
        status_code=STATUS_BY_CODE[code],
        content={
            "error": {
                "code": code,
                "message": MESSAGE_BY_CODE[code],
                "detail": detail,
            }
        },
    )


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'))}"


def _field_from_loc(loc: tuple[Any, ...]) -> str:
    parts = [str(item) for item in loc if item != "body"]
    return parts[0] if parts else "body"


def _problem_from_error(err: dict[str, Any]) -> str:
    err_type = str(err.get("type", ""))
    if err_type == "missing":
        return "required field absent"
    if err_type == "extra_forbidden":
        return "field not defined by this contract"
    if err_type in {"json_invalid", "value_error.jsondecode"}:
        return "body is not valid JSON"
    if err_type in {"json_type", "model_type", "dict_type"}:
        return "body is not a JSON object"
    if err_type == "string_too_short":
        return "must not be empty"
    if err_type in {"decimal_max_places", "decimal_places"}:
        return "must have no more than two decimal places"
    if err_type in {"greater_than", "greater_than_equal"}:
        return "must be greater than zero"
    if err_type == "literal_error":
        return "must be one of collision, theft, glass, liability, weather"
    if err_type.endswith("_type") or "parsing" in err_type:
        return "wrong type"
    return str(err.get("msg", "invalid value"))


def _problems_from_validation(errors: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {"field": _field_from_loc(tuple(err.get("loc", ()))), "problem": _problem_from_error(err)}
        for err in errors
    ]


def _malformed_from_errors(errors: list[Any]) -> JSONResponse:
    typed = [err if isinstance(err, dict) else dict(err) for err in errors]
    return _envelope("MALFORMED_REQUEST", {"problems": _problems_from_validation(typed)})


def _lookup_failed_response(exc: PolicyLookupFailed) -> JSONResponse:
    mapped = LOOKUP_FAILURE_BY_REASON.get(exc.reason)
    if mapped is None:
        raise exc
    code, retryable = mapped
    return _envelope(code, {"dependency": "policy_master", "retryable": retryable})


def _assemble_rule_detail(
    code: str,
    notification: NotificationRequest,
    outcome: ValidationOutcome,
    policy_client: PolicyClient,
) -> dict[str, Any]:
    """Fill section 5 detail. Does not decide rules; maps known values only.

    ``ValidationOutcome.detail`` is populated only for V-6. Other fields come
    from the request and a second policy read (the first already succeeded in
    ``submit_notification`` for every code except V-1).
    """
    if code == "POLICY_NOT_FOUND":
        return {"policy_number": notification.policy_number}
    if code == "DUPLICATE_NOTIFICATION":
        return {"claim_reference": str(outcome.detail.get("claim_reference", ""))}

    record = policy_client.get_policy(notification.policy_number)
    loss_date = notification.loss_date.isoformat()
    if code == "POLICY_CANCELLED":
        cancellation = record.cancellation_date
        return {
            "policy_number": notification.policy_number,
            "loss_date": loss_date,
            "cancellation_date": cancellation.isoformat() if cancellation is not None else "",
        }
    if code == "LOSS_BEFORE_INCEPTION":
        return {
            "policy_number": notification.policy_number,
            "loss_date": loss_date,
            "effective_date": record.effective_date.isoformat(),
        }
    if code == "LOSS_AFTER_EXPIRY":
        return {
            "policy_number": notification.policy_number,
            "loss_date": loss_date,
            "expiry_date": record.expiry_date.isoformat(),
        }
    if code == "AMOUNT_EXCEEDS_LIMIT":
        return {
            "policy_number": notification.policy_number,
            "estimated_amount": _money(notification.estimated_amount),
            "limit": _money(record.limit),
        }
    if code == "TYPE_NOT_COVERED":
        return {
            "policy_number": notification.policy_number,
            "claim_type": notification.claim_type,
            "permitted_claim_types": list(record.permitted_claim_types),
        }
    return {}


@app.exception_handler(RequestValidationError)
async def request_validation_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    return _malformed_from_errors(list(exc.errors()))


@app.exception_handler(ValidationError)
async def pydantic_validation_handler(_request: Request, exc: ValidationError) -> JSONResponse:
    return _malformed_from_errors(list(exc.errors()))


@app.exception_handler(PolicyLookupFailed)
async def policy_lookup_failed_handler(
    _request: Request, exc: PolicyLookupFailed
) -> JSONResponse:
    return _lookup_failed_response(exc)


@app.post("/notifications", status_code=201)
def post_notification(
    notification: NotificationRequest,
    policy_client: Annotated[PolicyClient, Depends(get_policy_client)],
    repository: Annotated[NotificationRepository, Depends(get_repository)],
) -> JSONResponse:
    try:
        outcome = submit_notification(notification, policy_client, repository)
    except PolicyLookupFailed:
        raise
    except Exception:  # noqa: BLE001
        return _envelope("INTERNAL_ERROR", {})

    if outcome.accepted and outcome.claim_reference is not None:
        return JSONResponse(
            status_code=201,
            content={"claim_reference": outcome.claim_reference, "status": "recorded"},
        )

    if outcome.failure is None:
        return _envelope("INTERNAL_ERROR", {})

    code = str(outcome.failure.code)
    if code not in STATUS_BY_CODE or code == "MALFORMED_REQUEST":
        return _envelope("INTERNAL_ERROR", {})

    try:
        detail = _assemble_rule_detail(code, notification, outcome, policy_client)
    except PolicyLookupFailed:
        raise
    except Exception:  # noqa: BLE001
        return _envelope("INTERNAL_ERROR", {})

    return _envelope(code, detail)
