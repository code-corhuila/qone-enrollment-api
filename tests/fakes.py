"""Fakes of the trivial ports and a small seed (the demo's ids) for the core and HTTP tests."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from enrollment.adapter.outbound.persistence.memory import InMemoryEnrollment
from enrollment.domain.model.eligibility import SectionView
from enrollment.domain.model.enrollment import Enrollment, EnrollmentStatus, Origin
from enrollment.domain.model.record import AcademicRecord, SubjectView
from enrollment.domain.model.slot import Slot

SEEDED = datetime(2026, 8, 15, 9, 0, tzinfo=UTC)
TERM = "2026-B"
LAURA = UUID("11111111-1111-4111-8111-111111111111")
SANTIAGO = UUID("11111111-1111-4111-8111-111111111112")
MARIA = UUID("11111111-1111-4111-8111-111111111113")

SUBJECTS = [
    SubjectView("ISW-501", "Bases de Datos I", 3, 5, ()),
    SubjectView("RED-501", "Redes de Datos", 3, 5, ()),
    SubjectView("MAT-501", "Estadística II", 3, 5, ()),
    SubjectView("ISW-601", "Bases de Datos II", 3, 6, ("ISW-501",)),
    SubjectView("ISW-604", "Sistemas Distribuidos", 4, 6, ("RED-501",)),
    SubjectView("ISW-701", "Seguridad Informática", 3, 7, ("ISW-604",)),
]


def section(n: int, code: str, name: str, group: int, professor: str, seats: int, *slots) -> SectionView:
    return SectionView(
        UUID(f"55555555-5555-4555-8555-5555555555{n:02d}"),
        code,
        name,
        TERM,
        group,
        professor,
        tuple(Slot.parse(*s) for s in slots),
        seats,
    )


ISW_601_G1 = section(
    1, "ISW-601", "Bases de Datos II", 1, "Ana Torres", 29, ("TUE", "08:00", "10:00"), ("THU", "08:00", "10:00")
)
ISW_604_G1 = section(
    5, "ISW-604", "Sistemas Distribuidos", 1, "Carlos Ramírez", 27, ("MON", "08:00", "10:00"), ("WED", "08:00", "10:00")
)
ISW_604_G2 = section(
    6, "ISW-604", "Sistemas Distribuidos", 2, "Ana Torres", 30, ("TUE", "08:00", "10:00"), ("THU", "08:00", "10:00")
)
SECTIONS = [ISW_601_G1, ISW_604_G1, ISW_604_G2]

RECORDS = [
    AcademicRecord(LAURA, "Laura Gómez", "Ingeniería de Software", 6, ("ISW-501", "RED-501"), SEEDED),
    AcademicRecord(SANTIAGO, "Santiago Rojas", "Ingeniería de Software", 5, ("ISW-501",), SEEDED),
    AcademicRecord(MARIA, "María José Cabrera", "Ingeniería de Software", 6, ("ISW-501", "RED-501", "MAT-501"), SEEDED),
]
TUITION = {(LAURA, TERM): "PAID", (SANTIAGO, TERM): "ISSUED", (MARIA, TERM): "PAID"}

LAURA_ISW_601 = Enrollment(
    UUID("66666666-6666-4666-8666-666666666601"),
    LAURA,
    "Laura Gómez",
    TERM,
    "ISW-601",
    "Bases de Datos II",
    ISW_601_G1.section_id,
    1,
    ISW_601_G1.slots,
    EnrollmentStatus.CONFIRMED,
    None,
    Origin.UI,
    None,
    None,
    UUID("77777777-7777-4777-8777-777777777701"),
    SEEDED,
    SEEDED,
)
MARIA_ISW_604 = Enrollment(
    UUID("66666666-6666-4666-8666-666666666605"),
    MARIA,
    "María José Cabrera",
    TERM,
    "ISW-604",
    "Sistemas Distribuidos",
    ISW_604_G1.section_id,
    1,
    ISW_604_G1.slots,
    EnrollmentStatus.CONFIRMED,
    None,
    Origin.UI,
    None,
    None,
    UUID("77777777-7777-4777-8777-777777777705"),
    SEEDED,
    SEEDED,
)


def seeded_store() -> InMemoryEnrollment:
    return InMemoryEnrollment(
        list(RECORDS), list(SUBJECTS), list(SECTIONS), dict(TUITION), [LAURA_ISW_601, MARIA_ISW_604]
    )


class FixedClock:
    def __init__(self, start: datetime = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)) -> None:
        self.current = start

    def now(self) -> datetime:
        return self.current

    def tick(self, **delta) -> datetime:
        self.current = self.current + timedelta(**delta)
        return self.current


class SequenceIds:
    def __init__(self, start: int = 1) -> None:
        self.next = start

    def new_id(self) -> UUID:
        value = UUID(f"99999999-9999-4999-8999-9999999999{self.next:02d}")
        self.next += 1
        return value


class FakeRenderer:
    def __init__(self) -> None:
        self.rendered = []

    def render(self, context) -> bytes:
        self.rendered.append(context)
        return b"%PDF-fake " + context.certificate.code.encode()
