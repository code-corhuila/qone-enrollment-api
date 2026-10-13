"""Core level (Annex C): the use cases over the in-memory adapter and fakes of the ports."""

from uuid import UUID, uuid4

import pytest

from enrollment.application.errors import Forbidden, NotFound
from enrollment.application.port.inbound.caller import Caller, Role
from enrollment.application.port.inbound.enrollment import RecordEnrollment
from enrollment.application.usecase.enrollment import EnrollmentService
from enrollment.domain.model.certificate import CertificateType
from enrollment.domain.model.eligibility import Ineligibility
from enrollment.domain.model.enrollment import EnrollmentStatus, Origin, RejectionReason
from enrollment.domain.model.errors import BusinessRuleViolation, InvalidStatusTransition, ValidationError
from enrollment.domain.model.record import SubjectState
from tests.fakes import (
    ISW_601_G1,
    ISW_604_G1,
    ISW_604_G2,
    LAURA,
    LAURA_ISW_601,
    MARIA,
    SANTIAGO,
    FakeRenderer,
    FixedClock,
    SequenceIds,
    seeded_store,
)

laura = Caller(LAURA, Role.STUDENT, "Laura Gómez", str(LAURA))
santiago = Caller(SANTIAGO, Role.STUDENT, "Santiago Rojas", str(SANTIAGO))
carlos = Caller(UUID("22222222-2222-4222-8222-222222222221"), Role.PROFESSOR, "Carlos Ramírez")
admin = Caller(UUID("33333333-3333-4333-8333-333333333331"), Role.ADMIN, "Patricia Mora")
saga = Caller(None, Role.SERVICE, "qone-workflow", "qone-workflow")


@pytest.fixture
def world():
    store = seeded_store()
    renderer = FakeRenderer()
    records, projections, enrollments, certificates = store.as_ports()
    service = EnrollmentService(
        records,
        projections,
        enrollments,
        certificates,
        renderer,
        FixedClock(),
        SequenceIds(),
        "2026-B",
        "Ingeniería de Software",
    )
    return store, renderer, service


def confirm(service, student=LAURA, section=ISW_604_G1, key="saga-1:confirm-enrollment"):
    command = RecordEnrollment(
        student, section.section_id, uuid4(), Origin.UI, EnrollmentStatus.CONFIRMED, reservation_id=uuid4()
    )
    return service.record(saga, command, key)


class TestRecordAndPensum:
    def test_the_record_exists_or_is_created_on_first_access(self, world):
        store, _, service = world
        assert service.my_record(laura).passed_codes == ("ISW-501", "RED-501")
        newcomer = Caller(uuid4(), Role.STUDENT, "Nuevo Estudiante")
        record = service.my_record(newcomer)
        assert (
            record.student_name == "Nuevo Estudiante"
            and record.semester == 1
            and record.program == "Ingeniería de Software"
        )
        assert store.find_record(newcomer.user_id) == record

    def test_the_pensum_uses_the_projection_and_the_confirmed_enrollments(self, world):
        _, _, service = world
        states = {p.subject.code: p.state for p in service.my_pensum(laura).subjects}
        assert states["ISW-601"] is SubjectState.IN_PROGRESS and states["ISW-604"] is SubjectState.AVAILABLE
        assert states["ISW-701"] is SubjectState.LOCKED

    @pytest.mark.parametrize("caller", [carlos, admin, saga])
    def test_only_a_student_has_a_record(self, world, caller):
        _, _, service = world
        with pytest.raises(Forbidden):
            service.my_pensum(caller)


