"""The composition root (Annex C): the only module that knows every concrete type and every
limit. It reads the environment, builds the adapters and hands the ports to the application."""

import json
import logging
import os
import sys

from enrollment.adapter.inbound.http.app import create_app
from enrollment.adapter.inbound.http.auth import TokenVerifier
from enrollment.adapter.outbound.document.pdf import PdfRenderer
from enrollment.adapter.outbound.persistence.memory import InMemoryEnrollment
from enrollment.adapter.outbound.runtime import SystemClock, Uuid4Generator
from enrollment.application.usecase.enrollment import EnrollmentService

# Explicit limits (norm 5.3.10, Annex C), declared here with their values.
DB_POOL_MAX = 10
DB_WAIT_SECONDS = 5.0
DB_STATEMENT_TIMEOUT_MS = 5000
HTTP_KEEP_ALIVE_SECONDS = 60
HTTP_GRACEFUL_SHUTDOWN_SECONDS = 20


class Settings:
    def __init__(self, env: dict[str, str] | None = None) -> None:
        env = os.environ if env is None else env
        self.database_url = env.get("ENROLLMENT_DATABASE_URL", "").strip()
        self.public_key = _public_key(env)
        self.current_term = env.get("CURRENT_TERM", "2026-B").strip()
        self.default_program = env.get("DEFAULT_PROGRAM", "Ingeniería de Software").strip()
        self.port = int(env.get("PORT", "8000"))
        self.log_level = env.get("LOG_LEVEL", "info").lower()


def _public_key(env: dict[str, str]) -> str:
    """JWT_PUBLIC_KEY as the PEM in one line with literal \\n, or JWT_PUBLIC_KEY_FILE."""
    inline = env.get("JWT_PUBLIC_KEY", "").strip()
    if inline:
        return inline.replace("\\n", "\n")
    path = env.get("JWT_PUBLIC_KEY_FILE", "").strip()
    if path:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    raise SystemExit("JWT_PUBLIC_KEY or JWT_PUBLIC_KEY_FILE is required: the service validates every token itself")


def build_app(settings: Settings | None = None):
    settings = settings or Settings()
    configure_logging(settings.log_level)
    log = logging.getLogger("enrollment")
    if settings.database_url and settings.database_url != "memory":
        from enrollment.adapter.outbound.persistence.postgres import (
            PostgresCertificateStore,
            PostgresEnrollmentStore,
            PostgresProjections,
            PostgresRecordStore,
            connect,
        )

        pool = connect(settings.database_url, DB_POOL_MAX, DB_WAIT_SECONDS, DB_STATEMENT_TIMEOUT_MS)
        ports = (
            PostgresRecordStore(pool),
            PostgresProjections(pool),
            PostgresEnrollmentStore(pool),
            PostgresCertificateStore(pool),
        )
        log.info("store: postgresql (pool max %s, statement timeout %s ms)", DB_POOL_MAX, DB_STATEMENT_TIMEOUT_MS)
    else:
        ports = InMemoryEnrollment().as_ports()
        log.warning("store: in memory and empty (ENROLLMENT_DATABASE_URL not set); development only")
    service = EnrollmentService(
        *ports, PdfRenderer(), SystemClock(), Uuid4Generator(), settings.current_term, settings.default_program
    )
    return create_app(service, TokenVerifier(settings.public_key))


class JsonFormatter(logging.Formatter):
    """One JSON line per record with the traceId (norm 5.3.9)."""

    def format(self, record: logging.LogRecord) -> str:
        line = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("traceId", "method", "path", "status", "durationMs"):
            if hasattr(record, key):
                line[key] = getattr(record, key)
        if record.exc_info:
            line["exception"] = self.formatException(record.exc_info)
        return json.dumps(line, ensure_ascii=False)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    logging.getLogger("uvicorn.access").disabled = True
