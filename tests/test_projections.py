"""Core level: which change each event makes, the ignored types and the duplicate delivery; a
TuitionPaid without the provider's evidence is ignored (F5)."""

import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from enrollment.application.usecase.projections import IncomingEvent, ProjectEvents, change_of
from tests.http_support import LAURA, SANTIAGO, bearer, make_client

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
    paid = event("TuitionPaid", {"studentId": "u", "term": "2026-B", "providerReference": "SIM-1"})
    assert project.handle(paid, "corr") == "applied" and project.handle(paid, "corr") == "duplicate"
    assert project.handle(event("PlanProposed", {}), "corr") == "ignored" and len(store.changes) == 1


@pytest.mark.parametrize(
    "evidence", [{}, {"providerReference": None}, {"providerReference": ""}, {"providerReference": "  "}]
)
def test_a_tuition_paid_without_the_provider_reference_is_ignored_with_a_warning(evidence, caplog):
    """F5 (qone-docs#43): only the provider's approval makes a tuition PAID; the event is acked
    (ignored, not an error) and not applied."""
    store = Store()
    unproven = event("TuitionPaid", {"invoiceId": "i", "studentId": "u", "term": "2026-B", **evidence})
    with caplog.at_level(logging.WARNING, logger="enrollment.projections"):
        assert ProjectEvents(store).handle(unproven, "corr-f5") == "ignored"
    assert store.changes == [] and store.seen == set()
    warning = next(r for r in caplog.records if r.levelno == logging.WARNING)
    assert "providerReference" in warning.getMessage() and warning.traceId == "corr-f5"


class TuitionOf:
    """The projection store over the in-memory tuition of the HTTP tests."""

    def __init__(self, store) -> None:
        self.store = store

    def apply(self, event_id, change, occurred_at):
        kind, d = change
        assert kind == "tuition"
        self.store.tuition[(UUID(d["studentId"]), d["term"])] = d["status"]
        return True


def test_a_forged_tuition_paid_does_not_unlock_a_payment_certificate():
    """Two students over HTTP: Laura (PAID) gets her `pago` certificate; Santiago (ISSUED) stays
    without it after a TuitionPaid with no provider evidence, and gets it after the real one."""
    client, store = make_client()
    project = ProjectEvents(TuitionOf(store))
    laura, santiago = bearer(LAURA, "STUDENT", "Laura Gómez"), bearer(SANTIAGO, "STUDENT", "Santiago Rojas")

    def pago(who, key):
        return client.post(
            "/api/v1/enrollment/certificates", json={"type": "pago"}, headers={**who, "Idempotency-Key": key}
        )

    assert pago(laura, "pago-key-0001").status_code == 201
    forged = event("TuitionPaid", {"invoiceId": "i", "studentId": SANTIAGO, "term": "2026-B"})
    assert project.handle(forged, "corr") == "ignored"
    refused = pago(santiago, "pago-key-0002")
    assert refused.status_code == 422 and refused.json()["message"].startswith("INV-ENR-002")
    paid = event(
        "TuitionPaid", {"invoiceId": "i", "studentId": SANTIAGO, "term": "2026-B", "providerReference": "SIM-7"}
    )
    assert project.handle(paid, "corr") == "applied" and pago(santiago, "pago-key-0003").status_code == 201


def test_a_section_keeps_its_professor():
    created = {
        "sectionId": "s",
        "subjectId": "i",
        "subjectCode": "ISW-604",
        "subjectName": "Sistemas Distribuidos",
        "term": "2026-B",
        "groupNumber": 4,
        "professorId": "22222222-2222-4222-8222-222222222222",
        "professorName": "Ana Torres",
        "capacity": 30,
        "seatsAvailable": 30,
        "slots": [],
    }
    kind, change = change_of(event("SectionCreated", created))
    assert kind == "section" and change["professorId"] == "22222222-2222-4222-8222-222222222222"
    older = {k: v for k, v in created.items() if k != "professorId"}
    assert change_of(event("SectionCreated", older))[1]["professorId"] is None, "no professor: nobody's roster"
