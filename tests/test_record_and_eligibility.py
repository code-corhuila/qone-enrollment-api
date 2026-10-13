"""Core level: the pensum and the eligibility verdicts, no database."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from enrollment.domain.model.eligibility import ActiveEnrollment, Ineligibility, SectionView, check_eligibility
from enrollment.domain.model.record import AcademicRecord, SubjectState, SubjectView, pensum_of
from enrollment.domain.model.slot import Slot

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
LAURA = AcademicRecord(
    UUID("11111111-1111-4111-8111-111111111111"),
    "Laura Gómez",
    "Ingeniería de Software",
    6,
    ("ISW-501", "RED-501"),
    NOW,
)
SUBJECTS = [
    SubjectView("ISW-501", "Bases de Datos I", 3, 5, ()),
    SubjectView("RED-501", "Redes de Datos", 3, 5, ()),
    SubjectView("ISW-601", "Bases de Datos II", 3, 6, ("ISW-501",)),
    SubjectView("ISW-604", "Sistemas Distribuidos", 4, 6, ("RED-501",)),
    SubjectView("ISW-701", "Seguridad Informática", 3, 7, ("ISW-604",)),
]


def test_the_pensum_states_and_totals():
    pensum = pensum_of(LAURA, SUBJECTS, {"ISW-601"})
    states = {p.subject.code: p.state for p in pensum.subjects}
    assert states == {
        "ISW-501": SubjectState.PASSED,
        "RED-501": SubjectState.PASSED,
        "ISW-601": SubjectState.IN_PROGRESS,
        "ISW-604": SubjectState.AVAILABLE,
        "ISW-701": SubjectState.LOCKED,
    }
    assert next(p for p in pensum.subjects if p.subject.code == "ISW-701").missing_prerequisites == ("ISW-604",)
    t = pensum.totals
    assert (
        t.credits_total,
        t.credits_passed,
        t.credits_in_progress,
        t.percent,
        t.subjects_passed,
        t.subjects_total,
    ) == (16, 6, 3, 38, 2, 5)


def test_an_empty_catalog_gives_zero_percent():
    assert pensum_of(LAURA, [], set()).totals.percent == 0


ISW_604_G1 = SectionView(
    uuid4(),
    "ISW-604",
    "Sistemas Distribuidos",
    "2026-B",
    1,
    "Carlos Ramírez",
    (Slot.parse("MON", "08:00", "10:00"),),
    27,
)


def active(code, *slots):
    return ActiveEnrollment(uuid4(), code, tuple(Slot.parse(*s) for s in slots))


def test_eligible_when_nothing_blocks():
    assert check_eligibility(
        ISW_604_G1, ("RED-501",), {"RED-501"}, [active("ISW-601", ("TUE", "08:00", "10:00"))]
    ).eligible


def test_the_first_failure_is_the_reason_in_the_contract_order():
    duplicate_and_conflict = [active("ISW-604", ("MON", "08:00", "10:00"))]
    verdict = check_eligibility(ISW_604_G1, ("RED-501",), set(), duplicate_and_conflict)
    assert verdict.reason is Ineligibility.DUPLICATE, "duplicate before prerequisites and schedule"
    verdict = check_eligibility(ISW_604_G1, ("RED-501",), set(), [active("ISW-601", ("MON", "09:00", "11:00"))])
    assert verdict.reason is Ineligibility.PREREQ_MISSING and verdict.missing_prerequisites == ("RED-501",)
    other = active("ISW-601", ("MON", "09:00", "11:00"))
    verdict = check_eligibility(ISW_604_G1, ("RED-501",), {"RED-501"}, [other])
    assert verdict.reason is Ineligibility.SCHEDULE_CONFLICT and verdict.conflicting_section_id == other.section_id


def test_ending_when_another_starts_is_not_a_conflict():
    assert check_eligibility(ISW_604_G1, (), set(), [active("ISW-601", ("MON", "10:00", "12:00"))]).eligible
