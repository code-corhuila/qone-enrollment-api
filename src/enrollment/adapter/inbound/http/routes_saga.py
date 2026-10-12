"""The saga steps of ADR-008 on the enrollment router (EN-04..EN-06): SERVICE token only,
enforced by the use case (D-10)."""

from fastapi import Request
from pydantic import BaseModel, ConfigDict, StrictStr

from enrollment.adapter.inbound.http.router import (
    PREFIX,
    Current,
    UseCases,
    created,
    idempotency_key_of,
    parse_uuid,
    represent_enrollment,
    router,
)
from enrollment.application.port.inbound.enrollment import RecordEnrollment
from enrollment.domain.model.enrollment import EnrollmentStatus, Origin, RejectionReason
from enrollment.domain.model.errors import FieldError, ValidationError


class EligibilityBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    studentId: StrictStr  # noqa: N815 - the contract's camelCase
    sectionId: StrictStr  # noqa: N815


class EnrollmentBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    studentId: StrictStr  # noqa: N815
    sectionId: StrictStr  # noqa: N815
    sagaId: StrictStr  # noqa: N815
    origin: StrictStr
    outcome: StrictStr
    reservationId: StrictStr | None = None  # noqa: N815
    confirmationId: StrictStr | None = None  # noqa: N815
    rejectionReason: StrictStr | None = None  # noqa: N815


@router.post("/eligibility-checks")
def eligibility(body: EligibilityBody, who: Current, service: UseCases):
    ids = _ids({"studentId": body.studentId, "sectionId": body.sectionId})
    verdict = service.check_eligibility(who, ids["studentId"], ids["sectionId"])
    payload: dict = {"eligible": verdict.eligible}
    if not verdict.eligible:
        payload["reason"] = verdict.reason.value if verdict.reason else None
        payload["missingPrerequisites"] = list(verdict.missing_prerequisites)
        payload["conflictingSectionId"] = (
            str(verdict.conflicting_section_id) if verdict.conflicting_section_id else None
        )
    return payload


@router.post("/enrollments")
def record(body: EnrollmentBody, who: Current, service: UseCases, request: Request):
    details: list[FieldError] = []
    key = ""
    try:
        key = idempotency_key_of(request)
    except ValidationError as error:
        details.extend(error.details)
    ids: dict = {}
    try:
        ids = _ids({"studentId": body.studentId, "sectionId": body.sectionId, "sagaId": body.sagaId})
    except ValidationError as error:
        details.extend(error.details)
    optional: dict = {}
    for field, value in (("reservationId", body.reservationId), ("confirmationId", body.confirmationId)):
        if value is not None:
            try:
                optional[field] = parse_uuid(value, field)
            except ValidationError as error:
                details.extend(error.details)
    origin = _enum(Origin, body.origin, "origin", details)
    outcome = _enum(EnrollmentStatus, body.outcome, "outcome", details)
    reason = _enum(RejectionReason, body.rejectionReason, "rejectionReason", details) if body.rejectionReason else None
    if details:
        raise ValidationError(details)
    command = RecordEnrollment(
        ids["studentId"],
        ids["sectionId"],
        ids["sagaId"],
        origin,
        outcome,
        reservation_id=optional.get("reservationId"),
        confirmation_id=optional.get("confirmationId"),
        rejection_reason=reason,
    )
    result = service.record(who, command, key)
    return created(represent_enrollment(result.item), result.created, f"{PREFIX}/enrollments/{result.item.id}")


@router.post("/enrollments/{enrollment_id}/cancel")
def cancel(enrollment_id: str, who: Current, service: UseCases):
    return represent_enrollment(service.cancel(who, parse_uuid(enrollment_id, "id")))


def _ids(values: dict[str, str]) -> dict:
    details: list[FieldError] = []
    out: dict = {}
    for field, value in values.items():
        try:
            out[field] = parse_uuid(value, field)
        except ValidationError as error:
            details.extend(error.details)
    if details:
        raise ValidationError(details)
    return out


def _enum(kind, value, field: str, details: list[FieldError]):
    try:
        return kind(value)
    except ValueError:
        details.append(FieldError(field, f"must be one of {', '.join(m.value for m in kind)}"))
        return None
