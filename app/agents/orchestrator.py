import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI
from app.agents.hr_agent import HRAgent
from app.agents.consultant_agent import ConsultantAgent
from app.agents.manager_agent import ManagerAgent

from app.agents import arabic_text
from app.config import fast_llm_options, get_settings
from app.security.governance import detect_prompt_injection
from app.agents.translation import (
    detect_language,
    prefetch_translations,
    translate_to_arabic,
    translate_to_english,
)

from app.db.connection import get_connection
from app.db.translation_cache import database_path
from app.db.approvals import list_pending_approvals
from app.db.grievances import create_grievance
from app.db.proposed_actions import get_proposed_action
from app.agents.hr_agent import get_historical_precedent, get_termination_profile
from app.llm import llm_client


logger = logging.getLogger(__name__)

_EMPTY_HR_RESULT = {
    "facts": {},
    "proposed_action": None,
    "sources": [],
}


def _failed_consultant_result(message: str) -> dict:
    """Consultant-shaped result for a crashed Consultant call, so Manager
    can still answer from HR facts (see Manager's `success is False`)."""
    return {
        "recommendation": "",
        "conflicts": [],
        "sources": [],
        "success": False,
        "error": {
            "type": "agent_exception",
            "stage": "ORCHESTRATOR",
            "message": message,
            "retryable": True,
        },
    }


def _render_arabic(blocks: list) -> str:
    """Arabic response from the Manager's (English, Arabic) pieces.

    A paragraph whose pieces all have fixed Arabic (balances, payroll,
    statuses) or were written in Arabic (Consultant) is used as is — no
    LLM call. Only paragraphs with a missing piece are translated, and
    those in parallel. A failed translation keeps that paragraph English."""

    def render(block: list) -> str:
        if all(ar_text for _, ar_text in block):
            return " ".join(arabic_text.strip_citation_tags(t) for _, t in block)
        english = " ".join(en for en, _ in block)
        try:
            return translate_to_arabic(english)
        except Exception:
            return english

    if len(blocks) == 1:
        paragraphs = [render(blocks[0])]
    else:
        with ThreadPoolExecutor(max_workers=min(4, len(blocks))) as pool:
            paragraphs = list(pool.map(render, blocks))
    return "\n\n".join(p for p in paragraphs if p)


# Employees may only see their own records. HR reads the signed-in user's
# employee_id, so without this check "Show me Mona Saleh's salary" silently
# answered with the requester's own payroll instead of refusing.
_PERSONAL_DATA_WORDS = (
    "salary", "payroll", "payslip", "pay slip", "net pay", "gross pay",
    "allowance", "deduction", "iban", "bank", "account number",
    "leave balance", "attendance", "absence", "overtime",
    "phone", "mobile", "email", "address", "national id", "iqama",
    "date of birth", "birthday", "personal info", "personal details",
    "personal data", "record", "profile", "performance", "evaluation",
    "contract",
)
_EMP_ID_IN_QUERY_RE = re.compile(r"\bEMP-\d{4}\b", re.IGNORECASE)
_POSSESSIVE_RE = re.compile(r"\b([A-Za-z]+)(?:'s|’s)\b")


def _references_other_employee(query: str, user: dict) -> bool:
    """True when an `employee`-role user asks for personal data (salary,
    IBAN, balances, contact details...) of someone other than themselves,
    named by employee ID, full name, or a possessive first name."""
    if (user or {}).get("role") != "employee":
        return False
    lowered = query.lower()
    if not any(word in lowered for word in _PERSONAL_DATA_WORDS):
        return False

    own_id = str(user.get("employee_id") or "").upper()
    if any(m.upper() != own_id for m in _EMP_ID_IN_QUERY_RE.findall(query)):
        return True

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT employee_id, full_name, employee_name_en FROM employees"
        ).fetchall()
    finally:
        conn.close()

    own_names: set[str] = set()
    other_names: set[str] = set()
    for employee_id, full_name, name_en in rows:
        names = {str(n).strip().lower() for n in (full_name, name_en) if n}
        (own_names if str(employee_id).upper() == own_id else other_names).update(names)
    other_names -= own_names

    for name in other_names:
        if name and re.search(rf"\b{re.escape(name)}\b", lowered):
            return True

    own_first = {n.split()[0] for n in own_names if n}
    other_first = {n.split()[0] for n in other_names if n} - own_first
    return any(
        m.lower() in other_first for m in _POSSESSIVE_RE.findall(query)
    )


