"""The operations of the enrollment service (EN-02..EN-14) over the ports. Authorization by role
and ownership (07-api/authentication.md access matrix): the `/me` reads, the certificates and
their documents are STUDENT only; one enrollment for its owner, PROFESSOR, ADMIN or SERVICE
(another student gets 403); the roster for PROFESSOR or ADMIN; the saga steps for the SERVICE
token only (D-10); the verification of a certificate is public."""

import re
from uuid import UUID

from enrollment.application.errors import Forbidden, NotFound
from enrollment.application.port.inbound.caller import Caller, Role
from enrollment.application.port.inbound.enrollment import (
    MAX_LIMIT,
    Created,
    EnrollmentUseCases,
    Page,
    RecordEnrollment,
    RosterItem,
    Verification,
)
from enrollment.application.port.outbound.ports import (
    CertificateContext,
    CertificateStore,
    Clock,
    DocumentRenderer,
    DuplicateActive,
    EnrollmentStore,
    IdGenerator,
    Projections,
    RecordStore,
)
from enrollment.domain.model.certificate import Certificate, CertificateType, ensure_preconditions, issue
from enrollment.domain.model.eligibility import ActiveEnrollment, Verdict, check_eligibility
from enrollment.domain.model.enrollment import Enrollment, EnrollmentStatus
from enrollment.domain.model.errors import BusinessRuleViolation, FieldError, ValidationError
from enrollment.domain.model.events import certificate_issued, enrollment_event
from enrollment.domain.model.record import AcademicRecord, Pensum, pensum_of

KEY_MIN, KEY_MAX = 8, 128
TERM_PATTERN = re.compile(r"^[0-9]{4}-[AB]$")


