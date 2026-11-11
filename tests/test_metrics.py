"""HTTP level (HU-OBS-001): `GET /metrics` at the root of the app in the Prometheus text format,
the requests counted under their route template (never the raw path), and a registry per app."""

import pytest
from prometheus_client.parser import text_string_to_metric_families

from tests.http_support import make_client

SERVICE = "qone-enrollment-api"
BASE = "/api/v1/enrollment"
TEMPLATED_PATH = f"{BASE}/enrollments/00000000-0000-4000-8000-000000000000"
TEMPLATE = f"{BASE}/enrollments/{{enrollment_id}}"
BUCKETS = ["0.05", "0.1", "0.25", "0.5", "1.0", "2.0", "3.0", "5.0", "10.0", "+Inf"]


def new_client():
    return make_client()[0]


@pytest.fixture
def client():
    return new_client()


def scrape(client) -> list:
    """Every sample of the exposition, flattened (counter, bucket, count and sum samples)."""
    r = client.get("/metrics")
    assert r.status_code == 200
    return [sample for family in text_string_to_metric_families(r.text) for sample in family.samples]


def value(samples, name: str, **labels) -> float:
    """The sum of the samples called `name` whose labels include `labels`."""
    return sum(s.value for s in samples if s.name == name and all(s.labels.get(k) == v for k, v in labels.items()))


def test_metrics_answers_200_in_the_prometheus_text_format_without_a_token(client):
    client.get(f"{BASE}/health")
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "http_server_requests_total" in r.text
    assert "http_server_request_duration_seconds" in r.text


def test_a_request_is_counted_under_its_route_template(client):
    client.get(TEMPLATED_PATH)  # no token: 401, still a request of that route
    samples = scrape(client)
    labels = {"service": SERVICE, "method": "GET", "route": TEMPLATE}
    assert value(samples, "http_server_requests_total", status_class="4xx", **labels) == 1
    assert value(samples, "http_server_request_duration_seconds_count", **labels) == 1
    assert TEMPLATED_PATH not in client.get("/metrics").text


def test_a_successful_request_is_counted_as_2xx(client):
    client.get(f"{BASE}/health")
    client.get(f"{BASE}/health")
    samples = scrape(client)
    labels = {"service": SERVICE, "method": "GET", "route": f"{BASE}/health", "status_class": "2xx"}
    assert value(samples, "http_server_requests_total", **labels) == 2


def test_an_unknown_path_is_counted_as_unmatched(client):
    client.get(f"{BASE}/no-such-route/12345")
    samples = scrape(client)
    assert value(samples, "http_server_requests_total", route="unmatched", status_class="4xx") == 1
    assert f"{BASE}/no-such-route/12345" not in client.get("/metrics").text


def test_the_scrape_itself_is_not_counted(client):
    client.get("/metrics")
    samples = scrape(client)
    assert value(samples, "http_server_requests_total", route="/metrics") == 0
    assert value(samples, "http_server_requests_total") == 0


def test_the_latency_histogram_has_the_agreed_buckets(client):
    client.get(f"{BASE}/health")
    samples = scrape(client)
    buckets = [s.labels["le"] for s in samples if s.name == "http_server_request_duration_seconds_bucket"]
    assert buckets == BUCKETS


def test_every_app_has_its_own_registry():
    first, second = new_client(), new_client()
    first.get(f"{BASE}/health")
    assert value(scrape(first), "http_server_requests_total") == 1
    assert value(scrape(second), "http_server_requests_total") == 0


def test_an_unhandled_error_is_counted_as_5xx(client):
    def boom():
        raise RuntimeError("boom")

    client.app.add_api_route(f"{BASE}/boom", boom)
    assert client.get(f"{BASE}/boom").status_code == 500
    samples = scrape(client)
    assert value(samples, "http_server_requests_total", route=f"{BASE}/boom", status_class="5xx") == 1
