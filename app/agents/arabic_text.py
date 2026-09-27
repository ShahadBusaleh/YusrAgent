"""Fixed Arabic wording for the chat pipeline.

Two things live here so every agent uses the same Arabic:

- HR_GLOSSARY: one standard Arabic term per HR concept, added to every
  LLM prompt that writes Arabic (translation, Consultant answers, growth
  plans) so the same term never comes out two different ways. Terms match
  the UI labels in app/ui/i18n.py.
- Arabic templates for deterministic answers (leave balances, payroll,
  attendance, request statuses). These are built from database values, so
  they are rendered directly in Arabic instead of being translated by the
  LLM: instant, free, and the numbers are always exact.

Every template returns None when it can't render its input fully in
Arabic (e.g. free-text notes from the HR agent); the caller then falls
back to translating the English text.
"""

from __future__ import annotations

import re

HR_GLOSSARY: dict[str, str] = {
    "annual leave": "إجازة سنوية",
    "sick leave": "إجازة مرضية",
    "emergency leave": "إجازة طارئة",
    "maternity leave": "إجازة وضع",
    "unpaid leave": "إجازة بدون أجر",
    "Hajj leave": "إجازة الحج",
    "leave balance": "رصيد الإجازات",
    "leave request": "طلب إجازة",
    "carry forward (leave)": "ترحيل الإجازة",
    "end-of-service award": "مكافأة نهاية الخدمة",
    "probation period": "فترة التجربة",
    "notice period": "فترة الإشعار",
    "employment contract": "عقد العمل",
    "termination": "إنهاء الخدمة",
    "resignation": "الاستقالة",
    "basic salary": "الراتب الأساسي",
    "housing allowance": "بدل السكن",
    "transportation allowance": "بدل النقل",
    "gross pay": "إجمالي الراتب",
    "total deductions": "إجمالي الاستقطاعات",
    "net pay": "صافي الراتب",
    "payroll": "الرواتب",
    "overtime": "العمل الإضافي",
    "working hours": "ساعات العمل",
    "attendance": "الحضور",
    "grievance": "شكوى",
    "approval": "الاعتماد",
    "pending approval": "بانتظار الاعتماد",
    "IBAN": "الآيبان",
    "Saudi Labor Law": "نظام العمل السعودي",
    "Article": "المادة",
    "company policy": "سياسة الشركة",
    "employment certificate": "شهادة تعريف بالراتب",
    "career growth plan": "خطة التطوير المهني",
}


def glossary_instruction(target: str = "ar") -> str:
    """Prompt text that pins each HR term to one standard form in the
    `target` language ("ar" when writing Arabic, "en" when translating
    Arabic into English, so the English side stays English)."""
    if target == "en":
        terms = "; ".join(f"{ar} -> {en}" for en, ar in HR_GLOSSARY.items())
        return (
            " Translate these Arabic HR terms into exactly these English "
            f"terms, and write no Arabic in the output: {terms}."
        )
    terms = "; ".join(f"{en} -> {ar}" for en, ar in HR_GLOSSARY.items())
    return (
        " When writing Arabic, use exactly these standard Arabic HR terms "
        f"for these concepts: {terms}."
    )


_LEAVE_TYPE_AR = {
    "annual": "السنوية",
    "sick": "المرضية",
    "emergency": "الطارئة",
    "maternity": "الوضع",
    "unpaid": "بدون أجر",
    "hajj": "الحج",
}

_READABLE_FIELDS_AR = {
    "leave_type": "نوع الإجازة",
    "start_date": "تاريخ البداية",
    "end_date": "تاريخ النهاية",
    "new_iban": "الآيبان الجديد",
    "new_value": "القيمة الجديدة",
    "field_name": "الحقل المراد تعديله",
}

_LEAVE_FIELDS = {"leave_type", "start_date", "end_date"}


def _num(value) -> str:
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