class TestSagaSteps:
    def test_eligibility_verdicts(self, world):
        _, _, service = world
        assert service.check_eligibility(saga, LAURA, ISW_604_G1.section_id).eligible
        assert service.check_eligibility(saga, SANTIAGO, ISW_604_G1.section_id).reason is Ineligibility.PREREQ_MISSING
        assert service.check_eligibility(saga, MARIA, ISW_604_G2.section_id).reason is Ineligibility.DUPLICATE
        conflict = service.check_eligibility(saga, LAURA, ISW_604_G2.section_id)
        assert (
            conflict.reason is Ineligibility.SCHEDULE_CONFLICT
            and conflict.conflicting_section_id == ISW_601_G1.section_id
        )
        with pytest.raises(NotFound):
            service.check_eligibility(saga, LAURA, uuid4())
        with pytest.raises(Forbidden):
            service.check_eligibility(laura, LAURA, ISW_604_G1.section_id)

    def test_record_a_confirmed_enrollment_once_with_its_event(self, world):
        store, _, service = world
        result = confirm(service)
        assert (
            result.created
            and result.item.subject_name == "Sistemas Distribuidos"
            and result.item.student_name == "Laura Gómez"
        )
        assert store.outbox[-1].event_type == "EnrollmentConfirmed"
        assert confirm(service).created is False
        with pytest.raises(BusinessRuleViolation) as caught:
            confirm(service, key="saga-2:confirm-enrollment")
        assert str(caught.value).startswith("INV-ENR-001")

    def test_record_a_rejected_attempt_and_the_outcome_rules(self, world):
        store, _, service = world
        command = RecordEnrollment(
            SANTIAGO,
            ISW_604_G1.section_id,
            uuid4(),
            Origin.UI,
            EnrollmentStatus.REJECTED,
            rejection_reason=RejectionReason.TUITION_UNPAID,
        )
        rejected = service.record(saga, command, "saga-3:confirm-enrollment").item
        assert (
            rejected.rejection_reason is RejectionReason.TUITION_UNPAID
            and store.outbox[-1].event_type == "EnrollmentRejected"
        )
        agent = RecordEnrollment(
            LAURA, ISW_604_G1.section_id, uuid4(), Origin.AGENT, EnrollmentStatus.CONFIRMED, reservation_id=uuid4()
        )
        with pytest.raises(ValidationError) as caught:
            service.record(saga, agent, "saga-4:confirm-enrollment")
        assert caught.value.details[0].field == "confirmationId"
        with pytest.raises(NotFound):
            service.record(
                saga,
                RecordEnrollment(
                    LAURA, uuid4(), uuid4(), Origin.UI, EnrollmentStatus.CONFIRMED, reservation_id=uuid4()
                ),
                "saga-5:confirm-enrollment",
            )
        with pytest.raises(ValidationError):
            service.record(saga, command, "short")

    def test_cancel_is_the_compensation_once_and_a_rejected_one_refuses(self, world):
        store, _, service = world
        confirmed = confirm(service).item
        cancelled = service.cancel(saga, confirmed.id)
        assert cancelled.status is EnrollmentStatus.CANCELLED and store.outbox[-1].event_type == "EnrollmentCancelled"
        assert service.cancel(saga, confirmed.id) == cancelled and len(store.outbox) == 2
        command = RecordEnrollment(
            SANTIAGO,
            ISW_604_G1.section_id,
            uuid4(),
            Origin.UI,
            EnrollmentStatus.REJECTED,
            rejection_reason=RejectionReason.NO_SEATS,
        )
        rejected = service.record(saga, command, "saga-6:confirm-enrollment").item
        with pytest.raises(InvalidStatusTransition):
            service.cancel(saga, rejected.id)
        with pytest.raises(Forbidden):
            service.cancel(admin, confirmed.id)


class TestReads:
    def test_my_enrollments_schedule_and_one_enrollment(self, world):
        _, _, service = world
        confirm(service)
        page = service.my_enrollments(laura, "2026-B", None, 1, 20)
        assert page.total == 2 and page.items[0].subject_code == "ISW-604"
        assert service.my_enrollments(laura, None, "REJECTED", 1, 20).total == 0
        with pytest.raises(ValidationError) as caught:
            service.my_enrollments(laura, "2026", "DONE", 0, 20)
        assert [d.field for d in caught.value.details] == ["page", "term", "status"]
        assert [e.subject_code for e in service.my_schedule(laura, None, 1, 20).items] == ["ISW-601", "ISW-604"]
        assert service.get_enrollment(laura, LAURA_ISW_601.id) == LAURA_ISW_601
        for caller in (carlos, admin, saga):
            assert service.get_enrollment(caller, LAURA_ISW_601.id).id == LAURA_ISW_601.id
        with pytest.raises(Forbidden):
            service.get_enrollment(santiago, LAURA_ISW_601.id)

    def test_the_roster_for_professor_and_admin(self, world):
        _, _, service = world
        confirm(service)
        roster = service.roster(carlos, ISW_604_G1.section_id, 1, 20)
        assert [r.student_name for r in roster.items] == ["Laura Gómez", "María José Cabrera"]
        assert service.roster(admin, ISW_604_G1.section_id, 1, 20).total == 2
        with pytest.raises(NotFound):
            service.roster(carlos, uuid4(), 1, 20)
        with pytest.raises(Forbidden):
            service.roster(laura, ISW_604_G1.section_id, 1, 20)


class TestCertificates:
    def test_issue_once_render_once_verify_and_download(self, world):
        store, renderer, service = world
        result = service.issue_certificate(laura, CertificateType.MATRICULA, "cert-key-0001")
        certificate = result.item
        assert (
            result.created
            and certificate.code.startswith("QMP-")
            and store.outbox[-1].event_type == "CertificateIssued"
        )
        assert renderer.rendered[0].confirmed[0].subject_code == "ISW-601" and renderer.rendered[0].tuition_paid
        assert (
            service.issue_certificate(laura, CertificateType.MATRICULA, "cert-key-0001").created is False
            and len(renderer.rendered) == 1
        )
        assert service.verify(certificate.code).valid and not service.verify("QMP-00000000").valid
        assert service.my_certificates(laura, 1, 20).total == 1
        found, document = service.certificate_document(laura, certificate.id)
        assert found == certificate and document.startswith(b"%PDF")
        with pytest.raises(NotFound):
            service.certificate_document(santiago, certificate.id)

    def test_preconditions(self, world):
        _, _, service = world
        with pytest.raises(BusinessRuleViolation) as caught:
            service.issue_certificate(santiago, CertificateType.PAGO, "cert-key-0002")
        assert str(caught.value).startswith("INV-ENR-002")
        with pytest.raises(BusinessRuleViolation):
            service.issue_certificate(santiago, CertificateType.MATRICULA, "cert-key-0003")
        assert service.issue_certificate(santiago, CertificateType.ESTUDIOS, "cert-key-0004").created
        with pytest.raises(Forbidden):
            service.issue_certificate(admin, CertificateType.ESTUDIOS, "cert-key-0005")
