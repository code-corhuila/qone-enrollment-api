"""Events the enrollment domain publishes (02-domain/domain-events.md) through its outbox:
EnrollmentConfirmed, EnrollmentRejected, EnrollmentCancelled, CertificateIssued."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from enrollment.domain.model.certificate import Certificate
from enrollment.domain.model.enrollment import Enrollment, EnrollmentStatus

_TYPES = {
    EnrollmentStatus.CONFIRMED: "EnrollmentConfirmed",
    EnrollmentStatus.REJECTED: "EnrollmentRejected",
    EnrollmentStatus.CANCELLED: "EnrollmentCancelled",
}


@dataclass(frozen=True)
class DomainEvent:
    event_type: str
    aggregate_type: str
    aggregate_id: UUID
    payload: dict[str, Any]
    occurred_at: datetime


def enrollment_event(enrollment: Enrollment) -> DomainEvent:
    payload = {
        "enrollmentId": str(enrollment.id),
        "studentId": str(enrollment.student_id),
        "term": enrollment.term,
        "subjectCode": enrollment.subject_code,
        "sectionId": str(enrollment.section_id),
        "status": enrollment.status.value,
        "rejectionReason": enrollment.rejection_reason.value if enrollment.rejection_reason else None,
        "origin": enrollment.origin.value,
        "sagaId": str(enrollment.saga_id),
    }
    return DomainEvent(_TYPES[enrollment.status], "enrollment", enrollment.id, payload, enrollment.updated_at)


def certificate_issued(certificate: Certificate) -> DomainEvent:
    payload = {
        "certificateId": str(certificate.id),
        "studentId": str(certificate.student_id),
        "type": certificate.type.value,
        "code": certificate.code,
        "term": certificate.term,
    }
    return DomainEvent("CertificateIssued", "certificate", certificate.id, payload, certificate.issued_at)
