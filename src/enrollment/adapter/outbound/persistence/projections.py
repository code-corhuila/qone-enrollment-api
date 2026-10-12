"""The projection store over PostgreSQL: the inbox row and the change in one transaction; a late
event (older than the row's updated_at) changes nothing; a PAID tuition never goes back to ISSUED."""

import json
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg_pool import ConnectionPool


class PostgresProjectionStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def apply(self, event_id: UUID, change: tuple[str, dict[str, Any]], occurred_at: datetime) -> bool:
        kind, d = change
        with self._pool.connection() as conn, conn.transaction():
            fresh = conn.execute(
                "INSERT INTO enrollment.inbox (event_id) VALUES (%s) ON CONFLICT (event_id) DO NOTHING RETURNING "
                "event_id",
                (event_id,),
            ).fetchone()
            if fresh is None:
                return False
            getattr(self, f"_{kind}")(conn, d, occurred_at)
        return True

    @staticmethod
    def _subject(conn: psycopg.Connection, d: dict, at: datetime) -> None:
        conn.execute(
            "INSERT INTO enrollment.catalog_subject_view (subject_id, code, name, credits, semester, prerequisites, "
            "updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (subject_id) DO UPDATE SET code = EXCLUDED.code, "
            "name = EXCLUDED.name, credits = EXCLUDED.credits, semester = EXCLUDED.semester, "
            "prerequisites = EXCLUDED.prerequisites, updated_at = EXCLUDED.updated_at "
            "WHERE enrollment.catalog_subject_view.updated_at <= EXCLUDED.updated_at",
            (d["subjectId"], d["code"], d["name"], d["credits"], d["semester"], d["prerequisites"], at),
        )

    @staticmethod
    def _section(conn: psycopg.Connection, d: dict, at: datetime) -> None:
        conn.execute(
            "INSERT INTO enrollment.catalog_section_view (section_id, subject_code, subject_name, term, group_number, "
            "professor_name, slots, seats_available, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (section_id) DO UPDATE SET subject_code = EXCLUDED.subject_code, subject_name = "
            "EXCLUDED.subject_name, "
            "term = EXCLUDED.term, group_number = EXCLUDED.group_number, professor_name = EXCLUDED.professor_name, "
            "slots = EXCLUDED.slots, seats_available = EXCLUDED.seats_available, updated_at = EXCLUDED.updated_at "
            "WHERE enrollment.catalog_section_view.updated_at <= EXCLUDED.updated_at",
            (
                d["sectionId"],
                d["subjectCode"],
                d["subjectName"],
                d["term"],
                d["groupNumber"],
                d["professorName"],
                json.dumps(d["slots"]),
                d["seatsAvailable"],
                at,
            ),
        )

    @staticmethod
    def _seats(conn: psycopg.Connection, d: dict, at: datetime) -> None:
        conn.execute(
            "UPDATE enrollment.catalog_section_view SET seats_available = %s, updated_at = %s "
            "WHERE section_id = %s AND updated_at <= %s",
            (d["seatsAvailable"], at, d["sectionId"], at),
        )

    @staticmethod
    def _tuition(conn: psycopg.Connection, d: dict, at: datetime) -> None:
        conn.execute(
            "INSERT INTO enrollment.billing_tuition_view (student_id, term, status, updated_at) VALUES (%s, %s, %s, "
            "%s) "
            "ON CONFLICT (student_id, term) DO UPDATE SET status = EXCLUDED.status, updated_at = EXCLUDED.updated_at "
            "WHERE enrollment.billing_tuition_view.status <> 'PAID' AND enrollment.billing_tuition_view.updated_at <= "
            "EXCLUDED.updated_at",
            (d["studentId"], d["term"], d["status"], at),
        )
