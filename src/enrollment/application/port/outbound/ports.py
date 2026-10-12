"""What the enrollment service needs (Annex C, outbound ports); the projections of catalog and
billing are read only here, their consumers keep them."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from enrollment.domain.model.certificate import Certificate
from enrollment.domain.model.eligibility import SectionView
from enrollment.domain.model.enrollment import Enrollment
from enrollment.domain.model.events import DomainEvent
from enrollment.domain.model.record import AcademicRecord, PensumTotals, SubjectView


class DuplicateActive(Exception):
    """The partial unique index of INV-ENR-001 fired."""


class RecordStore(Protocol):
    def find(self, student_id: UUID) -> AcademicRecord | None: ...

    def create(self, record: AcademicRecord) -> AcademicRecord:
        """Creates the record if it does not exist yet and returns the stored one."""


class Projections(Protocol):
    def subjects(self) -> list[SubjectView]: ...

    def subject(self, code: str) -> SubjectView | None: ...

    def section(self, section_id: UUID) -> SectionView | None: ...

    def tuition_paid(self, student_id: UUID, term: str) -> bool: ...


class EnrollmentStore(Protocol):
    def find_by_key(self, idempotency_key: str) -> Enrollment | None: ...

    def find(self, enrollment_id: UUID) -> Enrollment | None: ...

    def confirmed_of(self, student_id: UUID, term: str) -> list[Enrollment]: ...

    def page_of(
        self, student_id: UUID, term: str | None, status: str | None, page: int, limit: int
    ) -> tuple[list[Enrollment], int]: ...

    def roster(self, section_id: UUID, page: int, limit: int) -> tuple[list[Enrollment], int]: ...

    def create(self, enrollment: Enrollment, idempotency_key: str, event: DomainEvent) -> tuple[Enrollment, bool]:
        """One transaction: the row, its slots, the key and the event. Raises DuplicateActive."""

    def save_cancelled(self, enrollment: Enrollment, event: DomainEvent) -> None: ...


class CertificateStore(Protocol):
    def find_by_key(self, idempotency_key: str) -> Certificate | None: ...

    def find(self, certificate_id: UUID) -> Certificate | None: ...

    def find_by_code(self, code: str) -> Certificate | None: ...

    def page_of(self, student_id: UUID, page: int, limit: int) -> tuple[list[Certificate], int]: ...

    def create(
        self, certificate: Certificate, document: bytes, idempotency_key: str, event: DomainEvent
    ) -> tuple[Certificate, bool]: ...

    def document_of(self, certificate_id: UUID) -> bytes | None: ...


@dataclass(frozen=True)
class CertificateContext:
    """What a certificate says, as the use case gathered it at issue."""

    certificate: Certificate
    record: AcademicRecord
    confirmed: list[Enrollment]
    credits_by_code: dict[str, int]
    totals: PensumTotals
    tuition_paid: bool


class DocumentRenderer(Protocol):
    def render(self, context: CertificateContext) -> bytes: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdGenerator(Protocol):
    def new_id(self) -> UUID: ...
