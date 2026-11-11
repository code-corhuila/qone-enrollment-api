"""The ready probe (HU-PLT-004). Use-case level with fake probes; HTTP level `GET {PREFIX}/health/ready`
answers 200 with the checks, and 503 with the common envelope when the database does not answer
while `/health` (liveness) stays 200; the PostgreSQL probe fails fast on an unreachable engine."""

import threading
import time
from contextlib import contextmanager

import psycopg
import pytest
from psycopg_pool import PoolTimeout

from enrollment.adapter.outbound.persistence.memory import InMemoryProbe
from enrollment.application.errors import DependencyUnavailable
from enrollment.application.usecase.readiness import CheckReadiness
from tests.http_support import make_client

SERVICE = "qone-enrollment-api"
BASE = "/api/v1/enrollment"
UNREACHABLE = "postgresql://nobody:nothing@127.0.0.1:1/none"


class FakeProbe:
    def __init__(self, name: str = "database", fails: bool = False) -> None:
        self.name = name
        self.fails = fails

    def ping(self) -> None:
        if self.fails:
            raise ConnectionError("connection refused")


def new_client(readiness: CheckReadiness | None = None):
    return make_client(readiness)[0]


class TestCheckReadiness:
    def test_every_dependency_that_answers_is_reported_ok(self):
        checks = CheckReadiness([FakeProbe("database"), FakeProbe("broker")]).check()
        assert checks == {"database": "ok", "broker": "ok"}

    def test_a_dependency_that_does_not_answer_makes_the_service_unavailable(self):
        with pytest.raises(DependencyUnavailable) as raised:
            CheckReadiness([FakeProbe("database", fails=True)]).check()
        assert raised.value.dependency == "database"
        assert raised.value.message == "database is not reachable"

    def test_the_in_memory_store_is_always_ready(self):
        assert CheckReadiness([InMemoryProbe()]).check() == {"database": "ok"}


class TestReadyEndpoint:
    def test_ready_answers_200_with_the_checks_without_a_token(self):
        r = new_client().get(f"{BASE}/health/ready")
        assert r.status_code == 200
        assert r.json() == {"status": "ready", "service": SERVICE, "checks": {"database": "ok"}}

    def test_ready_answers_503_with_the_common_envelope_when_the_database_is_down(self):
        client = new_client(CheckReadiness([FakeProbe("database", fails=True)]))
        r = client.get(f"{BASE}/health/ready", headers={"X-Correlation-Id": "ready-check-1"})
        assert r.status_code == 503
        assert r.json() == {
            "error": "SERVICE_UNAVAILABLE",
            "message": "database is not reachable",
            "traceId": "ready-check-1",
        }

    def test_liveness_stays_200_while_the_database_is_down(self):
        client = new_client(CheckReadiness([FakeProbe("database", fails=True)]))
        assert client.get(f"{BASE}/health").status_code == 200


class TestPostgresProbe:
    def test_it_fails_fast_when_the_database_is_unreachable(self):
        from enrollment.adapter.outbound.persistence.postgres import PostgresProbe, connect

        pool = connect(UNREACHABLE, max_size=1, wait_seconds=0.5)
        try:
            started = time.perf_counter()
            with pytest.raises((PoolTimeout, psycopg.OperationalError, TimeoutError)):
                PostgresProbe(pool, timeout_seconds=0.5).ping()
            assert time.perf_counter() - started < 3
        finally:
            pool.close(timeout=1)

    def test_its_failure_reaches_the_client_as_503(self):
        from enrollment.adapter.outbound.persistence.postgres import PostgresProbe, connect

        pool = connect(UNREACHABLE, max_size=1, wait_seconds=0.5)
        try:
            client = new_client(CheckReadiness([PostgresProbe(pool, timeout_seconds=0.5)]))
            r = client.get(f"{BASE}/health/ready")
            assert r.status_code == 503 and r.json()["error"] == "SERVICE_UNAVAILABLE"
        finally:
            pool.close(timeout=1)


class _HangingPool:
    """A pool whose connection never answers the query until released: a paused database, whose
    kernel still accepts the bytes while the engine never replies."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.queries = 0

    @contextmanager
    def connection(self, timeout: float | None = None):
        yield self

    def execute(self, query: str) -> None:
        self.queries += 1
        self.release.wait(10)


class TestPostgresProbeDeadline:
    def test_a_query_that_never_answers_fails_within_the_timeout(self):
        from enrollment.adapter.outbound.persistence.postgres import PostgresProbe

        pool = _HangingPool()
        probe = PostgresProbe(pool, timeout_seconds=0.3)
        try:
            started = time.perf_counter()
            with pytest.raises(TimeoutError):
                probe.ping()
            with pytest.raises(TimeoutError):
                probe.ping()  # the check still pending is shared, not started again
            assert time.perf_counter() - started < 2
            assert pool.queries == 1
        finally:
            pool.release.set()

    def test_a_query_that_answers_is_ready(self):
        from enrollment.adapter.outbound.persistence.postgres import PostgresProbe

        pool = _HangingPool()
        pool.release.set()
        PostgresProbe(pool, timeout_seconds=0.3).ping()
        assert pool.queries == 1
