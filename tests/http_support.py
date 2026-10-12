"""Support for the HTTP and contract tests: an RSA pair per test session, tokens, the app over
the in-memory enrollment seeded with the fakes."""

import time

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from enrollment.adapter.inbound.http.app import create_app
from enrollment.adapter.inbound.http.auth import TokenVerifier
from enrollment.application.usecase.enrollment import EnrollmentService
from tests.fakes import FakeRenderer, FixedClock, SequenceIds, seeded_store


def _pem(key) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


PRIVATE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_PRIVATE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC_PEM = (
    PRIVATE.public_key()
    .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    .decode()
)

LAURA = "11111111-1111-4111-8111-111111111111"
SANTIAGO = "11111111-1111-4111-8111-111111111112"
MARIA = "11111111-1111-4111-8111-111111111113"
CARLOS = "22222222-2222-4222-8222-222222222221"
PATRICIA = "33333333-3333-4333-8333-333333333331"
ISW_601_G1 = "55555555-5555-4555-8555-555555555501"
ISW_604_G1 = "55555555-5555-4555-8555-555555555505"
ISW_604_G2 = "55555555-5555-4555-8555-555555555506"
LAURA_ISW_601 = "66666666-6666-4666-8666-666666666601"


def mint(sub: str, role: str, name: str = "", minutes: int = 60, alg: str = "RS256", key=None, **extra) -> str:
    now = int(time.time())
    claims = {"iss": "qone-identity-api", "sub": sub, "role": role, "name": name, "iat": now, "exp": now + minutes * 60}
    claims.update(extra)
    if alg == "HS256":
        return jwt.encode(claims, "not-a-secret", algorithm="HS256")
    return jwt.encode(claims, _pem(key or PRIVATE), algorithm=alg)


def bearer(sub: str, role: str, name: str = "", **kw) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint(sub, role, name, **kw)}"}


def make_client():
    store = seeded_store()
    records, projections, enrollments, certificates = store.as_ports()
    service = EnrollmentService(
        records,
        projections,
        enrollments,
        certificates,
        FakeRenderer(),
        FixedClock(),
        SequenceIds(),
        "2026-B",
        "Ingeniería de Software",
    )
    return TestClient(create_app(service, TokenVerifier(PUBLIC_PEM)), raise_server_exceptions=False), store