def assessment_message_ar(assessment: dict) -> str | None:
    """Arabic for the Manager's _assessment_message, where it is fixed text.
    Free-text notes from the HR agent return None (translated instead)."""
    status = assessment.get("status")
    notes = [str(n) for n in assessment.get("notes") or [] if n]
    missing = [str(m) for m in assessment.get("missing_information") or []]

    if status == "NOT_AUTHORIZED":
        return None if notes else "ليس لديك صلاحية لعرض هذه المعلومات."
    if status != "NEEDS_INFORMATION":
        return ""

    is_leave = assessment.get("action_type") == "leave_request" or (
        missing and set(missing) <= _LEAVE_FIELDS
    )
    if is_leave:
        missing_text = (
            "، ".join(_READABLE_FIELDS_AR.get(m, m) for m in missing)
            if missing
            else "نوع الإجازة والتواريخ المحددة"
        )
        return (
            f"لم أتمكن من تقديم طلب إجازة من هذه الرسالة — يرجى ذكر {missing_text} "
            "(مثال: \"أريد إجازة سنوية من 2027-01-10 إلى 2027-01-12\")."
        )
    if notes or any(m not in _READABLE_FIELDS_AR for m in missing):
        return None
    return "أحتاج إلى معلومات إضافية لتجهيز هذا الطلب. يرجى ذكر " + "، ".join(
        _READABLE_FIELDS_AR[m] for m in missing
    ) + "."


def leave_balance_ar(balance: dict) -> str | None:
    bits = [
        f"{label} {_num(balance[key])}"
        for label, key in (
            ("السنوية", "annual_remaining"),
            ("المرضية", "sick_remaining"),
            ("الطارئة", "emergency_remaining"),
        )
        if balance.get(key) is not None
    ]
    return "رصيد الإجازات المتبقي (بالأيام): " + "، ".join(bits) + "." if bits else None


def remaining_balance_ar(remaining) -> str:
    return f"رصيد الإجازات المتبقي (بالأيام): {_num(remaining)}."


def leave_requests_ar(count: int) -> str:
    return f"عدد طلبات الإجازة المسجّلة لديك: {count}."


def payroll_ar(payroll: dict) -> str | None:
    bits = [
        f"{label} {_num(payroll[key])} ريال"
        for label, key in (
            ("إجمالي الراتب", "gross_pay_sar"),
            ("إجمالي الاستقطاعات", "total_deductions_sar"),
            ("صافي الراتب", "net_pay_sar"),
        )
        if payroll.get(key) is not None
    ]
    if not bits:
        return None
    period = payroll.get("pay_period")
    prefix = f"الراتب ({period}): " if period else "الراتب: "
    return prefix + "، ".join(bits) + "."


def attendance_ar(attendance: dict) -> str | None:
    bits = []
    for label, key, suffix in (
        ("أيام الحضور", "days_present", ""),
        ("أيام الغياب", "days_absent", ""),
        ("نسبة الحضور", "attendance_rate_pct", "%"),
    ):
        if attendance.get(key) is not None:
            bits.append(f"{label}: {_num(attendance[key])}{suffix}")
    if not bits:
        return None
    period = attendance.get("attendance_month")
    prefix = f"الحضور ({period}): " if period else "الحضور: "
    return prefix + "، ".join(bits) + "."


_PERSONAL_FIELD_AR = {
    "email": "البريد الإلكتروني",
    "mobile": "رقم الجوال",
    "address": "العنوان",
    "city": "المدينة",
}


def action_summary_ar(action_type: str, payload: dict) -> str | None:
    if action_type == "leave_request":
        leave_type = str(payload.get("leave_type") or "").lower()
        type_ar = _LEAVE_TYPE_AR.get(leave_type)
        if not type_ar:
            return None
        cover = payload.get("suggested_cover_employee_name") or "لا يوجد مقترح"
        return (
            f"طلب إجازة {type_ar} للموظف {payload.get('employee_id')}: "
            f"من {payload.get('start_date')} إلى {payload.get('end_date')} "
            f"(عدد الأيام: {_num(payload.get('days'))}). البديل: {cover}."
        )
    if action_type == "bank_update":
        return (
            f"تحديث الحساب البنكي للموظف {payload.get('employee_id')}: "
            f"الآيبان \"{payload.get('old_iban')}\" ← \"{payload.get('new_iban')}\"."
        )
    if action_type == "certificate_request":
        return f"طلب شهادة تعريف للموظف {payload.get('employee_id')}."
    if action_type == "personal_info_update":
        # Fixed text so the requester's own new value (shown unmasked) is
        # never sent to the LLM translator.
        field_ar = _PERSONAL_FIELD_AR.get(str(payload.get("field_name") or ""))
        if not field_ar:
            return None
        old = payload.get("old_value") or "غير محدد"
        new = payload.get("new_value") or "غير محدد"
        return (
            f"تحديث البيانات الشخصية للموظف {payload.get('employee_id')}: "
            f"{field_ar} \"{old}\" ← \"{new}\"."
        )
    return None


