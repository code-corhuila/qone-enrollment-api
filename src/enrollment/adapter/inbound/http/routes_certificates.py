"""Certificate routes on the enrollment router (EN-11..EN-14): public verification by code, the
student's certificates, issue once per key, the PDF for its owner with X-Verification-Code."""

import re

from fastapi import Query, Request, Response
from pydantic import BaseModel, ConfigDict, StrictStr

from enrollment.adapter.inbound.http.router import (
    DEFAULT_LIMIT,
    PREFIX,
    Current,
    UseCases,
    created,
    idempotency_key_of,
    page_of,
    parse_uuid,
    rfc3339,
    router,
)
from enrollment.domain.model.certificate import Certificate, CertificateType
from enrollment.domain.model.errors import FieldError, ValidationError

CODE = re.compile(r"^QMP-[A-Z0-9]{8}$")


class CertificateBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: StrictStr


@router.get("/certificates/verify/{code}")
def verify(code: str, service: UseCases):
    if not CODE.match(code):
        raise ValidationError([FieldError("code", "must match ^QMP-[A-Z0-9]{8}$")])
    result = service.verify(code)
    if not result.valid or result.certificate is None:
        return {"valid": False, "type": None, "studentName": None, "issuedAt": None}
    c = result.certificate
    return {"valid": True, "type": c.type.value, "studentName": c.student_name, "issuedAt": rfc3339(c.issued_at)}


@router.get("/certificates/me")
def my_certificates(who: Current, service: UseCases, page: int = Query(1), limit: int = Query(DEFAULT_LIMIT)):
    return page_of(service.my_certificates(who, page, limit), represent_certificate)


@router.post("/certificates")
def issue(body: CertificateBody, who: Current, service: UseCases, request: Request):
    details: list[FieldError] = []
    key = ""
    try:
        key = idempotency_key_of(request)
    except ValidationError as error:
        details.extend(error.details)
    try:
        kind = CertificateType(body.type)
    except ValueError:
        details.append(FieldError("type", f"must be one of {', '.join(t.value for t in CertificateType)}"))
    if details:
        raise ValidationError(details)
    result = service.issue_certificate(who, kind, key)
    return created(represent_certificate(result.item), result.created, f"{PREFIX}/certificates/{result.item.id}/pdf")


@router.get("/certificates/{certificate_id}/pdf")
def pdf(certificate_id: str, who: Current, service: UseCases):
    certificate, document = service.certificate_document(who, parse_uuid(certificate_id, "id"))
    headers = {
        "X-Verification-Code": certificate.code,
        "Content-Disposition": f'attachment; filename="qampus-{certificate.type.value}-{certificate.code}.pdf"',
        "Access-Control-Expose-Headers": "X-Verification-Code, Content-Disposition",
    }
    return Response(content=document, media_type="application/pdf", headers=headers)


def represent_certificate(c: Certificate) -> dict:
    return {
        "id": str(c.id),
        "studentId": str(c.student_id),
        "studentName": c.student_name,
        "type": c.type.value,
        "code": c.code,
        "term": c.term,
        "issuedAt": rfc3339(c.issued_at),
    }
