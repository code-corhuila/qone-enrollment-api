"""The stores over PostgreSQL (schema `enrollment` of qone-enrollment-db) with psycopg 3 and a
pool. Every creation writes the row, its Idempotency-Key and its event in ONE transaction (norm
5.3.8); the key is scoped to its owner, PRIMARY KEY (owner_id, key) since V018, and a key the
owner already used surfaces as KeyTaken; the partial unique index of INV-ENR-001 surfaces as
DuplicateActive. The projections are read here and kept by consumers."""

import threading
from concurrent.futures import Future
from datetime import UTC, time
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from enrollment.application.port.outbound.ports import DuplicateActive, IdempotencyKey, Keyed, KeyTaken
from enrollment.domain.model.certificate import Certificate, CertificateType
from enrollment.domain.model.eligibility import SectionView
from enrollment.domain.model.enrollment import Enrollment, EnrollmentStatus, Origin, RejectionReason
from enrollment.domain.model.events import DomainEvent
from enrollment.domain.model.record import AcademicRecord, SubjectView
from enrollment.domain.model.slot import Day, Slot

ENROLLMENT_COLUMNS = (
    "id, student_id, student_name, term, subject_code, subject_name, section_id, group_number, status, "
    "rejection_reason, "
    "origin, confirmation_id, reservation_id, saga_id, created_at, updated_at"
)
CERTIFICATE_COLUMNS = "id, student_id, student_name, type, code, term, issued_at"


def connect(
    url: str, max_size: int = 10, wait_seconds: float = 5.0, statement_timeout_ms: int = 5000
) -> ConnectionPool:
    return ConnectionPool(
        conninfo=url,
        min_size=1,
        max_size=max_size,
        timeout=wait_seconds,
        kwargs={"options": f"-c statement_timeout={statement_timeout_ms}", "row_factory": dict_row},
        open=True,
    )


def _claim_key(conn, key: IdempotencyKey, resource_type: str, resource_id: UUID) -> None:
    """Raises KeyTaken (rolling the transaction back) when the owner already used the key."""
    taken = conn.execute(
        "INSERT INTO enrollment.idempotency_key (owner_id, key, request_hash, resource_type, resource_id) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (owner_id, key) DO NOTHING RETURNING key",
        (key.owner, key.value, key.request_hash, resource_type, resource_id),
    ).fetchone()
    if taken is None:
        raise KeyTaken()


def _resource_of(conn, owner: str, key: str, resource_type: str) -> tuple[UUID, str | None] | None:
    row = conn.execute(
        "SELECT resource_id, request_hash FROM enrollment.idempotency_key "
        "WHERE owner_id = %s AND key = %s AND resource_type = %s",
        (owner, key, resource_type),
    ).fetchone()
    return (row["resource_id"], row["request_hash"]) if row else None


def _write_event(conn, event: DomainEvent) -> None:
    conn.execute(
        "INSERT INTO enrollment.outbox (aggregate_type, aggregate_id, event_type, payload, occurred_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (event.aggregate_type, event.aggregate_id, event.event_type, Jsonb(event.payload), event.occurred_at),
    )


def _utc(moment):
    return moment.astimezone(UTC)


def _time(value) -> time:
    return value if isinstance(value, time) else time.fromisoformat(str(value))


class PostgresRecordStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def find(self, student_id: UUID) -> AcademicRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT student_id, student_name, program, semester, updated_at FROM enrollment.academic_record WHERE "
                "student_id = %s",
                (student_id,),
            ).fetchone()
            if row is None:
                return None
            passed = conn.execute(
                "SELECT subject_code FROM enrollment.passed_subject WHERE student_id = %s ORDER BY subject_code",
                (student_id,),
            ).fetchall()
        return AcademicRecord(
            row["student_id"],
            row["student_name"],
            row["program"],
            row["semester"],
            tuple(p["subject_code"] for p in passed),
            _utc(row["updated_at"]),
        )

    def create(self, record: AcademicRecord) -> AcademicRecord:
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO enrollment.academic_record (student_id, student_name, program, semester, created_at, "
                "updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (student_id) DO NOTHING",
                (
                    record.student_id,
                    record.student_name,
                    record.program,
                    record.semester,
                    record.updated_at,
                    record.updated_at,
                ),
            )
        stored = self.find(record.student_id)
        assert stored is not None
        return stored


class PostgresProjections:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def subjects(self) -> list[SubjectView]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT code, name, credits, semester, prerequisites FROM enrollment.catalog_subject_view"
            ).fetchall()
        return [_subject(r) for r in rows]

    def subject(self, code: str) -> SubjectView | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT code, name, credits, semester, prerequisites FROM enrollment.catalog_subject_view WHERE code "
                "= %s",
                (code,),
            ).fetchone()
        return _subject(row) if row else None

    def section(self, section_id: UUID) -> SectionView | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT section_id, subject_code, subject_name, term, group_number, professor_name, slots, "
                "seats_available "
                "FROM enrollment.catalog_section_view WHERE section_id = %s",
                (section_id,),
            ).fetchone()
        if row is None:
            return None
        slots = tuple(sorted(Slot.parse(s["day"], s["start"], s["end"]) for s in row["slots"]))
        return SectionView(
            row["section_id"],
            row["subject_code"],
            row["subject_name"],
            row["term"],
            row["group_number"],
            row["professor_name"],
            slots,
            row["seats_available"],
        )

    def tuition_paid(self, student_id: UUID, term: str) -> bool:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT status FROM enrollment.billing_tuition_view WHERE student_id = %s AND term = %s",
                (student_id, term),
            ).fetchone()
        return bool(row) and row["status"] == "PAID"


