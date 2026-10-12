"""Core level: which change each event makes, the ignored types and the duplicate delivery."""

from datetime import UTC, datetime
from uuid import uuid4

from enrollment.application.usecase.projections import IncomingEvent, ProjectEvents, change_of

AT = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def event(event_type, payload):
    return IncomingEvent(uuid4(), event_type, payload, AT)


def test_each_event_maps_to_its_projection_change():
    assert change_of(event("SeatReserved", {"sectionId": "s", "seatsAvailable": 26, "x": 1})) == (
        "seats",
        {"sectionId": "s", "seatsAvailable": 26},
    )
    assert (
        change_of(event("TuitionPaid", {"studentId": "u", "term": "2026-B", "providerReference": "SIM-1"}))[1]["status"]
        == "PAID"
    )
    assert (
        change_of(event("InvoiceIssued", {"studentId": "u", "term": "2026-B", "amountCents": 1}))[1]["status"]
        == "ISSUED"
    )
    subject = {"subjectId": "i", "code": "ISW-801", "name": "C", "credits": 3, "semester": 8, "prerequisites": []}
    assert change_of(event("SubjectCreated", subject)) == ("subject", subject)
    assert change_of(event("EnrollmentConfirmed", {})) is None


class Store:
    def __init__(self):
        self.seen, self.changes = set(), []

    def apply(self, event_id, change, occurred_at):
        if event_id in self.seen:
            return False
        self.seen.add(event_id)
        self.changes.append(change)
        return True


def test_a_repeated_delivery_changes_nothing_and_unknown_types_are_ignored():
    store = Store()
    project = ProjectEvents(store)
    paid = event("TuitionPaid", {"studentId": "u", "term": "2026-B"})
    assert project.handle(paid, "corr") == "applied" and project.handle(paid, "corr") == "duplicate"
    assert project.handle(event("PlanProposed", {}), "corr") == "ignored" and len(store.changes) == 1
