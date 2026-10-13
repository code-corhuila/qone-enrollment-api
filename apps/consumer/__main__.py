"""`python -m apps.consumer`: the projection consumer of the enrollment domain, a second process of
the same image (the API keeps answering while the projections move). Reconnects after a lost
broker with a pause."""

import logging
import os
import time

from apps.api.factory import DB_POOL_MAX, DB_STATEMENT_TIMEOUT_MS, DB_WAIT_SECONDS, configure_logging
from enrollment.adapter.inbound.events.rabbitmq import consume
from enrollment.adapter.outbound.persistence.postgres import connect
from enrollment.adapter.outbound.persistence.projections import PostgresProjectionStore
from enrollment.application.usecase.projections import ProjectEvents


def main() -> None:
    configure_logging(os.environ.get("LOG_LEVEL", "info"))
    logging.getLogger("pika").setLevel(logging.WARNING)
    log = logging.getLogger("enrollment.consumer")
    database, broker = os.environ["ENROLLMENT_DATABASE_URL"], os.environ["RABBITMQ_URL"]
    project = ProjectEvents(
        PostgresProjectionStore(connect(database, DB_POOL_MAX, DB_WAIT_SECONDS, DB_STATEMENT_TIMEOUT_MS))
    )
    while True:
        try:
            log.info("consuming catalog.# and billing.# into the projections")
            consume(broker, project)
        except Exception:  # noqa: BLE001 - a lost broker is retried; the queue keeps the messages
            log.exception("consumer stopped; reconnecting in 5 s")
            time.sleep(5)


if __name__ == "__main__":
    main()
