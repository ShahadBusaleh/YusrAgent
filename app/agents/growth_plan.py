"""
CV-driven growth plan generation for Growth Opportunities.

An additive path from Experience Gap's "closest experience"
candidates straight to a personalized development plan.
"""

from __future__ import annotations

import io
import re

from openai import OpenAI
from pypdf import PdfReader

from app.config import get_settings

_MAX_CV_CHARS = 15000

_SYSTEM_PROMPT = """You are an internal-mobility coach for an HR platform.

You are given:
- an employee's current job title and department
- an experience title their department is missing or under-covered in
- the raw text extracted from that employee's CV

Write a concise, personalized development plan for how this employee
could grow from their current role into the target experience title.

Rules:
- Base the plan only on the CV text and the facts given. Do not invent
  qualifications, past roles, or achievements that are not in the CV.
- If the CV text is thin or unrelated, say so plainly and give a
  general starting path instead of fabricating a personalized one.
- Structure the plan as short sections: what they already bring,
  concrete skill/training gaps to close, and suggested next steps
  (training, certifications, stretch assignments) with a realistic
  rough timeline.
- Keep it actionable and specific, not generic motivational text.
- Use only plain ASCII characters. Never use em/en dashes, curly or
  smart quotes, or non-breaking spaces - use a plain hyphen and
  straight quotes instead.
- Use plain Markdown only. Never use raw HTML tags (like <br> or
  <b>) - for a line break inside a table cell, use a semicolon or
  start a new list item instead.
"""


def extract_pdf_text(file_bytes: bytes) -> str:
    """
    Extract text from a PDF CV. Returns "" if the PDF has no
    extractable text layer (e.g. a scanned image) OR isn't a valid,
    parseable PDF at all — callers must treat "" as an error to
    surface to the user, not silently send empty text to the LLM.
    """

    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception:
        return ""

    text = "\n".join(pages).strip()

    return text[:_MAX_CV_CHARS]


_BR_TAG_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)


def _clean_plan_text(text: str) -> str:
    """
    The model is asked for plain Markdown but doesn't always comply -
    normalize stray raw <br> tags (common inside table cells) into
    something Markdown renders as a break instead of showing literally.
    """

    return _BR_TAG_RE.sub("; ", text)


def generate_growth_plan(
    employee: dict,
    gap: dict,
    cv_text: str,
) -> str:
    """Call the LLM to turn a CV + experience gap into a development plan."""

    settings = get_settings()

    if not settings.llm_api_key:
        raise RuntimeError(
            "LLM_API_KEY is not set. Add it to your local .env file, then retry."
        )

    client = OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )

    user_prompt = f"""
Employee:
- Current job title: {employee.get('job_title')}
- Department: {employee.get('department_name')}

Target experience title the department needs: {gap.get('skill_name')} ({gap.get('category')})
Current coverage: {gap.get('current_headcount')} employees in the department currently hold this experience title ({gap.get('status')})

CV text:
{cv_text}
"""

    response = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
    )

    return _clean_plan_text((response.choices[0].message.content or "").strip())
