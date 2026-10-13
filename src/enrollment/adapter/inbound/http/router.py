"""The router of the enrollment service, the shared border helpers and the reads of EN-01..EN-03
and EN-07..EN-10 (07-api/contracts/openapi/qone-enrollment-api.yaml). The saga steps and the
certificates register their routes on the same router from `routes_saga` and
`routes_certificates`. Domain entities are never serialized directly (Annex C)."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from enrollment.application.port.inbound.caller import Caller
from enrollment.application.port.inbound.enrollment import DEFAULT_LIMIT, EnrollmentUseCases, RosterItem
from enrollment.application.usecase.enrollment import KEY_MAX, KEY_MIN
from enrollment.domain.model.enrollment import Enrollment
from enrollment.domain.model.errors import FieldError, ValidationError
from enrollment.domain.model.record import AcademicRecord, Pensum
from enrollment.domain.model.slot import Slot

PREFIX = "/api/v1/enrollment"
router = APIRouter(prefix=PREFIX)


def use_cases(request: Request) -> EnrollmentUseCases:
    return request.app.state.use_cases


def caller(request: Request) -> Caller:
    return request.app.state.verifier.verify(request.headers.get("Authorization"))


UseCases = Annotated[EnrollmentUseCases, Depends(use_cases)]
Current = Annotated[Caller, Depends(caller)]


@router.get("/health")
def health(request: Request):
    return {"status": "ok", "service": request.app.state.service, "version": request.app.state.version}


@router.get("/academic-records/me")
def my_record(who: Current, service: UseCases):
    return represent_record(service.my_record(who))


@router.get("/pensum/me")
def my_pensum(who: Current, service: UseCases):
    return represent_pensum(service.my_pensum(who))


@router.get("/enrollments/me")
def my_enrollments(
    who: Current,
    service: UseCases,
    page: int = Query(1),
    limit: int = Query(DEFAULT_LIMIT),
    term: str | None = Query(None),
    status: str | None = Query(None),
):
    return page_of(service.my_enrollments(who, term, status, page, limit), represent_enrollment)


@router.get("/enrollments/{enrollment_id}")
def get_enrollment(enrollment_id: str, who: Current, service: UseCases):
    return represent_enrollment(service.get_enrollment(who, parse_uuid(enrollment_id, "id")))


@router.get("/schedule/me")
def my_schedule(
    who: Current,
    service: UseCases,
    page: int = Query(1),
    limit: int = Query(DEFAULT_LIMIT),
    term: str | None = Query(None),
):
    return page_of(service.my_schedule(who, term, page, limit), represent_schedule_item)


@router.get("/sections/{section_id}/roster")
def roster(section_id: str, who: Current, service: UseCases, page: int = Query(1), limit: int = Query(DEFAULT_LIMIT)):
    return page_of(service.roster(who, parse_uuid(section_id, "sectionId"), page, limit), represent_roster_item)


def idempotency_key_of(request: Request) -> str:
    key = request.headers.get("Idempotency-Key", "")
    if not KEY_MIN <= len(key) <= KEY_MAX:
        raise ValidationError([FieldError("Idempotency-Key", "header required, 8 to 128 characters")])
    return key


def parse_uuid(value: str | None, field: str) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise ValidationError([FieldError(field, "must be a UUID")]) from None


def created(payload: dict, is_new: bool, location: str) -> JSONResponse:
    if not is_new:
        return JSONResponse(payload, status_code=200)
    return JSONResponse(payload, status_code=201, headers={"Location": location})


def page_of(page, represent) -> dict:
    return {
        "data": [represent(i) for i in page.items],
        "meta": {"page": page.page, "limit": page.limit, "total": page.total, "totalPages": page.total_pages},
    }


def rfc3339(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def represent_slots(slots: tuple[Slot, ...]) -> list[dict]:
    return [{"day": s.day.value, "start": s.start.strftime("%H:%M"), "end": s.end.strftime("%H:%M")} for s in slots]


def represent_record(record: AcademicRecord) -> dict:
    return {
        "studentId": str(record.student_id),
        "studentName": record.student_name,
        "program": record.program,
        "semester": record.semester,
        "passedCodes": list(record.passed_codes),
        "updatedAt": rfc3339(record.updated_at),
    }


def represent_pensum(pensum: Pensum) -> dict:
    t = pensum.totals
    return {
        "student": represent_record(pensum.record),
        "subjects": [
            {
                "code": p.subject.code,
                "name": p.subject.name,
                "credits": p.subject.credits,
                "semester": p.subject.semester,
                "prerequisites": list(p.subject.prerequisites),
                "state": p.state.value,
                "missingPrerequisites": list(p.missing_prerequisites),
            }
            for p in pensum.subjects
        ],
        "totals": {
            "creditsTotal": t.credits_total,
            "creditsPassed": t.credits_passed,
            "creditsInProgress": t.credits_in_progress,
            "percent": t.percent,
            "subjectsPassed": t.subjects_passed,
            "subjectsTotal": t.subjects_total,
        },
    }


def represent_enrollment(e: Enrollment) -> dict:
    return {
        "id": str(e.id),
        "studentId": str(e.student_id),
        "studentName": e.student_name,
        "term": e.term,
        "subjectCode": e.subject_code,
        "subjectName": e.subject_name,
        "sectionId": str(e.section_id),
        "groupNumber": e.group_number,
        "slots": represent_slots(e.slots),
        "status": e.status.value,
        "rejectionReason": e.rejection_reason.value if e.rejection_reason else None,
        "origin": e.origin.value,
        "confirmationId": str(e.confirmation_id) if e.confirmation_id else None,
        "sagaId": str(e.saga_id),
        "createdAt": rfc3339(e.created_at),
    }


def represent_schedule_item(e: Enrollment) -> dict:
    return {
        "enrollmentId": str(e.id),
        "subjectCode": e.subject_code,
        "subjectName": e.subject_name,
        "groupNumber": e.group_number,
        "slots": represent_slots(e.slots),
    }


def represent_roster_item(item: RosterItem) -> dict:
    return {
        "enrollmentId": str(item.enrollment_id),
        "studentId": str(item.student_id),
        "studentName": item.student_name,
        "enrolledAt": rfc3339(item.enrolled_at),
    }
