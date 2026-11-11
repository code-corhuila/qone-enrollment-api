"""Keeps the projections of catalog and billing (D-11) from their events, consumed from RabbitMQ.
Idempotent: the event id goes to the inbox in the same transaction as the change, and a repeated
delivery is skipped. A late event never overwrites a newer state (the store compares updated_at).
Unknown event types are acknowledged and ignored: a new event in another domain must not stop
this consumer. A TuitionPaid without the provider's evidence (providerReference) is acknowledged,
logged at WARN and not applied: only the provider's approval makes a tuition PAID (qone-docs#43)."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

log = logging.getLogger("enrollment.projections")


@dataclass(frozen=True)
class IncomingEvent:
    event_id: UUID
    event_type: str
    payload: dict[str, Any]
    occurred_at: datetime


class ProjectionStore(Protocol):
    def apply(self, event_id: UUID, change: tuple[str, dict[str, Any]], occurred_at: datetime) -> bool:
        """Writes the inbox row and the change in one transaction; False when the event was already consumed."""


SEAT_EVENTS = {"SeatReserved", "SeatReleased", "SeatCommitted"}


def change_of(event: IncomingEvent) -> tuple[str, dict[str, Any]] | None:
    p = event.payload
    if event.event_type == "SubjectCreated":
        return "subject", {k: p[k] for k in ("subjectId", "code", "name", "credits", "semester", "prerequisites")}
    if event.event_type == "SectionCreated":
        keys = (
            "sectionId",
            "subjectCode",
            "subjectName",
            "term",
            "groupNumber",
            "professorName",
            "slots",
            "seatsAvailable",
        )
        # The professor decides who reads the roster (qone-docs#48); an event without it leaves the
        # section to nobody but ADMIN instead of failing the consumer.
        return "section", {**{k: p[k] for k in keys}, "professorId": p.get("professorId")}
    if event.event_type in SEAT_EVENTS:
        return "seats", {"sectionId": p["sectionId"], "seatsAvailable": p["seatsAvailable"]}
    if event.event_type == "InvoiceIssued":
        return "tuition", {"studentId": p["studentId"], "term": p["term"], "status": "ISSUED"}
    if event.event_type == "TuitionPaid":
        if not _has_provider_reference(p):
            return None
        return "tuition", {"studentId": p["studentId"], "term": p["term"], "status": "PAID"}
    return None


def _has_provider_reference(payload: dict[str, Any]) -> bool:
    reference = payload.get("providerReference")
    return isinstance(reference, str) and reference.strip() != ""


class ProjectEvents:
    def __init__(self, store: ProjectionStore) -> None:
        self._store = store

    def handle(self, event: IncomingEvent, correlation_id: str = "") -> str:
        change = change_of(event)
        if change is None:
            if event.event_type == "TuitionPaid":
                log.warning(
                    "TuitionPaid %s without providerReference ignored: no evidence of the provider's approval",
                    event.event_id,
                    extra={"traceId": correlation_id, "event": event.event_type},
                )
            return "ignored"
        applied = self._store.apply(event.event_id, change, event.occurred_at)
        log.info("event", extra={"traceId": correlation_id, "event": event.event_type, "applied": applied})
        return "applied" if applied else "duplicate"
