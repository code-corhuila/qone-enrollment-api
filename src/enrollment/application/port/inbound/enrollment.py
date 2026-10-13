"""What the enrollment service offers (Annex C, inbound ports): EN-02..EN-14 as commands,
results and the use-case interface the HTTP adapter depends on."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from enrollment.application.port.inbound.caller import Caller
from enrollment.domain.model.certificate import Certificate, CertificateType
from enrollment.domain.model.eligibility import Verdict
from enrollment.domain.model.enrollment import Enrollment, EnrollmentStatus, Origin, RejectionReason
from enrollment.domain.model.record import AcademicRecord, Pensum

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


@dataclass(frozen=True)
class RecordEnrollment:
    student_id: UUID
    section_id: UUID
    saga_id: UUID
    origin: Origin
    outcome: EnrollmentStatus
    reservation_id: UUID | None = None
    confirmation_id: UUID | None = None
    rejection_reason: RejectionReason | None = None


@dataclass(frozen=True)
class Created[T]:
    item: T
    created: bool


@dataclass(frozen=True)
class Page[T]:
    items: list[T]
    page: int
    limit: int
    total: int

    @property
    def total_pages(self) -> int:
        return -(-self.total // self.limit) if self.total else 0


@dataclass(frozen=True)
class RosterItem:
    enrollment_id: UUID
    student_id: UUID
    student_name: str
    enrolled_at: object


@dataclass(frozen=True)
class Verification:
    valid: bool
    certificate: Certificate | None = None


class EnrollmentUseCases(Protocol):
    def my_record(self, caller: Caller) -> AcademicRecord: ...

    def my_pensum(self, caller: Caller) -> Pensum: ...

    def check_eligibility(self, caller: Caller, student_id: UUID, section_id: UUID) -> Verdict: ...

    def record(self, caller: Caller, command: RecordEnrollment, idempotency_key: str) -> Created[Enrollment]: ...

    def cancel(self, caller: Caller, enrollment_id: UUID) -> Enrollment: ...

    def my_enrollments(
        self, caller: Caller, term: str | None, status: str | None, page: int, limit: int
    ) -> Page[Enrollment]: ...

    def get_enrollment(self, caller: Caller, enrollment_id: UUID) -> Enrollment: ...

    def my_schedule(self, caller: Caller, term: str | None, page: int, limit: int) -> Page[Enrollment]: ...

    def roster(self, caller: Caller, section_id: UUID, page: int, limit: int) -> Page[RosterItem]: ...

    def verify(self, code: str) -> Verification: ...

    def my_certificates(self, caller: Caller, page: int, limit: int) -> Page[Certificate]: ...

    def issue_certificate(
        self, caller: Caller, kind: CertificateType, idempotency_key: str
    ) -> Created[Certificate]: ...

    def certificate_document(self, caller: Caller, certificate_id: UUID) -> tuple[Certificate, bytes]: ...