def submission_note_ar(kind: str, proposal_id, cover: str | None = None) -> str:
    if kind == "duplicate":
        return f"طلبك بانتظار الاعتماد بالفعل (رقم الطلب {proposal_id})."
    if kind == "auto_approved":
        return (
            "تم تسجيل التعديل واعتماده تلقائياً لأنه منخفض الخطورة "
            f"(رقم الطلب {proposal_id})."
        )
    cover_note = f" البديل المقترح: {cover}." if cover else ""
    return f"تم إرسال طلبك للاعتماد (رقم الطلب {proposal_id}).{cover_note}"


_EXCEEDS_RE = re.compile(
    r"Requested ([\d.]+) days exceeds the remaining balance of ([\d.]+) days"
)


def failure_message_ar(reasons: list[str]) -> str:
    """Arabic twin of manager_agent._failure_message (same branch order)."""
    text = " ".join(reasons).lower()
    if "prompt injection" in text:
        return "لا يمكنني معالجة هذا الطلب. يرجى إعادة صياغته كسؤال عادي عن الموارد البشرية."
    if "not authorized" in text:
        return "ليس لديك صلاحية للوصول إلى هذه المعلومات أو تنفيذ هذا الإجراء."
    for reason in reasons:
        match = _EXCEEDS_RE.search(reason)
        if match:
            return (
                "لا يمكنني تقديم طلب الإجازة هذا. عدد الأيام المطلوبة "
                f"({match.group(1)}) يتجاوز الرصيد المتبقي ({match.group(2)})."
            )
    if "could not submit" in text:
        return "تعذّر إرسال طلبك للاعتماد. يرجى المحاولة مرة أخرى."
    if "policy lookup failed" in text:
        return "تعذّر الوصول إلى وثائق سياسات الموارد البشرية حالياً. يرجى المحاولة بعد قليل."
    if text == "greeting.":
        return (
            "مرحباً! أنا يُسر، مساعدك للموارد البشرية. يمكنني مساعدتك في الإجازات "
            "والرواتب والحضور وبياناتك الشخصية والشهادات وسياسات الموارد البشرية."
        )
    if "outside hr scope" in text:
        return (
            "هذا السؤال خارج نطاق ما يمكنني المساعدة فيه. أنا يُسر، مساعدك للموارد "
            "البشرية: اسألني عن الإجازات والرواتب والحضور وبياناتك الشخصية "
            "والشهادات وسياسات الموارد البشرية."
        )
    return (
        "لم أجد إجابة موثوقة لهذا السؤال. يمكنني مساعدتك في الإجازات والرواتب "
        "والحضور وبياناتك الشخصية والشهادات وأسئلة سياسات الموارد البشرية."
    )


POLICY_UNAVAILABLE_AR = (
    "(تعذّر جلب سياسة الموارد البشرية المرتبطة حالياً، لذا تغطي هذه الإجابة سجلاتك فقط.)"
)

GRIEVANCE_SUBMITTED_AR = "تم تقديم شكواك بنجاح. سيراجع قسم الموارد البشرية حالتك."

_CITATION_TAG_RE = re.compile(r"\s*\[\s*source\s*:[^\]]*\]", re.IGNORECASE)


_EMPTY_LABEL_RE = re.compile(r"^[\s>*#-]*\**[^\s:*]+(?:\s[^\s:*]+)?\**\s*:\s*\**\s*$")


def strip_citation_tags(text: str) -> str:
    """Arabic answers show sources in the UI's source list, not inline
    [Source: ID] tags (the translator was already told to drop them).
    A line left as just a label ("- **الاستشهاد**:") is dropped too."""
    lines = []
    for line in text.splitlines():
        stripped = _CITATION_TAG_RE.sub("", line)
        if stripped != line and _EMPTY_LABEL_RE.match(stripped):
            continue
        lines.append(stripped)
    return "\n".join(lines).strip()
