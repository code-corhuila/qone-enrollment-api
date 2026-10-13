"""HTTP level (Annex C): the checks of norm 5.3.12 against the enrollment reads."""

import pytest

from tests.http_support import (
    CARLOS,
    ISW_604_G1,
    LAURA,
    LAURA_ISW_601,
    OTHER_PRIVATE,
    PATRICIA,
    SANTIAGO,
    bearer,
    make_client,
    mint,
)

BASE = "/api/v1/enrollment"


@pytest.fixture
def client():
    client, _ = make_client()
    return client


def laura():
    return bearer(LAURA, "STUDENT", "Laura Gómez")


class TestAuthenticationAndEnvelope:
    def test_health_without_a_token_and_the_401_variants(self, client):
        assert client.get(f"{BASE}/health").json()["service"] == "qone-enrollment-api"
        assert client.get(f"{BASE}/pensum/me").status_code == 401
        for kw in ({"alg": "HS256"}, {"minutes": -5}, {"key": OTHER_PRIVATE}, {"exp": None}):
            token = mint(LAURA, "STUDENT", **kw)
            assert client.get(f"{BASE}/pensum/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401

    def test_the_envelope_the_correlation_and_the_404s(self, client):
        r = client.get(f"{BASE}/enrollments/not-a-uuid", headers={**laura(), "X-Correlation-Id": "e2e-saga-1"})
        assert r.status_code == 400 and r.json()["details"] == [{"field": "id", "message": "must be a UUID"}]
        assert r.json()["traceId"] == "e2e-saga-1" and r.headers["X-Correlation-Id"] == "e2e-saga-1"
        assert len(client.get(f"{BASE}/health").headers["X-Correlation-Id"]) == 36
        assert (
            client.get(f"{BASE}/enrollments/00000000-0000-4000-8000-000000000000", headers=laura()).status_code == 404
        )
        assert client.get(f"{BASE}/nothing", headers=laura()).json()["error"] == "NOT_FOUND"


class TestStudentReads:
    def test_record_and_pensum(self, client):
        record = client.get(f"{BASE}/academic-records/me", headers=laura()).json()
        assert record["studentName"] == "Laura Gómez" and record["passedCodes"] == ["ISW-501", "RED-501"]
        pensum = client.get(f"{BASE}/pensum/me", headers=laura()).json()
        states = {s["code"]: s["state"] for s in pensum["subjects"]}
        assert (
            states["ISW-601"] == "IN_PROGRESS"
            and states["ISW-701"] == "LOCKED"
            and pensum["totals"]["subjectsTotal"] == 6
        )
        assert client.get(f"{BASE}/pensum/me", headers=bearer(CARLOS, "PROFESSOR")).status_code == 403

    def test_enrollments_schedule_and_one_enrollment(self, client):
        page = client.get(f"{BASE}/enrollments/me?term=2026-B", headers=laura()).json()
        assert (
            page["meta"] == {"page": 1, "limit": 20, "total": 1, "totalPages": 1}
            and page["data"][0]["subjectCode"] == "ISW-601"
        )
        assert page["data"][0]["slots"][0] == {"day": "TUE", "start": "08:00", "end": "10:00"}
        bad = client.get(f"{BASE}/enrollments/me?limit=101&status=DONE", headers=laura())
        assert bad.status_code == 400 and [d["field"] for d in bad.json()["details"]] == ["limit", "status"]
        assert client.get(f"{BASE}/schedule/me", headers=laura()).json()["data"][0]["groupNumber"] == 1
        assert client.get(f"{BASE}/enrollments/{LAURA_ISW_601}", headers=laura()).json()["status"] == "CONFIRMED"
        assert client.get(f"{BASE}/enrollments/{LAURA_ISW_601}", headers=bearer(CARLOS, "PROFESSOR")).status_code == 200
        assert client.get(f"{BASE}/enrollments/{LAURA_ISW_601}", headers=bearer(SANTIAGO, "STUDENT")).status_code == 403

    def test_the_roster(self, client):
        roster = client.get(f"{BASE}/sections/{ISW_604_G1}/roster", headers=bearer(CARLOS, "PROFESSOR")).json()
        assert [r["studentName"] for r in roster["data"]] == ["María José Cabrera"]
        assert client.get(f"{BASE}/sections/{ISW_604_G1}/roster", headers=bearer(PATRICIA, "ADMIN")).status_code == 200
        assert client.get(f"{BASE}/sections/{ISW_604_G1}/roster", headers=laura()).status_code == 403
        assert (
            client.get(
                f"{BASE}/sections/00000000-0000-4000-8000-000000000000/roster", headers=bearer(CARLOS, "PROFESSOR")
            ).status_code
            == 404
        )
        assert (
            client.get(f"{BASE}/sections/x/roster", headers=bearer(CARLOS, "PROFESSOR")).json()["details"][0]["field"]
            == "sectionId"
        )
