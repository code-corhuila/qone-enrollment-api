"""Contract level: responses validate against the synced copy of qone-enrollment-api.yaml and
every path the contract declares is served."""

import re
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

from tests.http_support import ISW_604_G1, LAURA, LAURA_ISW_601, bearer, make_client

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts" / "openapi"
ENROLLMENT = yaml.safe_load((CONTRACTS / "qone-enrollment-api.yaml").read_text(encoding="utf-8"))
SHARED = yaml.safe_load((CONTRACTS / "_shared.yaml").read_text(encoding="utf-8"))
BASE = "/api/v1/enrollment"


def inline(node, doc):
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            source = SHARED if ref.startswith("./_shared.yaml#") else doc
            target = source
            for part in ref.split("#/")[1].split("/"):
                target = target[part]
            return inline(target, source)
        out = {k: inline(v, doc) for k, v in node.items() if k != "nullable"}
        return {"anyOf": [out, {"type": "null"}]} if node.get("nullable") else out
    if isinstance(node, list):
        return [inline(v, doc) for v in node]
    return node


def assert_valid(body, name: str, doc=ENROLLMENT):
    schema = inline(doc["components"]["schemas"][name], doc)
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(body))
    assert not errors, [e.message for e in errors]


@pytest.fixture
def client():
    client, _ = make_client()
    return client


def shape(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


def test_every_path_of_the_contract_is_served(client):
    served = {shape(p) for p in client.app.openapi()["paths"]}
    for path in ENROLLMENT["paths"]:
        assert shape("/api/v1" + path) in served, f"{path} is in the contract but not served"


def test_every_representation_matches_its_schema(client):
    laura, saga = bearer(LAURA, "STUDENT", "Laura Gómez"), bearer("qone-workflow", "SERVICE")
    assert_valid(client.get(f"{BASE}/health").json(), "Health", SHARED)
    assert_valid(client.get(f"{BASE}/academic-records/me", headers=laura).json(), "AcademicRecord")
    assert_valid(client.get(f"{BASE}/pensum/me", headers=laura).json(), "Pensum")
    assert_valid(client.get(f"{BASE}/enrollments/me", headers=laura).json(), "EnrollmentPage")
    assert_valid(client.get(f"{BASE}/enrollments/{LAURA_ISW_601}", headers=laura).json(), "Enrollment")
    assert_valid(client.get(f"{BASE}/schedule/me", headers=laura).json(), "ScheduleItemPage")
    assert_valid(
        client.get(f"{BASE}/sections/{ISW_604_G1}/roster", headers=bearer(LAURA, "ADMIN")).json(), "RosterItemPage"
    )
    assert_valid(
        client.post(
            f"{BASE}/eligibility-checks", json={"studentId": LAURA, "sectionId": ISW_604_G1}, headers=saga
        ).json(),
        "EligibilityResult",
    )
    body = {
        "studentId": LAURA,
        "sectionId": ISW_604_G1,
        "sagaId": str(uuid4()),
        "origin": "UI",
        "outcome": "CONFIRMED",
        "reservationId": str(uuid4()),
    }
    assert_valid(body, "EnrollmentRequest")
    assert_valid(
        client.post(f"{BASE}/enrollments", json=body, headers={**saga, "Idempotency-Key": "contract-enroll-1"}).json(),
        "Enrollment",
    )
    issued = client.post(
        f"{BASE}/certificates", json={"type": "estudios"}, headers={**laura, "Idempotency-Key": "contract-cert-1"}
    ).json()
    assert_valid(issued, "Certificate")
    assert_valid(client.get(f"{BASE}/certificates/me", headers=laura).json(), "CertificatePage")
    assert_valid(client.get(f"{BASE}/certificates/verify/{issued['code']}").json(), "Verification")
    assert_valid(client.get(f"{BASE}/certificates/verify/QMP-00000000").json(), "Verification")


@pytest.mark.parametrize(
    ("method", "path", "auth", "status", "code"),
    [
        ("GET", "/enrollments/me?limit=101", ("STUDENT", LAURA), 400, "VALIDATION_ERROR"),
        ("GET", "/pensum/me", None, 401, "UNAUTHORIZED"),
        ("GET", "/pensum/me", ("ADMIN", LAURA), 403, "FORBIDDEN"),
        ("GET", "/enrollments/00000000-0000-4000-8000-000000000000", ("STUDENT", LAURA), 404, "NOT_FOUND"),
        ("GET", "/no-such-route", None, 404, "NOT_FOUND"),
    ],
)
def test_every_error_matches_the_shared_error_schema(client, method, path, auth, status, code):
    headers = bearer(auth[1], auth[0]) if auth else {}
    r = client.request(method, f"{BASE}{path}", headers=headers)
    assert r.status_code == status and r.json()["error"] == code
    assert_valid(r.json(), "Error", SHARED)