class EnrollmentService(EnrollmentUseCases):
    def __init__(
        self,
        records: RecordStore,
        projections: Projections,
        enrollments: EnrollmentStore,
        certificates: CertificateStore,
        renderer: DocumentRenderer,
        clock: Clock,
        ids: IdGenerator,
        current_term: str,
        default_program: str,
    ) -> None:
        self._records = records
        self._projections = projections
        self._enrollments = enrollments
        self._certificates = certificates
        self._renderer = renderer
        self._clock = clock
        self._ids = ids
        self._term = current_term
        self._program = default_program

    # --- the record and the pensum (EN-02, EN-03)
    def my_record(self, caller: Caller) -> AcademicRecord:
        _require(caller, Role.STUDENT)
        return self._record_of(caller)

    def my_pensum(self, caller: Caller) -> Pensum:
        record = self.my_record(caller)
        in_progress = {e.subject_code for e in self._enrollments.confirmed_of(record.student_id, self._term)}
        return pensum_of(record, self._projections.subjects(), in_progress)

    # --- the saga steps (EN-04..EN-06)
    def check_eligibility(self, caller: Caller, student_id: UUID, section_id: UUID) -> Verdict:
        _require(caller, Role.SERVICE)
        section = self._projections.section(section_id)
        if section is None:
            raise NotFound("section not found")
        subject = self._projections.subject(section.subject_code)
        record = self._records.find(student_id)
        passed = set(record.passed_codes) if record else set()
        active = [
            ActiveEnrollment(e.section_id, e.subject_code, e.slots)
            for e in self._enrollments.confirmed_of(student_id, section.term)
        ]
        return check_eligibility(section, subject.prerequisites if subject else (), passed, active)

    def record(self, caller: Caller, command: RecordEnrollment, idempotency_key: str) -> Created[Enrollment]:
        _require(caller, Role.SERVICE)
        _check_key(idempotency_key)
        earlier = self._enrollments.find_by_key(idempotency_key)
        if earlier is not None:
            return Created(earlier, created=False)
        section = self._projections.section(command.section_id)
        if section is None:
            raise NotFound("section not found")
        record = self._records.find(command.student_id)
        student_name = record.student_name if record else "Unknown student"
        enrollment = Enrollment.record(
            self._ids.new_id(),
            command.student_id,
            student_name,
            section,
            command.saga_id,
            command.origin,
            command.outcome,
            self._clock.now(),
            reservation_id=command.reservation_id,
            rejection_reason=command.rejection_reason,
            confirmation_id=command.confirmation_id,
        )
        try:
            stored, created = self._enrollments.create(enrollment, idempotency_key, enrollment_event(enrollment))
        except DuplicateActive:
            raise _duplicate(enrollment) from None
        return Created(stored, created)

    def cancel(self, caller: Caller, enrollment_id: UUID) -> Enrollment:
        _require(caller, Role.SERVICE)
        enrollment = self._enrollments.find(enrollment_id)
        if enrollment is None:
            raise NotFound()
        cancelled, changed = enrollment.cancel(self._clock.now())
        if changed:
            self._enrollments.save_cancelled(cancelled, enrollment_event(cancelled))
        return cancelled

    # --- the student's enrollments and schedule, one enrollment, the roster (EN-07..EN-10)
    def my_enrollments(
        self, caller: Caller, term: str | None, status: str | None, page: int, limit: int
    ) -> Page[Enrollment]:
        _require(caller, Role.STUDENT)
        details = _page_details(page, limit) + _term_details(term)
        if status is not None and status not in {s.value for s in EnrollmentStatus}:
            details.append(FieldError("status", "must be one of CONFIRMED, REJECTED, CANCELLED"))
        _raise_if(details)
        items, total = self._enrollments.page_of(caller.user_id, term, status, page, limit)
        return Page(items, page, limit, total)

    def get_enrollment(self, caller: Caller, enrollment_id: UUID) -> Enrollment:
        _require(caller, Role.STUDENT, Role.PROFESSOR, Role.ADMIN, Role.SERVICE)
        enrollment = self._enrollments.find(enrollment_id)
        if enrollment is None:
            raise NotFound()
        if caller.role is Role.STUDENT and enrollment.student_id != caller.user_id:
            raise Forbidden("this enrollment belongs to another student")
        return enrollment

    def my_schedule(self, caller: Caller, term: str | None, page: int, limit: int) -> Page[Enrollment]:
        _require(caller, Role.STUDENT)
        _raise_if(_page_details(page, limit) + _term_details(term))
        items = self._enrollments.confirmed_of(caller.user_id, term or self._term)
        start = (page - 1) * limit
        return Page(items[start : start + limit], page, limit, len(items))

    def roster(self, caller: Caller, section_id: UUID, page: int, limit: int) -> Page[RosterItem]:
        _require(caller, Role.PROFESSOR, Role.ADMIN)
        _raise_if(_page_details(page, limit))
        if self._projections.section(section_id) is None:
            raise NotFound("section not found")
        rows, total = self._enrollments.roster(section_id, page, limit)
        return Page([RosterItem(e.id, e.student_id, e.student_name, e.created_at) for e in rows], page, limit, total)

    # --- certificates (EN-11..EN-14)
    def verify(self, code: str) -> Verification:
        found = self._certificates.find_by_code(code)
        return Verification(found is not None, found)

    def my_certificates(self, caller: Caller, page: int, limit: int) -> Page[Certificate]:
        _require(caller, Role.STUDENT)
        _raise_if(_page_details(page, limit))
        items, total = self._certificates.page_of(caller.user_id, page, limit)
        return Page(items, page, limit, total)

    def issue_certificate(self, caller: Caller, kind: CertificateType, idempotency_key: str) -> Created[Certificate]:
        _require(caller, Role.STUDENT)
        _check_key(idempotency_key)
        earlier = self._certificates.find_by_key(idempotency_key)
        if earlier is not None:
            return Created(earlier, created=False)
        record = self._record_of(caller)
        confirmed = self._enrollments.confirmed_of(record.student_id, self._term)
        paid = self._projections.tuition_paid(record.student_id, self._term)
        ensure_preconditions(kind, paid, len(confirmed), record.is_empty)
        certificate = issue(
            self._ids.new_id(), record.student_id, record.student_name, kind, self._term, self._clock.now()
        )
        subjects = self._projections.subjects()
        pensum = pensum_of(record, subjects, {e.subject_code for e in confirmed})
        context = CertificateContext(
            certificate, record, confirmed, {s.code: s.credits for s in subjects}, pensum.totals, paid
        )
        document = self._renderer.render(context)
        stored, created = self._certificates.create(
            certificate, document, idempotency_key, certificate_issued(certificate)
        )
        return Created(stored, created)

    def certificate_document(self, caller: Caller, certificate_id: UUID) -> tuple[Certificate, bytes]:
        _require(caller, Role.STUDENT)
        certificate = self._certificates.find(certificate_id)
        document = self._certificates.document_of(certificate_id) if certificate else None
        if certificate is None or document is None or certificate.student_id != caller.user_id:
            raise NotFound()  # D-03: another student's certificate does not exist for this caller
        return certificate, document

    def _record_of(self, caller: Caller) -> AcademicRecord:
        record = self._records.find(caller.user_id)
        if record is not None:
            return record
        name = caller.name.strip() if len(caller.name.strip()) >= 2 else "Student"
        fresh = AcademicRecord(caller.user_id, name, self._program, 1, (), self._clock.now())
        return self._records.create(fresh)


def _require(caller: Caller, *roles: Role) -> None:
    if caller.role not in roles:
        raise Forbidden()


def _check_key(idempotency_key: str) -> None:
    if not KEY_MIN <= len(idempotency_key or "") <= KEY_MAX:
        raise ValidationError([FieldError("Idempotency-Key", "header required, 8 to 128 characters")])


def _page_details(page: int, limit: int) -> list[FieldError]:
    details: list[FieldError] = []
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        details.append(FieldError("page", "must be 1 or greater"))
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        details.append(FieldError("limit", f"must be between 1 and {MAX_LIMIT}"))
    return details


def _term_details(term: str | None) -> list[FieldError]:
    if term is not None and not TERM_PATTERN.match(term):
        return [FieldError("term", "must match ^[0-9]{4}-[AB]$")]
    return []


def _raise_if(details: list[FieldError]) -> None:
    if details:
        raise ValidationError(details)


def _duplicate(enrollment: Enrollment) -> BusinessRuleViolation:
    return BusinessRuleViolation(
        f"INV-ENR-001: the student already has an active enrollment in {enrollment.subject_code} for {enrollment.term}"
    )