def _subject(row: dict) -> SubjectView:
    return SubjectView(row["code"], row["name"], row["credits"], row["semester"], tuple(row["prerequisites"]))


class PostgresEnrollmentStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def find_by_key(self, owner: str, idempotency_key: str) -> Keyed[Enrollment] | None:
        with self._pool.connection() as conn:
            recorded = _resource_of(conn, owner, idempotency_key, "enrollment")
            enrollment = self._one(conn, recorded[0]) if recorded else None
        return Keyed(enrollment, recorded[1]) if recorded and enrollment else None

    def find(self, enrollment_id: UUID) -> Enrollment | None:
        with self._pool.connection() as conn:
            return self._one(conn, enrollment_id)

    def confirmed_of(self, student_id: UUID, term: str) -> list[Enrollment]:
        return self._many(
            "WHERE student_id = %s AND term = %s AND status = 'CONFIRMED' ORDER BY subject_code", (student_id, term)
        )

    def page_of(self, student_id: UUID, term, status, page: int, limit: int) -> tuple[list[Enrollment], int]:
        clauses, params = ["student_id = %s"], [student_id]
        for column, value in (("term", term), ("status", status)):
            if value is not None:
                clauses.append(f"{column} = %s")
                params.append(value)
        where = "WHERE " + " AND ".join(clauses)
        with self._pool.connection() as conn:
            total = conn.execute(f"SELECT count(*) AS n FROM enrollment.enrollment {where}", params).fetchone()["n"]
        rows = self._many(
            f"{where} ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s", (*params, limit, (page - 1) * limit)
        )
        return rows, int(total)

    def roster(self, section_id: UUID, page: int, limit: int) -> tuple[list[Enrollment], int]:
        where = "WHERE section_id = %s AND status = 'CONFIRMED'"
        with self._pool.connection() as conn:
            total = conn.execute(f"SELECT count(*) AS n FROM enrollment.enrollment {where}", (section_id,)).fetchone()[
                "n"
            ]
        rows = self._many(f"{where} ORDER BY student_name LIMIT %s OFFSET %s", (section_id, limit, (page - 1) * limit))
        return rows, int(total)

    def create(self, enrollment: Enrollment, key: IdempotencyKey, event: DomainEvent) -> None:
        with self._pool.connection() as conn, conn.transaction():
            # The key first: a retry of the same intention finds its first row (KeyTaken) instead
            # of tripping over the index of INV-ENR-001 that its own first attempt filled.
            _claim_key(conn, key, "enrollment", enrollment.id)
            try:
                conn.execute(
                    f"INSERT INTO enrollment.enrollment ({ENROLLMENT_COLUMNS}) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    _values(enrollment),
                )
            except psycopg.errors.UniqueViolation as cause:
                if cause.diag.constraint_name == "ux_enrollment_active":
                    raise DuplicateActive() from cause
                raise
            for slot in enrollment.slots:
                conn.execute(
                    "INSERT INTO enrollment.enrollment_slot (enrollment_id, day, start_time, end_time) VALUES "
                    "(%s, %s, %s, %s)",
                    (enrollment.id, slot.day.value, slot.start, slot.end),
                )
            _write_event(conn, event)

    def save_cancelled(self, enrollment: Enrollment, event: DomainEvent) -> None:
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                "UPDATE enrollment.enrollment SET status = %s, updated_at = %s WHERE id = %s",
                (enrollment.status.value, enrollment.updated_at, enrollment.id),
            )
            _write_event(conn, event)

    def _one(self, conn, enrollment_id: UUID) -> Enrollment | None:
        row = conn.execute(
            f"SELECT {ENROLLMENT_COLUMNS} FROM enrollment.enrollment WHERE id = %s", (enrollment_id,)
        ).fetchone()
        return _enrollment(conn, row) if row else None

    def _many(self, tail: str, params) -> list[Enrollment]:
        with self._pool.connection() as conn:
            rows = conn.execute(f"SELECT {ENROLLMENT_COLUMNS} FROM enrollment.enrollment {tail}", params).fetchall()
            return [_enrollment(conn, r) for r in rows]


def _values(e: Enrollment) -> tuple:
    return (
        e.id,
        e.student_id,
        e.student_name,
        e.term,
        e.subject_code,
        e.subject_name,
        e.section_id,
        e.group_number,
        e.status.value,
        e.rejection_reason.value if e.rejection_reason else None,
        e.origin.value,
        e.confirmation_id,
        e.reservation_id,
        e.saga_id,
        e.created_at,
        e.updated_at,
    )


