"""Arabic versions of the API's user-facing error messages.

Routers keep raising HTTPException with English `detail` strings; the
exception handler in app/api/main.py passes them through `localize_detail`
when the request's Accept-Language is Arabic. Unknown messages come back
unchanged, so a new English message never breaks — it just isn't
translated until it is added here.
"""

from __future__ import annotations

import re

_EXACT_AR: dict[str, str] = {
    # auth / access
    "Missing bearer token": "رمز الدخول مفقود. يرجى تسجيل الدخول مرة أخرى.",
    "Invalid or expired token": "انتهت صلاحية الجلسة. يرجى تسجيل الدخول مرة أخرى.",
    "Invalid credentials": "اسم المستخدم أو كلمة المرور غير صحيحة.",
    "Invalid password": "كلمة المرور غير صحيحة.",
    "Invalid refresh token": "انتهت صلاحية الجلسة. يرجى تسجيل الدخول مرة أخرى.",
    "Inactive or unknown user": "الحساب غير نشط أو غير معروف.",
    "Insufficient role": "ليست لديك صلاحية للقيام بذلك.",
    "Own record only": "يمكنك الاطلاع على سجلك فقط.",
    "Own records only": "يمكنك الاطلاع على سجلاتك فقط.",
    # not found
    "Not found": "العنصر غير موجود.",
    "User not found": "المستخدم غير موجود.",
    "Employee not found": "الموظف غير موجود.",
    "Employee profile not found.": "لم يُعثر على الملف الوظيفي.",
    "Approval not found": "طلب الموافقة غير موجود.",
    "Approval has no proposal_id": "طلب الموافقة غير مرتبط بأي طلب.",
    "Grievance not found.": "الشكوى غير موجودة.",
    "Record not found": "السجل غير موجود.",
    "Regulation request not found": "طلب تحديث اللائحة غير موجود.",
    "New-hire request not found.": "طلب التعيين غير موجود.",
    "No leave balance": "لا يوجد رصيد إجازات مسجّل.",
    "Law row not in this request": "هذه المادة ليست ضمن هذا الطلب.",
    "Policy not in this request": "هذه السياسة ليست ضمن هذا الطلب.",
    # decisions
    "You cannot approve or reject your own request.": "لا يمكنك الموافقة على طلبك الخاص أو رفضه.",
    "You cannot view the decision brief for your own request.": "لا يمكنك عرض ملخص القرار لطلبك الخاص.",
    "This request has already been decided.": "تم البت في هذا الطلب مسبقاً.",
    "This request was already decided.": "تم البت في هذا الطلب مسبقاً.",
    "This grievance has already been reviewed.": "تمت مراجعة هذه الشكوى مسبقاً.",
    "Decision must be 'accept' or 'reject'.": "يجب أن يكون القرار قبولاً أو رفضاً.",
    "You edited the proposed text, so another HR manager or admin must approve it.":
        "لقد عدّلت النص المقترح، لذا يجب أن يعتمده مدير موارد بشرية آخر أو مسؤول النظام.",
    "A check is already running.": "هناك فحص قيد التشغيل حالياً.",
    # decision brief (Orchestrator.explain_pending_approval errors)
    "Could not generate decision brief": "تعذّر إنشاء ملخص القرار.",
    "proposal_id is required.": "معرّف الطلب مطلوب.",
    "Proposed action not found.": "الطلب غير موجود.",
    "No pending approval found for this proposal.": "لا توجد موافقة معلّقة لهذا الطلب.",
    # CV / growth plans / onboarding
    "Please upload your CV as a PDF file.": "يرجى رفع سيرتك الذاتية بصيغة PDF.",
    "Please upload the CV as a PDF file.": "يرجى رفع السيرة الذاتية بصيغة PDF.",
    "That CV file is too large (10 MB limit).": "ملف السيرة الذاتية كبير جداً (الحد الأقصى 10 ميغابايت).",
    "We couldn't read any text from that PDF. Try a text-based PDF export of your CV rather than a scanned image.":
        "تعذّرت قراءة أي نص من ملف PDF. جرّب تصدير سيرتك الذاتية كملف PDF نصي بدلاً من صورة ممسوحة ضوئياً.",
    "We couldn't read any text from that PDF. Fill in the fields manually.":
        "تعذّرت قراءة أي نص من ملف PDF. يرجى تعبئة الحقول يدوياً.",
    "No usable CV content remains. Please upload a CV describing your skills and experience.":
        "لم يتبقَّ محتوى صالح في السيرة الذاتية. يرجى رفع سيرة ذاتية تصف مهاراتك وخبراتك.",
    "This experience gap is not currently open to you.": "فرصة التطوير هذه غير متاحة لك حالياً.",
    "Growth plans are temporarily unavailable. Please try again later.":
        "خطط التطوير غير متاحة مؤقتاً. يرجى المحاولة لاحقاً.",
    "We couldn't generate a complete growth plan. Please retry, or upload a clearer CV.":
        "تعذّر إنشاء خطة تطوير كاملة. يرجى المحاولة مرة أخرى أو رفع سيرة ذاتية أوضح.",
    "CV reading is temporarily unavailable. Please fill in the fields manually.":
        "قراءة السيرة الذاتية غير متاحة مؤقتاً. يرجى تعبئة الحقول يدوياً.",
    "The CV could not be read reliably. Please retry or fill in the fields manually.":
        "تعذّرت قراءة السيرة الذاتية بشكل موثوق. يرجى المحاولة مرة أخرى أو تعبئة الحقول يدوياً.",
    "Only the requester can attach a CV to this request.": "يمكن لمقدّم الطلب فقط إرفاق سيرة ذاتية بهذا الطلب.",
}

_PATTERNS_AR: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^No payroll records found for (?P<period>.+)\.$"), "لا توجد سجلات رواتب للفترة {period}."),
    (re.compile(r"^status must be one of: (?P<values>.+)$"), "يجب أن تكون الحالة إحدى القيم التالية: {values}"),
    (re.compile(r"^Simulate mode refused: (?P<why>.*)$", re.S), "تم رفض وضع المحاكاة: {why}"),
]


def localize_detail(detail, lang: str):
    """Arabic version of an HTTPException detail, or `detail` unchanged."""
    if lang != "ar" or not isinstance(detail, str):
        return detail
    if detail in _EXACT_AR:
        return _EXACT_AR[detail]
    for pattern, template in _PATTERNS_AR:
        match = pattern.match(detail)
        if match:
            return template.format(**match.groupdict())
    return detail
