"""The Enrollment aggregate: the outcome of an attempt the saga records (EN-05) and its
compensation (EN-06). There is no PENDING row: the in-flight state is the saga's.

- CONFIRMED requires the reservation the catalog gave; REJECTED requires its reason (D-01).
- An enrollment made by the agent carries the student's confirmation (INV-ENR-005).
- CONFIRMED only moves to CANCELLED, by saga compensation; cancelling a CANCELLED one is a no-op
  (D-04); REJECTED and CANCELLED are terminal (INV-ENR-004).
- One CONFIRMED enrollment per student, subject and term (INV-ENR-001) is kept by the use case
  and by the database's partial unique index.
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from enrollment.domain.model.eligibility import SectionView
from enrollment.domain.model.errors import FieldError, InvalidStatusTransition, ValidationError
from enrollment.domain.model.slot import Slot


class EnrollmentStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class RejectionReason(StrEnum):
    TUITION_UNPAID = "TUITION_UNPAID"
    PREREQ_MISSING = "PREREQ_MISSING"
    NO_SEATS = "NO_SEATS"
    SCHEDULE_CONFLICT = "SCHEDULE_CONFLICT"


class Origin(StrEnum):
    UI = "UI"
    AGENT = "AGENT"


@dataclass(frozen=True)
class Enrollment:
    id: UUID
    student_id: UUID
    student_name: str
    term: str
    subject_code: str
    subject_name: str
    section_id: UUID
    group_number: int
    slots: tuple[Slot, ...]
    status: EnrollmentStatus
    rejection_reason: RejectionReason | None
    origin: Origin
    confirmation_id: UUID | None
    reservation_id: UUID | None
    saga_id: UUID
    created_at: datetime
    updated_at: datetime

    @staticmethod
    def record(
        enrollment_id: UUID,
        student_id: UUID,
        student_name: str,
        section: SectionView,
        saga_id: UUID,
        origin: Origin,
        outcome: EnrollmentStatus,
        now: datetime,
        reservation_id: UUID | None = None,
        rejection_reason: RejectionReason | None = None,
        confirmation_id: UUID | None = None,
    ) -> "Enrollment":
        details: list[FieldError] = []
        if outcome is EnrollmentStatus.CANCELLED:
            details.append(FieldError("outcome", "must be CONFIRMED or REJECTED"))
        if outcome is EnrollmentStatus.CONFIRMED and reservation_id is None:
            details.append(FieldError("reservationId", "required when the outcome is CONFIRMED"))
        if outcome is EnrollmentStatus.REJECTED and rejection_reason is None:
            details.append(FieldError("rejectionReason", "required when the outcome is REJECTED"))
        if outcome is EnrollmentStatus.CONFIRMED and rejection_reason is not None:
            details.append(FieldError("rejectionReason", "only with the outcome REJECTED"))
        if origin is Origin.AGENT and confirmation_id is None:
            details.append(FieldError("confirmationId", "INV-ENR-005: required when the origin is AGENT"))
        if origin is Origin.UI and confirmation_id is not None:
            details.append(FieldError("confirmationId", "only with the origin AGENT"))
        if details:
            raise ValidationError(details)
        moment = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
        return Enrollment(
            id=enrollment_id,
            student_id=student_id,
            student_name=student_name,
            term=section.term,
            subject_code=section.subject_code,
            subject_name=section.subject_name,
            section_id=section.section_id,
            group_number=section.group_number,
            slots=section.slots,
            status=outcome,
            rejection_reason=rejection_reason,
            origin=origin,
            confirmation_id=confirmation_id,
            reservation_id=reservation_id if outcome is EnrollmentStatus.CONFIRMED else None,
            saga_id=saga_id,
            created_at=moment,
            updated_at=moment,
        )

    def cancel(self, now: datetime) -> tuple["Enrollment", bool]:
        if self.status is EnrollmentStatus.CANCELLED:
            return self, False
        if self.status is EnrollmentStatus.REJECTED:
            raise InvalidStatusTransition("INV-ENR-004: a REJECTED enrollment cannot be cancelled")
        moment = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
        return replace(self, status=EnrollmentStatus.CANCELLED, updated_at=moment), True
