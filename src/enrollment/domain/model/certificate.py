"""Certificates (EN-11..EN-14, D-12): issued once with a public verification code, the PDF
rendered once at issue. Preconditions are business rules: `pago` and `paz-y-salvo` need the
tuition of the current term PAID (INV-ENR-002, read from the billing projection), `matricula`
needs at least one CONFIRMED enrollment this term, `avance` needs a non-empty record;
`estudios` has none."""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from enrollment.domain.model.errors import BusinessRuleViolation


class CertificateType(StrEnum):
    ESTUDIOS = "estudios"
    MATRICULA = "matricula"
    PAGO = "pago"
    PAZ_Y_SALVO = "paz-y-salvo"
    AVANCE = "avance"


TITLES = {
    CertificateType.ESTUDIOS: "CONSTANCIA DE ESTUDIOS",
    CertificateType.MATRICULA: "CERTIFICADO DE MATRICULA",
    CertificateType.PAGO: "CERTIFICADO DE PAGO DE MATRICULA",
    CertificateType.PAZ_Y_SALVO: "PAZ Y SALVO FINANCIERO",
    CertificateType.AVANCE: "CERTIFICADO DE AVANCE DEL PENSUM",
}


@dataclass(frozen=True)
class Certificate:
    id: UUID
    student_id: UUID
    student_name: str
    type: CertificateType
    code: str
    term: str
    issued_at: datetime


def new_code() -> str:
    """`QMP-` plus eight upper-case hexadecimal characters (contract pattern ^QMP-[A-Z0-9]{8}$)."""
    return f"QMP-{secrets.token_hex(4).upper()}"


def ensure_preconditions(
    kind: CertificateType, tuition_paid: bool, confirmed_this_term: int, record_empty: bool
) -> None:
    if kind in (CertificateType.PAGO, CertificateType.PAZ_Y_SALVO) and not tuition_paid:
        raise BusinessRuleViolation("INV-ENR-002: the tuition of the current term is not paid")
    if kind is CertificateType.MATRICULA and confirmed_this_term == 0:
        raise BusinessRuleViolation("there is no confirmed enrollment this term")
    if kind is CertificateType.AVANCE and record_empty:
        raise BusinessRuleViolation("the academic record has no passed subjects yet")


def issue(
    certificate_id: UUID, student_id: UUID, student_name: str, kind: CertificateType, term: str, now: datetime
) -> Certificate:
    moment = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    return Certificate(certificate_id, student_id, student_name, kind, new_code(), term, moment)
