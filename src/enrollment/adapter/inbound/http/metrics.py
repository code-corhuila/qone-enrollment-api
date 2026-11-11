"""The RED metrics of the service (HU-OBS-001): requests by route template and status class, and
their latency, on a registry of its own per application (tests stay isolated). `GET /metrics`
lives at the root of the app, outside the API prefix: Prometheus scrapes the container on the
platform network and the gateway never routes it. The scrape itself is not counted."""

import time

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest

METRICS_PATH = "/metrics"
UNMATCHED = "unmatched"
BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 10)


class HttpMetrics:
    def __init__(self, service: str) -> None:
        self.service = service
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "http_server_requests_total",
            "HTTP requests served, by route template and status class",
            ("service", "method", "route", "status_class"),
            registry=self.registry,
        )
        self.duration = Histogram(
            "http_server_request_duration_seconds",
            "Latency of the HTTP requests, by route template",
            ("service", "method", "route"),
            buckets=BUCKETS,
            registry=self.registry,
        )

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        self.requests.labels(self.service, method, route, f"{status // 100}xx").inc()
        self.duration.labels(self.service, method, route).observe(seconds)

    def exposition(self) -> bytes:
        return generate_latest(self.registry)


def route_of(request: Request) -> str:
    """The template the router matched (`/api/v1/.../{id}`), never the raw path: the label keeps
    a bounded cardinality."""
    route = request.scope.get("route")
    return getattr(route, "path", None) or UNMATCHED


def install(app: FastAPI, service: str) -> HttpMetrics:
    """Adds the measuring middleware (outermost, so it sees the final status, the 500 included)
    and the scrape route."""
    metrics = HttpMetrics(service)
    app.state.metrics = metrics

    @app.middleware("http")
    async def measure(request: Request, call_next):
        if request.url.path == METRICS_PATH:
            return await call_next(request)
        started = time.perf_counter()
        response = await call_next(request)
        metrics.observe(request.method, route_of(request), response.status_code, time.perf_counter() - started)
        return response

    @app.get(METRICS_PATH, include_in_schema=False)
    def scrape() -> Response:
        return Response(metrics.exposition(), media_type=CONTENT_TYPE_LATEST)

    return metrics