def _enrollment(conn, row: dict) -> Enrollment:
    slots = conn.execute(
        "SELECT day, start_time, end_time FROM enrollment.enrollment_slot WHERE enrollment_id = %s", (row["id"],)
    ).fetchall()
    return Enrollment(
        id=row["id"],
        student_id=row["student_id"],
        student_name=row["student_name"],
        term=row["term"],
        subject_code=row["subject_code"],
        subject_name=row["subject_name"],
        section_id=row["section_id"],
        group_number=row["group_number"],
        slots=tuple(sorted(Slot(Day(s["day"]), _time(s["start_time"]), _time(s["end_time"])) for s in slots)),
        status=EnrollmentStatus(row["status"]),
        rejection_reason=RejectionReason(row["rejection_reason"]) if row["rejection_reason"] else None,
        origin=Origin(row["origin"]),
        confirmation_id=row["confirmation_id"],
        reservation_id=row["reservation_id"],
        saga_id=row["saga_id"],
        created_at=_utc(row["created_at"]),
        updated_at=_utc(row["updated_at"]),
    )


class PostgresCertificateStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def find_by_key(self, owner: str, idempotency_key: str) -> Keyed[Certificate] | None:
        with self._pool.connection() as conn:
            recorded = _resource_of(conn, owner, idempotency_key, "certificate")
        certificate = self.find(recorded[0]) if recorded else None
        return Keyed(certificate, recorded[1]) if recorded and certificate else None

    def find(self, certificate_id: UUID) -> Certificate | None:
        return self._one("WHERE id = %s", (certificate_id,))

    def find_by_code(self, code: str) -> Certificate | None:
        return self._one("WHERE code = %s", (code,))

    def page_of(self, student_id: UUID, page: int, limit: int) -> tuple[list[Certificate], int]:
        with self._pool.connection() as conn:
            total = conn.execute(
                "SELECT count(*) AS n FROM enrollment.certificate WHERE student_id = %s", (student_id,)
            ).fetchone()["n"]
            rows = conn.execute(
                f"SELECT {CERTIFICATE_COLUMNS} FROM enrollment.certificate WHERE student_id = %s "
                "ORDER BY issued_at DESC, id DESC LIMIT %s OFFSET %s",
                (student_id, limit, (page - 1) * limit),
            ).fetchall()
        return [_certificate(r) for r in rows], int(total)

    def create(self, certificate: Certificate, document: bytes, key: IdempotencyKey, event: DomainEvent) -> None:
        c = certificate
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                f"INSERT INTO enrollment.certificate ({CERTIFICATE_COLUMNS}, document) VALUES (%s, %s, %s, %s, %s, "
                "%s, %s, %s)",
                (c.id, c.student_id, c.student_name, c.type.value, c.code, c.term, c.issued_at, document),
            )
            _claim_key(conn, key, "certificate", c.id)
            _write_event(conn, event)

    def document_of(self, certificate_id: UUID) -> bytes | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT document FROM enrollment.certificate WHERE id = %s", (certificate_id,)
            ).fetchone()
        return bytes(row["document"]) if row else None

    def _one(self, where: str, params) -> Certificate | None:
        with self._pool.connection() as conn:
            row = conn.execute(f"SELECT {CERTIFICATE_COLUMNS} FROM enrollment.certificate {where}", params).fetchone()
        return _certificate(row) if row else None


def _certificate(row: dict) -> Certificate:
    return Certificate(
        row["id"],
        row["student_id"],
        row["student_name"],
        CertificateType(row["type"]),
        row["code"],
        row["term"],
        _utc(row["issued_at"]),
    )


class PostgresProbe:
    """`SELECT 1` through the pool within a short deadline (HU-PLT-004): a database that does not
    answer makes the service not ready instead of hanging the probe. The pool bounds the wait for a
    connection, but a query on a pooled connection whose engine stopped answering (paused,
    partitioned) would block until TCP gives up; so the check runs on a daemon thread and the probe
    waits for it at most `timeout_seconds`. The next probes share a check still pending instead of
    starting another one: at most one thread is ever stuck."""

    name = "database"

    def __init__(self, pool: ConnectionPool, timeout_seconds: float = 2.0) -> None:
        self._pool = pool
        self._timeout = timeout_seconds
        self._lock = threading.Lock()
        self._pending: Future | None = None

    def ping(self) -> None:
        with self._lock:
            if self._pending is None or self._pending.done():
                self._pending = Future()
                check = threading.Thread(target=self._select_one, args=(self._pending,), name="probe", daemon=True)
                check.start()
            pending = self._pending
        pending.result(timeout=self._timeout)  # TimeoutError when the engine does not answer in time

    def _select_one(self, outcome: Future) -> None:
        try:
            with self._pool.connection(timeout=self._timeout) as conn:
                conn.execute("SELECT 1")
        except Exception as error:  # noqa: BLE001 - handed to the waiting probe as is
            outcome.set_exception(error)
        else:
            outcome.set_result(None)
