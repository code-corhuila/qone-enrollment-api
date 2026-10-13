"""Integration level (Annex C): the real engine with the schema and seeds of qone-enrollment-db,
through TEST_DATABASE_URL. Skipped, not failed, without it; leaves the seed as it found it."""

import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from enrollment.application.port.outbound.ports import DuplicateActive
from enrollment.domain.model.certificate import CertificateType, issue
from enrollment.domain.model.enrollment import Enrollment, EnrollmentStatus, Origin
from enrollment.domain.model.events import certificate_issued, enrollment_event
from enrollment.domain.model.record import AcademicRecord

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
LAURA = UUID("11111111-1111-4111-8111-111111111111")
SANTIAGO = UUID("11111111-1111-4111-8111-111111111112")
ISW_604_G1 = UUID("55555555-5555-4555-8555-555555555505")


@pytest.fixture(scope="module")
def pool():
    from enrollment.adapter.outbound.persistence.postgres import connect

    pool = connect(URL, max_size=2)
    yield pool
    pool.close()


def reset(pool):
    with pool.connection() as conn:
        conn.execute("TRUNCATE enrollment.idempotency_key, enrollment.outbox, enrollment.certificate")
        conn.execute("DELETE FROM enrollment.enrollment_slot WHERE enrollment_id::text NOT LIKE '66666666-%%'")
        conn.execute("DELETE FROM enrollment.enrollment WHERE id::text NOT LIKE '66666666-%%'")
        conn.execute("DELETE FROM enrollment.academic_record WHERE student_name = 'Integration Student'")


@pytest.fixture
def stores(pool):
    from enrollment.adapter.outbound.persistence.postgres import (
        PostgresCertificateStore,
        PostgresEnrollmentStore,
        PostgresProjections,
        PostgresRecordStore,
    )

    reset(pool)
    yield (
        PostgresRecordStore(pool),
        PostgresProjections(pool),
        PostgresEnrollmentStore(pool),
        PostgresCertificateStore(pool),
    )
    reset(pool)


def test_the_seed_reads_back_through_the_record_and_the_projections(stores):
    records, projections, enrollments, _ = stores
    laura = records.find(LAURA)
    assert laura.student_name == "Laura Gómez" and laura.passed_codes == ("HUM-501", "ISW-501", "ISW-502", "RED-501")
    assert projections.subject("ISW-604").prerequisites == ("RED-501",) and len(projections.subjects()) == 14
    section = projections.section(ISW_604_G1)
    assert section.subject_code == "ISW-604" and [s.day.value for s in section.slots] == ["MON", "WED"]
    assert projections.tuition_paid(LAURA, "2026-B") and not projections.tuition_paid(SANTIAGO, "2026-B")
    assert [e.subject_code for e in enrollments.confirmed_of(LAURA, "2026-B")] == [
        "ISW-601",
        "ISW-603",
        "ISW-605",
        "MAT-501",
    ]
    roster, total = enrollments.roster(ISW_604_G1, 1, 20)
    assert total == 3 and [e.student_name for e in roster][0] == "Andrés Pineda"


def test_a_record_is_created_once(stores):
    records, *_ = stores
    fresh = AcademicRecord(uuid4(), "Integration Student", "Ingeniería de Software", 1, (), NOW)
    assert records.create(fresh).student_id == fresh.student_id
    assert records.create(fresh).passed_codes == ()


def test_an_enrollment_once_per_key_inv_enr_001_and_cancel(stores, pool):
    _, projections, enrollments, _ = stores
    section = projections.section(ISW_604_G1)
    e = Enrollment.record(
        uuid4(),
        LAURA,
        "Laura Gómez",
        section,
        uuid4(),
        Origin.UI,
        EnrollmentStatus.CONFIRMED,
        NOW,
        reservation_id=uuid4(),
    )
    stored, created = enrollments.create(e, "it-enroll-key-1", enrollment_event(e))
    assert created and enrollments.find(e.id).slots == section.slots
    retry = Enrollment.record(
        uuid4(),
        LAURA,
        "Laura Gómez",
        section,
        e.saga_id,
        Origin.UI,
        EnrollmentStatus.CONFIRMED,
        NOW,
        reservation_id=uuid4(),
    )
    assert enrollments.create(retry, "it-enroll-key-1", enrollment_event(retry)) == (stored, False)
    again = Enrollment.record(
        uuid4(),
        LAURA,
        "Laura Gómez",
        section,
        uuid4(),
        Origin.UI,
        EnrollmentStatus.CONFIRMED,
        NOW,
        reservation_id=uuid4(),
    )
    with pytest.raises(DuplicateActive):
        enrollments.create(again, "it-enroll-key-2", enrollment_event(again))
    cancelled, _ = e.cancel(NOW)
    enrollments.save_cancelled(cancelled, enrollment_event(cancelled))
    assert enrollments.find(e.id).status is EnrollmentStatus.CANCELLED
    with pool.connection() as conn:
        events = [
            r["event_type"]
            for r in conn.execute(
                "SELECT event_type FROM enrollment.outbox ORDER BY occurred_at, event_type"
            ).fetchall()
        ]
    assert sorted(events) == ["EnrollmentCancelled", "EnrollmentConfirmed"]
    page, total = enrollments.page_of(LAURA, "2026-B", "CANCELLED", 1, 20)
    assert total == 1 and page[0].id == e.id


def test_a_certificate_keeps_its_document_and_is_found_by_code(stores):
    *_, certificates = stores
    c = issue(uuid4(), LAURA, "Laura Gómez", CertificateType.ESTUDIOS, "2026-B", NOW)
    stored, created = certificates.create(c, b"%PDF-1.4 test", "it-cert-key-1", certificate_issued(c))
    assert created and certificates.find_by_code(c.code) == c and certificates.document_of(c.id) == b"%PDF-1.4 test"
    retry = issue(uuid4(), LAURA, "Laura Gómez", CertificateType.ESTUDIOS, "2026-B", NOW)
    assert certificates.create(retry, b"other", "it-cert-key-1", certificate_issued(retry)) == (stored, False)
    assert certificates.page_of(LAURA, 1, 20) == ([c], 1)
