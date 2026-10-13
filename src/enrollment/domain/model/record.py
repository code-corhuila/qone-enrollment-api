"""The academic record and the pensum (EN-02, EN-03).

The record is created on first access from the token (`sub`, `name`); the passed subjects are
append-only (INV-REC-001). The catalog is never read directly: the pensum is derived from the
projection of catalog subjects (D-11), the record and the student's confirmed enrollments.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


@dataclass(frozen=True)
class AcademicRecord:
    student_id: UUID
    student_name: str
    program: str
    semester: int
    passed_codes: tuple[str, ...]
    updated_at: datetime

    @property
    def is_empty(self) -> bool:
        return not self.passed_codes


@dataclass(frozen=True)
class SubjectView:
    """A subject of the catalog as the projection keeps it."""

    code: str
    name: str
    credits: int
    semester: int
    prerequisites: tuple[str, ...]


class SubjectState(StrEnum):
    PASSED = "PASSED"
    IN_PROGRESS = "IN_PROGRESS"
    AVAILABLE = "AVAILABLE"
    LOCKED = "LOCKED"


@dataclass(frozen=True)
class PensumSubject:
    subject: SubjectView
    state: SubjectState
    missing_prerequisites: tuple[str, ...]


@dataclass(frozen=True)
class PensumTotals:
    credits_total: int
    credits_passed: int
    credits_in_progress: int
    percent: int
    subjects_passed: int
    subjects_total: int


@dataclass(frozen=True)
class Pensum:
    record: AcademicRecord
    subjects: tuple[PensumSubject, ...]
    totals: PensumTotals


def pensum_of(record: AcademicRecord, subjects: list[SubjectView], in_progress_codes: set[str]) -> Pensum:
    """PASSED in the record; IN_PROGRESS confirmed this term; AVAILABLE when every prerequisite
    is passed (INV-ENR-006); LOCKED otherwise. Ordered by semester, then code."""
    passed = set(record.passed_codes)
    items: list[PensumSubject] = []
    for subject in sorted(subjects, key=lambda s: (s.semester, s.code)):
        missing = tuple(p for p in subject.prerequisites if p not in passed)
        if subject.code in passed:
            state = SubjectState.PASSED
        elif subject.code in in_progress_codes:
            state = SubjectState.IN_PROGRESS
        elif not missing:
            state = SubjectState.AVAILABLE
        else:
            state = SubjectState.LOCKED
        items.append(PensumSubject(subject, state, missing))
    total = sum(i.subject.credits for i in items)
    credits_passed = sum(i.subject.credits for i in items if i.state is SubjectState.PASSED)
    credits_in_progress = sum(i.subject.credits for i in items if i.state is SubjectState.IN_PROGRESS)
    totals = PensumTotals(
        credits_total=total,
        credits_passed=credits_passed,
        credits_in_progress=credits_in_progress,
        percent=round(credits_passed * 100 / total) if total else 0,
        subjects_passed=sum(1 for i in items if i.state is SubjectState.PASSED),
        subjects_total=len(items),
    )
    return Pensum(record, tuple(items), totals)
