"""In-memory fake of every outbound store, with the semantics the PostgreSQL adapter keeps."""

from uuid import UUID

from enrollment.application.port.outbound.ports import DuplicateActive
from enrollment.domain.model.certificate import Certificate
from enrollment.domain.model.eligibility import SectionView
from enrollment.domain.model.enrollment import Enrollment, EnrollmentStatus
from enrollment.domain.model.events import DomainEvent
from enrollment.domain.model.record import AcademicRecord, SubjectView


class InMemoryEnrollment:
    def __init__(
        self,
        records: list[AcademicRecord] | None = None,
        subjects: list[SubjectView] | None = None,
        sections: list[SectionView] | None = None,
        tuition: dict[tuple[UUID, str], str] | None = None,
        enrollments: list[Enrollment] | None = None,
    ) -> None:
        self.records = {r.student_id: r for r in records or []}
        self.subject_views = {s.code: s for s in subjects or []}
        self.section_views = {s.section_id: s for s in sections or []}
        self.tuition = dict(tuition or {})
        self.enrollments = {e.id: e for e in enrollments or []}
        self.certificates: dict[UUID, Certificate] = {}
        self.documents: dict[UUID, bytes] = {}
        self.keys: dict[str, UUID] = {}
        self.outbox: list[DomainEvent] = []

    # --- RecordStore
    def find_record(self, student_id: UUID) -> AcademicRecord | None:
        return self.records.get(student_id)

    def create_record(self, record: AcademicRecord) -> AcademicRecord:
        return self.records.setdefault(record.student_id, record)

    # --- Projections
    def subjects(self) -> list[SubjectView]:
        return list(self.subject_views.values())

    def subject(self, code: str) -> SubjectView | None:
        return self.subject_views.get(code)

    def section(self, section_id: UUID) -> SectionView | None:
        return self.section_views.get(section_id)

    def tuition_paid(self, student_id: UUID, term: str) -> bool:
        return self.tuition.get((student_id, term)) == "PAID"

    # --- EnrollmentStore
    def find_enrollment_by_key(self, key: str) -> Enrollment | None:
        target = self.keys.get(key)
        return self.enrollments.get(target) if target else None

    def find_enrollment(self, enrollment_id: UUID) -> Enrollment | None:
        return self.enrollments.get(enrollment_id)

    def confirmed_of(self, student_id: UUID, term: str) -> list[Enrollment]:
        rows = [
            e
            for e in self.enrollments.values()
            if e.student_id == student_id and e.term == term and e.status is EnrollmentStatus.CONFIRMED
        ]
        return sorted(rows, key=lambda e: e.subject_code)

    def enrollment_page(self, student_id, term, status, page, limit):
        rows = [
            e
            for e in self.enrollments.values()
            if e.student_id == student_id
            and (term is None or e.term == term)
            and (status is None or e.status.value == status)
        ]
        return _page(rows, page, limit)

    def roster(self, section_id: UUID, page: int, limit: int):
        rows = [
            e
            for e in self.enrollments.values()
            if e.section_id == section_id and e.status is EnrollmentStatus.CONFIRMED
        ]
        rows.sort(key=lambda e: e.student_name)
        start = (page - 1) * limit
        return rows[start : start + limit], len(rows)

    def create_enrollment(self, enrollment: Enrollment, key: str, event: DomainEvent):
        earlier = self.find_enrollment_by_key(key)
        if earlier is not None:
            return earlier, False
        if enrollment.status is EnrollmentStatus.CONFIRMED and any(
            e.student_id == enrollment.student_id
            and e.subject_code == enrollment.subject_code
            and e.term == enrollment.term
            and e.status is EnrollmentStatus.CONFIRMED
            for e in self.enrollments.values()
        ):
            raise DuplicateActive()
        self.enrollments[enrollment.id] = enrollment
        self.keys[key] = enrollment.id
        self.outbox.append(event)
        return enrollment, True

    def save_cancelled(self, enrollment: Enrollment, event: DomainEvent) -> None:
        self.enrollments[enrollment.id] = enrollment
        self.outbox.append(event)

    # --- CertificateStore
    def find_certificate_by_key(self, key: str) -> Certificate | None:
        target = self.keys.get(key)
        return self.certificates.get(target) if target else None

    def find_certificate(self, certificate_id: UUID) -> Certificate | None:
        return self.certificates.get(certificate_id)

    def find_by_code(self, code: str) -> Certificate | None:
        return next((c for c in self.certificates.values() if c.code == code), None)

    def certificate_page(self, student_id: UUID, page: int, limit: int):
        rows = [c for c in self.certificates.values() if c.student_id == student_id]
        rows.sort(key=lambda c: (c.issued_at, str(c.id)), reverse=True)
        start = (page - 1) * limit
        return rows[start : start + limit], len(rows)

    def create_certificate(self, certificate: Certificate, document: bytes, key: str, event: DomainEvent):
        earlier = self.find_certificate_by_key(key)
        if earlier is not None:
            return earlier, False
        self.certificates[certificate.id] = certificate
        self.documents[certificate.id] = document
        self.keys[key] = certificate.id
        self.outbox.append(event)
        return certificate, True

    def document_of(self, certificate_id: UUID) -> bytes | None:
        return self.documents.get(certificate_id)

    def as_ports(self):
        return _RecordPort(self), self, _EnrollmentPort(self), _CertificatePort(self)


def _page(rows, page, limit):
    rows = sorted(rows, key=lambda r: (r.created_at, str(r.id)), reverse=True)
    start = (page - 1) * limit
    return rows[start : start + limit], len(rows)


class _RecordPort:
    def __init__(self, store: InMemoryEnrollment) -> None:
        self.find, self.create = store.find_record, store.create_record


class _EnrollmentPort:
    def __init__(self, s: InMemoryEnrollment) -> None:
        self.find_by_key, self.find, self.confirmed_of = s.find_enrollment_by_key, s.find_enrollment, s.confirmed_of
        self.page_of, self.roster, self.create, self.save_cancelled = (
            s.enrollment_page,
            s.roster,
            s.create_enrollment,
            s.save_cancelled,
        )


class _CertificatePort:
    def __init__(self, s: InMemoryEnrollment) -> None:
        self.find_by_key, self.find, self.find_by_code = s.find_certificate_by_key, s.find_certificate, s.find_by_code
        self.page_of, self.create, self.document_of = s.certificate_page, s.create_certificate, s.document_of
