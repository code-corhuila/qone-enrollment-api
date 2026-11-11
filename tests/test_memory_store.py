"""The in-memory adapter keeps the semantics the PostgreSQL adapter must keep."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from enrollment.application.port.outbound.ports import DuplicateActive, IdempotencyKey, Keyed, KeyTaken
from enrollment.domain.model.certificate import CertificateType, issue
from enrollment.domain.model.enrollment import Enrollment, EnrollmentStatus, Origin
from enrollment.domain.model.events import certificate_issued, enrollment_event
from tests.fakes import ISW_601_G1, ISW_604_G1, LAURA, MARIA, SANTIAGO, TERM, seeded_store

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
SAGA = "qone-workflow"


def key(value, owner=SAGA, request_hash="a" * 64):
    return IdempotencyKey(owner, value, request_hash)


def confirmed(student, section):
    return Enrollment.record(
        uuid4(),
        student,
        "Someone",
        section,
        uuid4(),
        Origin.UI,
        EnrollmentStatus.CONFIRMED,
        NOW,
        reservation_id=uuid4(),
    )


def test_projections_and_records():
    store = seeded_store()
    assert store.section(ISW_604_G1.section_id).seats_available == 27 and store.subject("ISW-604").prerequisites == (
        "RED-501",
    )
    assert store.tuition_paid(LAURA, TERM) and not store.tuition_paid(SANTIAGO, TERM)
    assert store.find_record(LAURA).student_name == "Laura Gómez"


def test_a_key_is_used_once_and_one_confirmed_per_subject_and_term():
    store = seeded_store()
    e = confirmed(LAURA, ISW_604_G1)
    store.create_enrollment(e, key("saga-1:confirm"), enrollment_event(e))
    assert store.find_enrollment_by_key(SAGA, "saga-1:confirm") == Keyed(e, "a" * 64)
    with pytest.raises(KeyTaken):
        store.create_enrollment(confirmed(LAURA, ISW_604_G1), key("saga-1:confirm"), enrollment_event(e))
    with pytest.raises(DuplicateActive):
        store.create_enrollment(confirmed(MARIA, ISW_604_G1), key("saga-2:confirm"), enrollment_event(e))
    assert store.find_enrollment_by_key("qone-advisor-api", "saga-1:confirm") is None
    assert [x.subject_code for x in store.confirmed_of(LAURA, TERM)] == ["ISW-601", "ISW-604"]
    assert {x.student_id for x in store.roster(ISW_604_G1.section_id, 1, 20)[0]} == {LAURA, MARIA}


def test_certificates_keep_their_document_and_code():
    store = seeded_store()
    c = issue(uuid4(), LAURA, "Laura Gómez", CertificateType.ESTUDIOS, TERM, NOW)
    store.create_certificate(c, b"%PDF", key("cert-key-0001", str(LAURA)), certificate_issued(c))
    his = issue(uuid4(), SANTIAGO, "Santiago Rojas", CertificateType.ESTUDIOS, TERM, NOW)
    store.create_certificate(his, b"%PDF", key("cert-key-0001", str(SANTIAGO)), certificate_issued(his))
    assert store.find_certificate_by_key(str(SANTIAGO), "cert-key-0001").item == his
    assert store.find_certificate_by_key(str(LAURA), "cert-key-0001").item == c
    assert store.find_enrollment_by_key(str(LAURA), "cert-key-0001") is None
    assert store.find_by_code(c.code) == c and store.document_of(c.id) == b"%PDF"
    assert store.certificate_page(LAURA, 1, 20) == ([c], 1)
    assert store.section(ISW_601_G1.section_id) is not None