class OrchestratorAgent:

    def __init__(self):
        settings = get_settings()

        # Rotates across LLM_API_KEYS (app/llm.py).
        self.client = llm_client(settings, openai_cls=OpenAI)

        self.model = settings.llm_model

        self.hr_agent = HRAgent()
        self.consultant_agent = ConsultantAgent()
        self.manager_agent = ManagerAgent()

    # =========================================================
    # 1. SECURITY GUARDRAILS
    # =========================================================

    def assess_query_risk(self, query: str) -> dict:
        """
        Detect query/prompt injection attempts and classify risk.

        Returns:
            {
                "detected": bool,
                "type": str,
                "risk": "LOW" | "MEDIUM" | "HIGH",
                "reason": str
            }
        """

        query_lower = query.lower()

        # Existing project security detector
        try:
            injection_detected = detect_prompt_injection(query)
        except Exception:
            injection_detected = False

        high_risk_patterns = [
            "ignore previous instructions",
            "ignore all previous instructions",
            "ignore the previous instructions",
            "system prompt",
            "reveal your instructions",
            "show me your prompt",
            "reveal the system prompt",
            "bypass security",
            "bypass authorization",
            "disable security",
            "ignore security",
            "execute unauthorized",
            "give me admin access",
            "bypass access control",
            "bypass authentication",
            "change my role to admin",
            # Arabic equivalents: checked on the original prompt before
            # it is sent to the translation LLM.
            "تجاهل التعليمات",
            "تجاهل جميع التعليمات",
            "تجاهل كل التعليمات",
            "تجاهل التعليمات السابقة",
            "موجه النظام",
            "تعليمات النظام",
            "اكشف تعليماتك",
            "تجاوز الأمان",
            "تجاوز الصلاحيات",
            "اجعلني مسؤول",
        ]

        medium_risk_patterns = [
            "ignore instructions",
            "forget your instructions",
            "override instructions",
            "act as admin",
            "pretend to be",
            "change your role",
            "reveal hidden",
            "ignore the rules",
            "override the rules",
        ]

        # Check explicit HIGH patterns first
        if any(
            pattern in query_lower
            for pattern in high_risk_patterns
        ):
            return {
                "detected": True,
                "type": "PROMPT_INJECTION",
                "risk": "HIGH",
                "reason": "High-risk prompt injection attempt detected.",
            }

        # Check explicit MEDIUM patterns
        if any(
            pattern in query_lower
            for pattern in medium_risk_patterns
        ):
            return {
                "detected": True,
                "type": "PROMPT_INJECTION",
                "risk": "MEDIUM",
                "reason": "Potential prompt injection attempt detected.",
            }

        # If the external detector detects something unknown,
        # classify it as MEDIUM
        if injection_detected:
            return {
                "detected": True,
                "type": "PROMPT_INJECTION",
                "risk": "MEDIUM",
                "reason": "Potential prompt injection detected.",
            }

        return {
            "detected": False,
            "type": "NONE",
            "risk": "LOW",
            "reason": "",
        }

    # =========================================================
    # 2. INTENT CLASSIFICATION
    # =========================================================

    def classify_intent(self, query: str) -> str:
        """
        Classify the request into exactly one intent:

        HR
        CONSULTANT
        BOTH
        GRIEVANCE
        OTHER

        Phase 2 HR requests such as payroll, attendance,
        personal-info updates, bank changes, and certificate
        requests are routed through the existing HR intent.
        """

                # Fast path for obvious intent matches.
        normalized = str(query or "").strip().lower()

        fast_paths = [
            (
                # Phase 4 staffing actions — checked first so a free-text
                # termination reason can't route the request elsewhere.
                # (Restored: dropped by fe67d6e, which was edited from a
                # pre-Phase-4 copy of this file.)
                "HR",
                [
                    "hire new employee",
                    "add new employee",
                    "terminate employee",
                ],
            ),
            (
                "BOTH",
                [
                    "how many annual leave days do i have remaining and can i carry",
                    "how many annual leave days do i have left and can i carry",
                    "am i entitled to 30 days of annual leave",
                    "can i change my bank account according to company policy",
                    "annual leave balance and carry forward",
                ],
            ),
            (
                "GRIEVANCE",
                [
                    "submit a complaint",
                    "file a complaint",
                    "submit a grievance",
                    "file a grievance",
                    "workplace complaint",
                    "workplace grievance",
                ],
            ),
            (
                "HR",
                [
                    "show my leave balance",
                    "check my leave balance",
                    "my leave balance",
                    "submit a leave request",
                    "show my attendance",
                    "show my payroll",
                    "my payroll information",
                    "update my phone number",
                    "update my email",
                    "update my address",
                    "change my iban",
                    "change my bank account",
                    "request an employment certificate",
                ],
            ),
            (
                "CONSULTANT",
                [
                    "what does article",
                    "what does the law say",
                    "according to saudi labor law",
                    "according to company policy",
                    "what is the company policy",
                    "what does company policy say",
                ],
            ),
        ]

        for intent, phrases in fast_paths:
            if any(phrase in normalized for phrase in phrases):
                return intent

        prompt = f"""
You are an intent classifier for an HR assistant.

Classify the user's request into EXACTLY ONE category.

HR
- Employee-specific information.
- User's own employee data.
- Leave balance or remaining leave.
- Employee status or personal employment information.
- HR actions such as submitting leave.
- Updating personal information such as phone, email, or address.
- Payroll-related employee requests.
- Attendance-related employee requests.
- Bank or IBAN change requests.
- Employment or HR certificate requests.
- Other employee-specific HR actions.

CONSULTANT
- General HR policies.
- Saudi labor law.
- Articles and legal rules.
- General rights and requirements.
- Policy conditions and exceptions.
- Questions that can be answered from policy documents
  without employee-specific information.

BOTH
- The request requires BOTH:
  1. Employee-specific information from HR.
  2. General policy, law, or policy interpretation from Consultant.

GRIEVANCE
- Employee complaints or grievances about:
  - salary deductions
  - unfair treatment
  - workplace issues
  - violations of Saudi Labor Law
  - violations of company HR policies
  - employment-related complaints
  - disputes with the employer or workplace

Examples:

"I want to submit a complaint about my salary deduction."
-> GRIEVANCE

"I believe my employer violated my annual leave rights."
-> GRIEVANCE

"I want to file a grievance about unfair treatment at work."
-> GRIEVANCE

OTHER
- Requests unrelated to HR.
- Requests that cannot clearly be classified.

Examples:

"How many annual leave days do I have remaining?"
-> HR

"What does Article 109 say about annual leave?"
-> CONSULTANT

"Am I entitled to 30 days of annual leave?"
-> BOTH

"Submit a leave request for me."
-> HR

"Can I postpone my annual leave to next year?"
-> CONSULTANT

"Show my attendance record."
-> HR

"Update my phone number."
-> HR

"Change my IBAN."
-> HR

"Request an employment certificate."
-> HR

"Show my payroll information."
-> HR

"How does the company calculate annual leave?"
-> CONSULTANT

"Can I change my bank account according to company policy?"
-> BOTH

Return ONLY valid JSON:

{{"intent": "GRIEVANCE"}}

Allowed intents:
HR
CONSULTANT
BOTH
GRIEVANCE
OTHER

User request:
{query}
"""

        # One retry: a transient LLM/JSON failure used to silently turn
        # a clear HR question into OTHER (seen in the eval baseline).
        for attempt in range(2):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You classify HR requests. "
                                "Return JSON only."
                            ),
                        },
                        {
                            "role": "user",
                            "content": prompt,
                        },
                    ],
                    temperature=0,
                    # One-word JSON answer: low reasoning + a small cap keeps
                    # this call from reserving most of the per-minute budget.
                    **fast_llm_options(512),
                )

                content = (
                    response.choices[0]
                    .message.content
                    .strip()
                )

                # Remove markdown code fences if returned
                if content.startswith("```"):
                    content = content.replace(
                        "```json",
                        "",
                    )
                    content = content.replace(
                        "```",
                        "",
                    )
                    content = content.strip()

                result = json.loads(content)

                intent = result.get(
                    "intent",
                    "OTHER",
                )

                allowed_intents = {
                    "HR",
                    "CONSULTANT",
                    "BOTH",
                    "GRIEVANCE",
                    "OTHER",
                }

                if intent not in allowed_intents:
                    return "OTHER"

                return intent

            except Exception:
                logger.warning(
                    "Intent classification failed (attempt %d/2)",
                    attempt + 1,
                    exc_info=True,
                )

        return "OTHER"

    # =========================================================
    # 3. BOTH REQUEST ORDER
    # =========================================================


    # =========================================================
    # 4. HIGH-RISK SECURITY ACTION
    # =========================================================

    def handle_high_risk(
        self,
        query: str,
        user: dict,
        security_result: dict,
    ) -> dict:
        """
        Send HIGH-risk security events to the Manager Agent.
        The Manager decides the security action.
        """

        manager_result = self._run_manager({
            "query": query,
            "user": user,
            "hr_result": {},
            "consultant_result": {},
            "security": security_result,
            "intent": "SECURITY_BLOCK",
            "execution_order": [],
        })

        return {
            "status": manager_result.get(
                "decision",
                "FAIL",
            ),
            "response": manager_result.get("response") or (
                "Your request was blocked because a "
                "high-risk security threat was detected."
            ),
            "sources": [],
            "intent": "SECURITY_BLOCK",
            "execution_order": ["MANAGER"],
            "security": security_result,
            "employee_id": user.get(
                "employee_id"
            ),
        }

    # =========================================================
    # 5. ROUTE TO HR
    # =========================================================

    def run_hr(
        self,
        query: str,
        user: dict,
    ) -> dict:

        try:
            return self.hr_agent.run({
                "query": query,
                "user": user,
                "employee_id": user.get(
                    "employee_id"
                ),
            })
        except Exception:
            logger.exception("HR Agent failed")
            return dict(_EMPTY_HR_RESULT)

    # =========================================================
    # 6. ROUTE TO CONSULTANT
    # =========================================================

    def run_consultant(
        self,
        query: str,
        lang: str = "en",
    ) -> dict:

        try:
            return self.consultant_agent.run({
                "query": query,
                "lang": lang,
            })
        except Exception as exc:
            logger.exception("Consultant Agent failed")
            return _failed_consultant_result(str(exc))

    def _run_manager(self, manager_input: dict) -> dict:
        """Manager is the final gate, so a crash there must still produce
        a well-formed FAIL instead of a 500 / KeyError on "decision"."""

        try:
            result = self.manager_agent.run(manager_input)
        except Exception:
            logger.exception("Manager Agent failed")
            result = {}

        if not isinstance(result, dict) or "decision" not in result:
            return {
                "decision": "FAIL",
                "reasons": ["Manager Agent failed."],
                "response": (
                    "Something went wrong while checking your request. "
                    "Please try again."
                ),
            }

        return result
    # =========================================================
    # 6.5 EXPLAIN PENDING APPROVAL
    # =========================================================

    def explain_pending_approval(
        self,
        proposal_id: str,
    ) -> dict:
        """
        Build a Decision Brief for a pending approval.

        Flow:
        1. Get the proposed action.
        2. Find its pending approval.
        3. Ask HR Agent for employee context.
        4. Get historical precedent from HR Agent helper.
        5. Ask Consultant Agent for the relevant policy.
        6. Ask Manager Agent to validate/compose the explanation.
        7. Return a structured Decision Brief.
        """

        if not proposal_id:
            return {
                "status": "FAIL",
                "error": "proposal_id is required.",
            }

        conn = get_connection()

        try:
            # -------------------------------------------------
            # 1. Get proposed action
            # -------------------------------------------------

            proposal = get_proposed_action(
                conn,
                proposal_id,
            )

            if proposal is None:
                return {
                    "status": "FAIL",
                    "error": "Proposed action not found.",
                    "proposal_id": proposal_id,
                }

            # -------------------------------------------------
            # 2. Find pending approval
            # -------------------------------------------------

            pending_approvals = list_pending_approvals(
                conn,
                status="pending",
            )

            approval = next(
                (
                    item
                    for item in pending_approvals
                    if item.get("proposal_id") == proposal_id
                ),
                None,
            )

            if approval is None:
                return {
                    "status": "FAIL",
                    "error": "No pending approval found for this proposal.",
                    "proposal_id": proposal_id,
                }

            employee_id = proposal.get("employee_id")
            action_type = proposal.get("action_type")

            # -------------------------------------------------
            # 3. HR Agent
            # -------------------------------------------------

            hr_result = self.hr_agent.run({
                "query": (
                    "Review the employee record for this "
                    "pending HR approval."
                ),
                "user": {
                    "employee_id": employee_id,
                    "role": "employee",
                },
                "employee_id": employee_id,
            })

            # Historical precedent is already implemented
            # inside HR Agent. We call that helper here instead
            # of putting SQL inside the Orchestrator.
            precedent = get_historical_precedent(
                conn,
                employee_id,
                action_type,
            )

            hr_facts = dict(
                hr_result.get("facts") or {}
            )

            hr_facts["historical_precedent"] = precedent

            hr_result["facts"] = hr_facts

            # Prevent Manager from submitting the same action
            # again while explaining an existing approval.
            manager_hr_result = dict(hr_result)
            manager_hr_result["proposed_action"] = None

            # -------------------------------------------------
            # 4. Consultant Agent
            # -------------------------------------------------

            # Reuse Consultant's dedicated action_type -> policy-question
            # mapping (see _query_for_action_type in consultant_agent.py)
            # instead of a one-off free-text prompt here. The bespoke prompt
            # this replaced retrieved unrelated law/policy text for actions
            # like "bank_update" and produced a brief claiming no policy
            # applied, even though Consultant's own action_type lookup
            # finds the right citation (e.g. LAW006 for bank/IBAN changes).
            consultant_result = self.consultant_agent.run({
                "action_type": action_type,
            })

            # -------------------------------------------------
            # 5. Manager Agent
            # -------------------------------------------------

            manager_result = self.manager_agent.run({
                "query": (
                    "Explain this pending approval using the "
                    "employee facts, historical precedent, "
                    "and policy evidence."
                ),
                "user": {
                    "employee_id": employee_id,
                    "role": "employee",
                },
                "hr_result": manager_hr_result,
                "consultant_result": consultant_result,
            })

            # -------------------------------------------------
            # 6. Decision Brief
            # -------------------------------------------------

            return {
                "status": "SUCCESS",
                "proposal_id": proposal_id,
                "approval_id": approval.get("approval_id"),

                "brief": {
                    "action_type": action_type,
                    "employee_id": employee_id,
                    "action_summary": approval.get(
                        "action_summary"
                    ),
                    "risk_level": (
                        proposal.get("risk_level")
                        or approval.get("risk_level")
                    ),

                    "historical_precedent": precedent,

                    # End-of-service file for terminations (rendered by the
                    # UI's _render_termination_profile). Restored: dropped
                    # by fe67d6e along with the staffing fast path.
                    "termination_profile": (
                        get_termination_profile(conn, proposal)
                        if action_type == "termination"
                        else None
                    ),

                    "policy": {
                        "recommendation": consultant_result.get(
                            "recommendation",
                            "",
                        ),
                        "sources": consultant_result.get(
                            "sources",
                            [],
                        ),
                        "conflicts": consultant_result.get(
                            "conflicts",
                            [],
                        ),
                    },

                    "manager": {
                        "decision": manager_result.get(
                            "decision",
                            "FAIL",
                        ),
                        "reasons": manager_result.get(
                            "reasons",
                            [],
                        ),
                        "response": manager_result.get(
                            "response",
                            "",
                        ),
                    },
                },
            }

        except Exception as exc:
            return {
                "status": "FAIL",
                "error": str(exc),
                "proposal_id": proposal_id,
            }

        finally:
            conn.close()
    # =========================================================
    # 7. MAIN ORCHESTRATOR
    # =========================================================

    def run(self, input: dict) -> dict:
        """Language-translation edge around `_run`.

        Detects Arabic vs. English on the raw prompt, translates Arabic
        to English before `_run` (the unmodified pipeline) ever sees it,
        then translates the pipeline's `response` string back to Arabic.
        HR/Consultant/Manager, the DB, and the RAG index only ever see
        English. English prompts skip both translation calls entirely.
        """

        original_query = str(input.get("query") or "")

        language = detect_language(original_query)

        translated_query = original_query

        # Screen the raw prompt before it reaches the translation LLM, so an
        # Arabic injection attempt is blocked instead of being "translated".
        # English prompts are screened again (identically) inside `_run`.
        if language == "ar":
            original_risk = self.assess_query_risk(original_query)
            if original_risk["risk"] == "HIGH":
                result = self.handle_high_risk(
                    query=original_query,
                    user=input.get("user") or {},
                    security_result=original_risk,
                )
                try:
                    result["response"] = translate_to_arabic(result["response"])
                except Exception:
                    pass
                return result

        if language == "ar" and original_query.strip():
            try:
                translated_query = translate_to_english(original_query)
            except Exception:
                # Best-effort: fall back to the original text rather than
                # failing the whole request if translation is unavailable.
                translated_query = original_query

        internal_input = dict(input)
        internal_input["query"] = translated_query
        internal_input["original_query"] = original_query
        internal_input["lang"] = language

        result = self._run(internal_input)
        blocks = result.pop("_response_blocks", None)

        if language == "ar":
            response_text = result.get("response")
            if blocks:
                result["response"] = _render_arabic(blocks)
            elif isinstance(response_text, str) and response_text.strip():
                try:
                    result["response"] = translate_to_arabic(response_text)
                except Exception:
                    pass

        return result

    def _run(self, input: dict) -> dict:
        """
        Main orchestration workflow:

        1. Security guardrail
        2. Intent classification
        3. Route request
        4. Collect results
        5. Send results to Manager
        6. Return final response
        """

        identity_visible = input.get("identity_visible")

        query = str(
            input.get("query") or ""
        )

        user = input.get("user") or {}

        # Reader's language: the Consultant answers Arabic readers in Arabic.
        lang = input.get("lang") or "en"

        # =====================================================
        # STEP 1 — SECURITY
        # =====================================================

        security_result = self.assess_query_risk(query)

        # HIGH → STOP EVERYTHING
        if security_result["risk"] == "HIGH":
            return self.handle_high_risk(
                query=query,
                user=user,
                security_result=security_result,
            )

        # =====================================================
        # STEP 2 — INTENT
        # =====================================================

        intent = self.classify_intent(query)

        # Grievances may legitimately name a colleague; everything else
        # asking for another employee's personal data is refused.
        if intent != "GRIEVANCE" and _references_other_employee(query, user):
            return self.handle_high_risk(
                query=query,
                user=user,
                security_result={
                    "detected": True,
                    "type": "UNAUTHORIZED_ACCESS",
                    "risk": "HIGH",
                    "reason": "Employees can only see their own records.",
                },
            )

        # =====================================================
        # STEP 3 — DEFAULT RESULTS
        # =====================================================

        hr_result = {
            "facts": {},
            "proposed_action": None,
            "sources": [],
        }

        consultant_result = {
            "recommendation": "",
            "conflicts": [],
            "policy_analysis": "",
            "sources": [],
        }

        execution_order = []

        # =====================================================
        # STEP 4 — ROUTING
        # =====================================================

        if intent == "HR":

            execution_order.append("HR")

            hr_result = self.run_hr(
                query=query,
                user=user,
            )

        elif intent == "CONSULTANT":

            execution_order.append("CONSULTANT")

            consultant_result = self.run_consultant(
                query=query,
                lang=lang,
            )
        elif intent == "BOTH":

            execution_order.extend(["HR", "CONSULTANT"])

            with ThreadPoolExecutor(max_workers=2) as executor:
                hr_future = executor.submit(
                    self.run_hr,
                    query=query,
                    user=user,
                )
                consultant_future = executor.submit(
                    self.run_consultant,
                    query=query,
                    lang=lang,
                )

                hr_result = hr_future.result()
                consultant_result = consultant_future.result()

        elif intent == "GRIEVANCE":

            # The orchestrator detected a grievance.
            # If the employee has not chosen an identity option yet,
            # stop here and ask the UI to show Hide / Show.
            if identity_visible is None:
                return {
                    "status": "IDENTITY_REQUIRED",
                    "response": "",
                    "sources": [],
                    "intent": "GRIEVANCE",
                    "execution_order": [],
                    "security": security_result,
                    "needs_identity_choice": True,
                }

            execution_order.append("CONSULTANT")

            consultant_result = self.run_consultant(
                query=query,
            )

            # If the employee chose to show their identity,
            # employee-specific HR data can be used.
            if identity_visible:
                execution_order.append("HR")

                hr_result = self.run_hr(
                    query=query,
                    user=user,
                )

            else:
                # Anonymous grievance:
                # do not expose employee-specific information.
                hr_result = {
                    "facts": {},
                    "proposed_action": None,
                    "sources": [],
                }

            # Governance checks the grievance workflow.
            manager_result = self._run_manager({
                "query": query,
                "user": user,
                "security": security_result,
                "intent": "GRIEVANCE",
                "hr_result": hr_result,
                "consultant_result": consultant_result,
                "execution_order": execution_order,
            })

            # Governance PASS means the grievance can proceed
            # to human HR review. It is NOT the final grievance decision.
            if manager_result.get("decision") == "PASS":

                conn = get_connection()

                try:
                    grievance = create_grievance(
                        conn,
                        employee_id=user.get("employee_id"),
                        identity_visible=bool(identity_visible),
                        complaint=input.get("original_query") or query,
                        consultant_recommendation=consultant_result.get(
                            "recommendation",
                            "",
                        ),
                        sources=json.dumps(
                            consultant_result.get("sources", []),
                            ensure_ascii=False,
                        ),
                    )

                    conn.commit()
                    grievance_db = database_path(conn)

                finally:
                    conn.close()

                # Warm the translation cache in the background so HR
                # reviewers in either language see it without waiting.
                prefetch_translations(
                    [
                        grievance.get("complaint"),
                        grievance.get("consultant_recommendation"),
                    ],
                    grievance_db,
                )

                return {
                    "status": "PENDING_HR_REVIEW",
                    "response": (
                        "Your grievance has been submitted successfully. "
                        "HR will review your case."
                    ),
                    "sources": consultant_result.get(
                        "sources",
                        [],
                    ),
                    "intent": "GRIEVANCE",
                    "execution_order": execution_order,
                    "security": security_result,
                    "identity_visible": bool(identity_visible),
                    "hr_review": True,
                    "grievance_id": grievance.get("grievance_id"),
                    "_response_blocks": [[(
                        "Your grievance has been submitted successfully. "
                        "HR will review your case.",
                        arabic_text.GRIEVANCE_SUBMITTED_AR,
                    )]],
                }

            return {
                "status": "FAIL",
                "response": manager_result.get(
                    "response",
                    "Your grievance could not be submitted.",
                ),
                "sources": consultant_result.get(
                    "sources",
                    [],
                ),
                "intent": "GRIEVANCE",
                "execution_order": execution_order,
                "security": security_result,
                "identity_visible": bool(identity_visible),
                "hr_review": False,
                "_response_blocks": manager_result.get("response_blocks"),
            }

        
        # =====================================================
        # STEP 5 — MANAGER
        # =====================================================

        manager_result = self._run_manager({
            "query": query,
            "user": user,

            # Security context
            "security": security_result,

            # Detected intent
            "intent": intent,

            # Agent outputs
            "hr_result": hr_result,
            "consultant_result": consultant_result,

            # Useful for tracing
            "execution_order": execution_order,
        })

        # =====================================================
        # STEP 6 — FINAL RESPONSE
        # =====================================================

        return {
            "status": manager_result.get("decision", "FAIL"),
            "response": manager_result.get("response", ""),
            "sources": (
                hr_result.get("sources", [])
                + consultant_result.get("sources", [])
            ),
            "intent": intent,
            "execution_order": execution_order,
            "security": security_result,
            "_response_blocks": manager_result.get("response_blocks"),
        }