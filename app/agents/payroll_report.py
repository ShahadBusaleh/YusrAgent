"""Render a monthly, all-employee payroll report as a PDF (HR-manager export)."""

from __future__ import annotations

from datetime import datetime, timezone

from fpdf import FPDF


def _safe_text(value: object) -> str:
    """Core Helvetica only supports Latin-1; replace anything outside it
    rather than let an unexpected name/department crash PDF generation."""
    text = str(value or "")
    return text.encode("latin-1", "replace").decode("latin-1")


def generate_monthly_payroll_pdf(rows: list[dict], period: str) -> bytes:
    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, f"Yusor - Payroll Report ({period})", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 9)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pdf.cell(0, 6, f"Generated {generated} · {len(rows)} employee(s)", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    header = [
        "Employee ID", "Name", "Department",
        "Basic (SAR)", "Gross (SAR)", "Deductions (SAR)", "Net (SAR)",
    ]

    total_gross = 0.0
    total_deductions = 0.0
    total_net = 0.0
    data = [header]

    for row in rows:
        basic = row.get("basic_salary_sar") or 0.0
        gross = row.get("gross_pay_sar") or 0.0
        deductions = row.get("total_deductions_sar") or 0.0
        net = row.get("net_pay_sar") or 0.0
        total_gross += gross
        total_deductions += deductions
        total_net += net
        data.append(
            [
                _safe_text(row.get("employee_id")),
                _safe_text(row.get("full_name")),
                _safe_text(row.get("department_name")),
                f"{basic:,.2f}",
                f"{gross:,.2f}",
                f"{deductions:,.2f}",
                f"{net:,.2f}",
            ]
        )

    data.append(
        [
            "", "", "TOTAL",
            "",
            f"{total_gross:,.2f}",
            f"{total_deductions:,.2f}",
            f"{total_net:,.2f}",
        ]
    )

    pdf.set_font("Helvetica", "", 9)
    with pdf.table(
        col_widths=(22, 45, 35, 28, 28, 30, 28),
        text_align=("LEFT", "LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT", "RIGHT"),
        first_row_as_headings=True,
    ) as table:
        for data_row in data:
            row = table.row()
            for datum in data_row:
                row.cell(datum)

    return bytes(pdf.output())
