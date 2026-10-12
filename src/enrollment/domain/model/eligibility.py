"""Can this student enroll in this section? The saga's step `check-eligibility` (EN-04). The
checks run in the contract's order and the first failure is the reason (D-01): an active
duplicate (INV-ENR-001), a missing prerequisite (INV-ENR-006), a schedule conflict with the
student's confirmed enrollments (INV-ENR-003). Seats and tuition are other domains' gates."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from enrollment.domain.model.slot import Slot


class Ineligibility(StrEnum):
    DUPLICATE = "DUPLICATE"
    PREREQ_MISSING = "PREREQ_MISSING"
    SCHEDULE_CONFLICT = "SCHEDULE_CONFLICT"


@dataclass(frozen=True)
class SectionView:
    """A section of the catalog as the projection keeps it."""

    section_id: UUID
    subject_code: str
    subject_name: str
    term: str
    group_number: int
    professor_name: str
    slots: tuple[Slot, ...]
    seats_available: int


@dataclass(frozen=True)
class ActiveEnrollment:
    section_id: UUID
    subject_code: str
    slots: tuple[Slot, ...]


@dataclass(frozen=True)
class Verdict:
    eligible: bool
    reason: Ineligibility | None = None
    missing_prerequisites: tuple[str, ...] = ()
    conflicting_section_id: UUID | None = None


def check_eligibility(
    section: SectionView, prerequisites: tuple[str, ...], passed_codes: set[str], active: list[ActiveEnrollment]
) -> Verdict:
    if any(a.subject_code == section.subject_code for a in active):
        return Verdict(False, Ineligibility.DUPLICATE)
    missing = tuple(p for p in prerequisites if p not in passed_codes)
    if missing:
        return Verdict(False, Ineligibility.PREREQ_MISSING, missing_prerequisites=missing)
    for other in active:
        if any(mine.overlaps(theirs) for mine in section.slots for theirs in other.slots):
            return Verdict(False, Ineligibility.SCHEDULE_CONFLICT, conflicting_section_id=other.section_id)
    return Verdict(True)
