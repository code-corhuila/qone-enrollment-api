"""Core level: the Enrollment aggregate, the certificate preconditions and the events."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from enrollment.domain.model.certificate import CertificateType, ensure_preconditions, issue, new_code
from enrollment.domain.model.eligibility import SectionView
from enrollment.domain.model.enrollment import Enrollment, EnrollmentStatus, Origin, RejectionReason
from enrollment.domain.model.errors import BusinessRuleViolation, InvalidStatusTransition, ValidationError
from enrollment.domain.model.events import certificate_issued, enrollment_event
from enrollment.domain.model.slot import Slot

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
SECTION = SectionView(
    uuid4(),
    "ISW-604",
    "Sistemas Distribuidos",
    "2026-B",
    1,
    "Carlos Ramírez",
    (Slot.parse("MON", "08:00", "10:00"),),
    27,
)


def record(**kw):
    args = dict(
        enrollment_id=uuid4(),
        student_id=uuid4(),
        student_name="Laura Gómez",
        section=SECTION,
        saga_id=uuid4(),
        origin=Origin.UI,
        outcome=EnrollmentStatus.CONFIRMED,
        now=NOW,
        reservation_id=uuid4(),
    )
    args.update(kw)
    return Enrollment.record(**args)


def test_a_confirmed_enrollment_snapshots_the_section():
    e = record()
    assert (
        e.status is EnrollmentStatus.CONFIRMED
        and e.subject_code == "ISW-604"
        and e.group_number == 1
        and e.slots == SECTION.slots
    )
    assert enrollment_event(e).event_type == "EnrollmentConfirmed"


def test_a_rejected_attempt_is_recorded_with_its_reason():
    e = record(outcome=EnrollmentStatus.REJECTED, reservation_id=None, rejection_reason=RejectionReason.NO_SEATS)
    assert e.rejection_reason is RejectionReason.NO_SEATS and e.reservation_id is None
    assert enrollment_event(e).payload["rejectionReason"] == "NO_SEATS"


@pytest.mark.parametrize(
    ("kw", "fields"),
    [
        ({"reservation_id": None}, ["reservationId"]),
        ({"outcome": EnrollmentStatus.REJECTED, "reservation_id": None}, ["rejectionReason"]),
        ({"rejection_reason": RejectionReason.NO_SEATS}, ["rejectionReason"]),
        ({"origin": Origin.AGENT}, ["confirmationId"]),
        ({"confirmation_id": uuid4()}, ["confirmationId"]),
        ({"outcome": EnrollmentStatus.CANCELLED}, ["outcome"]),
    ],
)
def test_the_outcome_rules(kw, fields):
    with pytest.raises(ValidationError) as caught:
        record(**kw)
    assert [d.field for d in caught.value.details] == fields


def test_an_agent_enrollment_carries_the_confirmation():
    assert record(origin=Origin.AGENT, confirmation_id=uuid4()).origin is Origin.AGENT


def test_cancel_once_then_unchanged_and_a_rejected_one_refuses():
    cancelled, changed = record().cancel(NOW + timedelta(minutes=1))
    assert changed and cancelled.status is EnrollmentStatus.CANCELLED
    assert cancelled.cancel(NOW + timedelta(minutes=2)) == (cancelled, False)
    rejected = record(
        outcome=EnrollmentStatus.REJECTED, reservation_id=None, rejection_reason=RejectionReason.TUITION_UNPAID
    )
    with pytest.raises(InvalidStatusTransition):
        rejected.cancel(NOW)


@pytest.mark.parametrize(
    ("kind", "paid", "confirmed", "empty", "message"),
    [
        (CertificateType.PAGO, False, 1, False, "INV-ENR-002"),
        (CertificateType.PAZ_Y_SALVO, False, 1, False, "INV-ENR-002"),
        (CertificateType.MATRICULA, True, 0, False, "no confirmed enrollment"),
        (CertificateType.AVANCE, True, 1, True, "no passed subjects"),
    ],
)
def test_certificate_preconditions(kind, paid, confirmed, empty, message):
    with pytest.raises(BusinessRuleViolation) as caught:
        ensure_preconditions(kind, paid, confirmed, empty)
    assert message in str(caught.value)


def test_estudios_has_no_precondition_and_the_code_has_the_contract_shape():
    ensure_preconditions(CertificateType.ESTUDIOS, False, 0, True)
    certificate = issue(uuid4(), uuid4(), "Laura Gómez", CertificateType.ESTUDIOS, "2026-B", NOW)
    assert certificate.code.startswith("QMP-") and len(certificate.code) == 12 and certificate.code[4:].isalnum()
    assert new_code() != new_code()
    assert certificate_issued(certificate).payload["code"] == certificate.code
