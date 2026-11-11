"""Integration: the projection store against qone-enrollment-db (TEST_DATABASE_URL)."""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
SANTIAGO = UUID("11111111-1111-4111-8111-111111111112")
G1 = "55555555-5555-4555-8555-555555555505"
AT = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def test_inbox_seats_and_tuition_with_late_events():
    from enrollment.adapter.outbound.persistence.postgres import PostgresProjections, connect
    from enrollment.adapter.outbound.persistence.projections import PostgresProjectionStore

    pool = connect(URL, max_size=2)
    store, read = PostgresProjectionStore(pool), PostgresProjections(pool)
    with pool.connection() as conn:
        before = conn.execute(
            "SELECT seats_available, updated_at FROM enrollment.catalog_section_view WHERE section_id = %s", (G1,)
        ).fetchone()
        tuition = conn.execute(
            "SELECT status, updated_at FROM enrollment.billing_tuition_view WHERE student_id = %s", (SANTIAGO,)
        ).fetchone()
    try:
        reserved = uuid4()
        assert store.apply(reserved, ("seats", {"sectionId": G1, "seatsAvailable": 25}), AT)
        assert not store.apply(reserved, ("seats", {"sectionId": G1, "seatsAvailable": 1}), AT), (
            "a repeated delivery is skipped"
        )
        assert (
            not store.apply(uuid4(), ("seats", {"sectionId": G1, "seatsAvailable": 9}), AT - timedelta(days=1))
            or read.section(UUID(G1)).seats_available == 25
        )
        assert read.section(UUID(G1)).seats_available == 25, "a late event never overwrites a newer state"
        store.apply(uuid4(), ("tuition", {"studentId": str(SANTIAGO), "term": "2026-B", "status": "PAID"}), AT)
        assert read.tuition_paid(SANTIAGO, "2026-B")
        store.apply(
            uuid4(),
            ("tuition", {"studentId": str(SANTIAGO), "term": "2026-B", "status": "ISSUED"}),
            AT + timedelta(days=1),
        )
        assert read.tuition_paid(SANTIAGO, "2026-B"), "a PAID tuition never goes back"
    finally:
        with pool.connection() as conn:
            conn.execute(
                "UPDATE enrollment.catalog_section_view SET seats_available = %s, updated_at = %s WHERE "
                "section_id = %s",
                (*before.values(), G1),
            )
            conn.execute(
                "UPDATE enrollment.billing_tuition_view SET status = %s, updated_at = %s WHERE student_id = %s",
                (*tuition.values(), SANTIAGO),
            )
        pool.close()


def test_a_section_event_keeps_its_professor():
    from enrollment.adapter.outbound.persistence.postgres import PostgresProjections, connect
    from enrollment.adapter.outbound.persistence.projections import PostgresProjectionStore

    pool = connect(URL, max_size=2)
    store, read, section_id = PostgresProjectionStore(pool), PostgresProjections(pool), uuid4()
    ana = UUID("22222222-2222-4222-8222-222222222222")
    created = {
        "sectionId": str(section_id),
        "subjectCode": "ISW-604",
        "subjectName": "Sistemas Distribuidos",
        "term": "2026-B",
        "groupNumber": 9,
        "professorId": str(ana),
        "professorName": "Ana Torres",
        "slots": [{"day": "SAT", "start": "08:00", "end": "10:00"}],
        "seatsAvailable": 30,
    }
    try:
        assert store.apply(uuid4(), ("section", created), AT)
        assert read.section(section_id).taught_by(ana)
        assert store.apply(uuid4(), ("section", {**created, "professorId": None}), AT + timedelta(days=1))
        assert read.section(section_id).professor_id is None
    finally:
        with pool.connection() as conn:
            conn.execute("DELETE FROM enrollment.catalog_section_view WHERE section_id = %s", (section_id,))
        pool.close()
