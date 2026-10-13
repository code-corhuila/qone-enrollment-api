"""The DocumentRenderer over fpdf2: the certificate as the prototype printed it (logo, title,
paragraphs, issue date, signature line, public verification code). The core fonts of fpdf2 are
latin-1: Spanish accents are fine; nothing outside latin-1 is written."""

from datetime import datetime
from pathlib import Path

from fpdf import FPDF

from enrollment.application.port.outbound.ports import CertificateContext
from enrollment.domain.model.certificate import TITLES, CertificateType

LOGO = Path(__file__).with_name("logo-corhuila.png")
MONTHS = [
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
]
DAYS = {"MON": "lunes", "TUE": "martes", "WED": "miércoles", "THU": "jueves", "FRI": "viernes", "SAT": "sábados"}


def spanish_date(moment: datetime) -> str:
    return f"{moment.day} de {MONTHS[moment.month - 1]} de {moment.year}"


def paragraphs_of(context: CertificateContext) -> list[str]:
    c, record = context.certificate, context.record
    intro = (
        f"La Corporación Universitaria del Huila hace constar que {record.student_name.upper()} "
        f"está matriculada/o en el "
        f"programa de {record.program} y cursa el semestre {record.semester} en el periodo académico {c.term}."
    )
    credits_now = sum(context.credits_by_code.get(e.subject_code, 0) for e in context.confirmed)
    if c.type is CertificateType.ESTUDIOS:
        return [
            intro,
            f"En este periodo cursa {len(context.confirmed)} materias equivalentes a "
            f"{credits_now} créditos académicos.",
        ]
    if c.type is CertificateType.MATRICULA:
        lines = [
            f"- {e.subject_code} {e.subject_name}, grupo {e.group_number}, "
            + ", ".join(f"{DAYS[s.day.value]} {s.start:%H:%M}-{s.end:%H:%M}" for s in e.slots)
            for e in context.confirmed
        ]
        return [
            intro,
            f"Materias matriculadas y confirmadas en {c.term}:",
            *lines,
            f"Total: {len(context.confirmed)} materias, {credits_now} créditos.",
        ]
    if c.type is CertificateType.PAGO:
        return [intro, f"La factura de matrícula del periodo {c.term} se encuentra pagada."]
    if c.type is CertificateType.PAZ_Y_SALVO:
        return [
            intro,
            "A la fecha, la/el estudiante se encuentra a PAZ Y SALVO por todo concepto financiero "
            f"para el periodo {c.term}.",
        ]
    t = context.totals
    return [
        intro,
        f"Avance en el plan de estudios: {t.credits_passed} de {t.credits_total} créditos aprobados ({t.percent}%), "
        f"{t.subjects_passed} de {t.subjects_total} materias, más {t.credits_in_progress} créditos en curso.",
    ]


class PdfRenderer:
    def render(self, context: CertificateContext) -> bytes:
        c = context.certificate
        pdf = FPDF(format="letter")
        pdf.set_auto_page_break(True, margin=20)
        pdf.add_page()
        if LOGO.exists():
            pdf.image(str(LOGO), x=18, y=12, w=24)
        pdf.set_xy(46, 16)
        pdf.set_font("helvetica", "B", 11)
        pdf.cell(0, 6, "CORPORACIÓN UNIVERSITARIA DEL HUILA - CORHUILA")
        pdf.set_xy(46, 22)
        pdf.set_font("helvetica", "", 9)
        pdf.cell(0, 5, "Registro y Control Académico - Plataforma Qampus")
        pdf.set_draw_color(0, 149, 64)
        pdf.set_line_width(0.8)
        pdf.line(18, 42, 198, 42)
        pdf.set_y(56)
        pdf.set_font("helvetica", "B", 14)
        pdf.cell(0, 8, TITLES[c.type], align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(8)
        pdf.set_font("helvetica", "", 11)
        for paragraph in paragraphs_of(context):
            pdf.set_x(24)
            pdf.multi_cell(168, 6.5, paragraph)
            pdf.ln(2)
        pdf.ln(6)
        pdf.set_x(24)
        pdf.multi_cell(168, 6.5, f"Se expide a solicitud de la persona interesada, el {spanish_date(c.issued_at)}.")
        pdf.ln(14)
        pdf.line(24, pdf.get_y(), 88, pdf.get_y())
        pdf.set_x(24)
        pdf.set_font("helvetica", "B", 10)
        pdf.cell(0, 6, "Registro y Control Académico", new_x="LMARGIN", new_y="NEXT")
        pdf.set_y(-38)
        pdf.set_x(24)
        pdf.set_font("helvetica", "", 8)
        pdf.cell(0, 5, f"Código de verificación: {c.code}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(24)
        pdf.cell(0, 5, "Cualquier persona puede validar este documento en Qampus con el código anterior.")
        return bytes(pdf.output())
