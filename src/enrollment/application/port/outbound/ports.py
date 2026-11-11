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


class KeyTaken(Exception):
    """The owner's Idempotency-Key was recorded meanwhile (a concurrent first attempt won)."""


@dataclass(frozen=True)
class IdempotencyKey:
    """An Idempotency-Key as the stores keep it (qone-docs#55): scoped to its owner (the user id of the
    token, or the subject of a SERVICE token) with the SHA-256 of the canonical request body."""

    owner: str
    value: str
    request_hash: str


@dataclass(frozen=True)
class Keyed[T]:
    """What an owner's key recorded: the resource and the fingerprint of the body that created it
    (None for a key recorded before the fingerprint existed)."""

    item: T
    request_hash: str | None


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
    def find_by_key(self, owner: str, idempotency_key: str) -> Keyed[Enrollment] | None: ...

    def find(self, enrollment_id: UUID) -> Enrollment | None: ...

    def confirmed_of(self, student_id: UUID, term: str) -> list[Enrollment]: ...

    def page_of(
        self, student_id: UUID, term: str | None, status: str | None, page: int, limit: int
    ) -> tuple[list[Enrollment], int]: ...

    def roster(self, section_id: UUID, page: int, limit: int) -> tuple[list[Enrollment], int]: ...

    def create(self, enrollment: Enrollment, key: IdempotencyKey, event: DomainEvent) -> None:
        """One transaction: the row, its slots, the key and the event. Raises DuplicateActive or KeyTaken."""

    def save_cancelled(self, enrollment: Enrollment, event: DomainEvent) -> None: ...


class CertificateStore(Protocol):
    def find_by_key(self, owner: str, idempotency_key: str) -> Keyed[Certificate] | None: ...

    def find(self, certificate_id: UUID) -> Certificate | None: ...

    def find_by_code(self, code: str) -> Certificate | None: ...

    def page_of(self, student_id: UUID, page: int, limit: int) -> tuple[list[Certificate], int]: ...

    def create(self, certificate: Certificate, document: bytes, key: IdempotencyKey, event: DomainEvent) -> None:
        """One transaction: the row with its document, the key and the event. Raises KeyTaken."""

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


class DependencyProbe(Protocol):
    """A dependency the service needs to take traffic (HU-PLT-004): `ping` returns when it answers
    within the adapter's short timeout and raises otherwise."""

    name: str

    def ping(self) -> None: ...
