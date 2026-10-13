"""HTTP level: the saga steps and the certificates, and the real PDF renderer."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from enrollment.adapter.outbound.document.pdf import PdfRenderer, paragraphs_of
from enrollment.application.port.outbound.ports import CertificateContext
from enrollment.domain.model.certificate import CertificateType, issue
from enrollment.domain.model.record import PensumTotals
from tests.fakes import LAURA_ISW_601, RECORDS
from tests.http_support import ISW_601_G1, ISW_604_G1, ISW_604_G2, LAURA, MARIA, PATRICIA, SANTIAGO, bearer, make_client

BASE = "/api/v1/enrollment"


@pytest.fixture
def client():
    client, _ = make_client()
    return client


def saga():
    return bearer("qone-workflow", "SERVICE", "qone-workflow")


def confirm_body(student=LAURA, section=ISW_604_G1):
    return {
        "studentId": student,
        "sectionId": section,
        "sagaId": str(uuid4()),
        "origin": "UI",
        "outcome": "CONFIRMED",
        "reservationId": str(uuid4()),
    }


class TestSaga:
    def test_eligibility_verdicts(self, client):
        def check(student, section):
            return client.post(
                f"{BASE}/eligibility-checks", json={"studentId": student, "sectionId": section}, headers=saga()
            ).json()

        assert check(LAURA, ISW_604_G1) == {"eligible": True}
        assert check(SANTIAGO, ISW_604_G1)["reason"] == "PREREQ_MISSING" and check(SANTIAGO, ISW_604_G1)[
            "missingPrerequisites"
        ] == ["RED-501"]
        assert check(MARIA, ISW_604_G2)["reason"] == "DUPLICATE"
        conflict = check(LAURA, ISW_604_G2)
        assert conflict["reason"] == "SCHEDULE_CONFLICT" and conflict["conflictingSectionId"] == ISW_601_G1
        assert (
            client.post(
                f"{BASE}/eligibility-checks",
                json={"studentId": LAURA, "sectionId": ISW_604_G1},
                headers=bearer(LAURA, "STUDENT"),
            ).status_code
            == 403
        )
        bad = client.post(f"{BASE}/eligibility-checks", json={"studentId": "x", "sectionId": "y"}, headers=saga())
        assert [d["field"] for d in bad.json()["details"]] == ["studentId", "sectionId"]

    def test_record_replay_duplicate_and_cancel(self, client):
        key = {"Idempotency-Key": "saga-1:confirm-enrollment"}
        r = client.post(f"{BASE}/enrollments", json=confirm_body(), headers={**saga(), **key})
        assert (
            r.status_code == 201
            and r.json()["status"] == "CONFIRMED"
            and r.headers["Location"].endswith(r.json()["id"])
        )
        assert client.post(f"{BASE}/enrollments", json=confirm_body(), headers={**saga(), **key}).status_code == 200
        dup = client.post(
            f"{BASE}/enrollments",
            json=confirm_body(),
            headers={**saga(), "Idempotency-Key": "saga-2:confirm-enrollment"},
        )
        assert dup.status_code == 422 and dup.json()["message"].startswith("INV-ENR-001")
        enrollment = r.json()["id"]
        assert client.post(f"{BASE}/enrollments/{enrollment}/cancel", headers=saga()).json()["status"] == "CANCELLED"
        assert client.post(f"{BASE}/enrollments/{enrollment}/cancel", headers=saga()).status_code == 200

    def test_rejected_attempts_and_the_border(self, client):
        body = {
            "studentId": SANTIAGO,
            "sectionId": ISW_604_G1,
            "sagaId": str(uuid4()),
            "origin": "UI",
            "outcome": "REJECTED",
            "rejectionReason": "TUITION_UNPAID",
        }
        rejected = client.post(
            f"{BASE}/enrollments", json=body, headers={**saga(), "Idempotency-Key": "saga-3:confirm-enrollment"}
        ).json()
        assert rejected["rejectionReason"] == "TUITION_UNPAID"
        r = client.post(f"{BASE}/enrollments/{rejected['id']}/cancel", headers=saga())
        assert r.status_code == 422 and r.json()["error"] == "INVALID_STATUS_TRANSITION"
        agent = {**confirm_body(), "origin": "AGENT"}
        r = client.post(
            f"{BASE}/enrollments", json=agent, headers={**saga(), "Idempotency-Key": "saga-4:confirm-enrollment"}
        )
        assert r.status_code == 400 and r.json()["details"][0]["field"] == "confirmationId"
        bad = client.post(
            f"{BASE}/enrollments",
            json={**confirm_body(), "origin": "WEB", "outcome": "DONE", "sagaId": "x"},
            headers=saga(),
        )
        assert [d["field"] for d in bad.json()["details"]] == ["Idempotency-Key", "sagaId", "origin", "outcome"]
        assert (
            client.post(
                f"{BASE}/enrollments",
                json=confirm_body(),
                headers={**bearer(PATRICIA, "ADMIN"), "Idempotency-Key": "saga-5:confirm-enrollment"},
            ).status_code
            == 403
        )


class TestCertificates:
    def test_issue_verify_list_and_download(self, client):
        laura = bearer(LAURA, "STUDENT", "Laura Gómez")
        r = client.post(
            f"{BASE}/certificates", json={"type": "matricula"}, headers={**laura, "Idempotency-Key": "cert-key-0001"}
        )
        assert r.status_code == 201
        certificate = r.json()
        assert (
            certificate["code"].startswith("QMP-")
            and r.headers["Location"] == f"{BASE}/certificates/{certificate['id']}/pdf"
        )
        assert (
            client.post(
                f"{BASE}/certificates",
                json={"type": "matricula"},
                headers={**laura, "Idempotency-Key": "cert-key-0001"},
            ).status_code
            == 200
        )
        verified = client.get(f"{BASE}/certificates/verify/{certificate['code']}").json()
        assert verified == {
            "valid": True,
            "type": "matricula",
            "studentName": "Laura Gómez",
            "issuedAt": certificate["issuedAt"],
        }
        assert client.get(f"{BASE}/certificates/verify/QMP-00000000").json() == {
            "valid": False,
            "type": None,
            "studentName": None,
            "issuedAt": None,
        }
        assert client.get(f"{BASE}/certificates/verify/nope").status_code == 400
        assert client.get(f"{BASE}/certificates/me", headers=laura).json()["meta"]["total"] == 1
        pdf = client.get(f"{BASE}/certificates/{certificate['id']}/pdf", headers=laura)
        assert (
            pdf.status_code == 200
            and pdf.headers["content-type"] == "application/pdf"
            and pdf.headers["X-Verification-Code"] == certificate["code"]
        )
        assert (
            client.get(f"{BASE}/certificates/{certificate['id']}/pdf", headers=bearer(SANTIAGO, "STUDENT")).status_code
            == 404
        )

    def test_preconditions_and_the_border(self, client):
        santiago = bearer(SANTIAGO, "STUDENT", "Santiago Rojas")
        r = client.post(
            f"{BASE}/certificates", json={"type": "pago"}, headers={**santiago, "Idempotency-Key": "cert-key-0002"}
        )
        assert r.status_code == 422 and r.json()["message"].startswith("INV-ENR-002")
        bad = client.post(f"{BASE}/certificates", json={"type": "diploma"}, headers=santiago)
        assert [d["field"] for d in bad.json()["details"]] == ["Idempotency-Key", "type"]


def test_the_pdf_renderer_writes_a_real_pdf_with_the_code():
    now = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    for kind in CertificateType:
        certificate = issue(uuid4(), LAURA_ISW_601.student_id, "Laura Gómez", kind, "2026-B", now)
        context = CertificateContext(
            certificate, RECORDS[0], [LAURA_ISW_601], {"ISW-601": 3}, PensumTotals(16, 6, 3, 38, 2, 5), True
        )
        document = PdfRenderer().render(context)
        assert document.startswith(b"%PDF") and len(document) > 1000
    assert any(
        "ISW-601 Bases de Datos II, grupo 1, martes 08:00-10:00" in p
        for p in paragraphs_of(
            context.__class__(
                issue(uuid4(), LAURA_ISW_601.student_id, "L", CertificateType.MATRICULA, "2026-B", now),
                RECORDS[0],
                [LAURA_ISW_601],
                {"ISW-601": 3},
                PensumTotals(16, 6, 3, 38, 2, 5),
                True,
            )
        )
    )
