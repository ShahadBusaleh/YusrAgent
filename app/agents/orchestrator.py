import json

from openai import OpenAI

from app.agents.hr_agent import HRAgent
from app.agents.consultant_agent import ConsultantAgent
from app.agents.manager_agent import ManagerAgent
from app.config import get_settings
from app.security.governance import detect_prompt_injection


class OrchestratorAgent:

    def __init__(self):
        settings = get_settings()

        self.client = OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,)

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
        if any(pattern in query_lower for pattern in high_risk_patterns):
            return {
                "detected": True,
                "type": "PROMPT_INJECTION",
                "risk": "HIGH",
                "reason": "High-risk prompt injection attempt detected.",
            }

        # Check explicit MEDIUM patterns
        if any(pattern in query_lower for pattern in medium_risk_patterns):
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
        OTHER
        """

        prompt = f"""
You are an intent classifier for an HR assistant.

Classify the user's request into EXACTLY ONE category.

HR
- Employee-specific information.
- User's own employee data.
- Leave balance or remaining leave.
- Employee status or personal employment information.
- HR actions such as submitting leave or updating contact information.

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

Return ONLY valid JSON:

{{"intent": "HR"}}

Allowed intents:
HR
CONSULTANT
BOTH
OTHER

User request:
{query}
"""

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
            )

            content = (
                response.choices[0]
                .message.content
                .strip()
            )

            # Remove markdown code fences if returned
            if content.startswith("```"):
                content = content.replace(
                    "```json", ""
                )
                content = content.replace(
                    "```", ""
                )
                content = content.strip()

            result = json.loads(content)

            intent = result.get("intent", "OTHER")

            allowed_intents = {
                "HR",
                "CONSULTANT",
                "BOTH",
                "OTHER",
            }

            if intent not in allowed_intents:
                return "OTHER"

            return intent

        except Exception:
            return "OTHER"

    # =========================================================
    # 3. BOTH REQUEST ORDER
    # =========================================================

    def detect_order(self, query: str) -> str:
        """
        Detect which part appears first in a BOTH request.

        Returns:
            HR_FIRST
            CONSULTANT_FIRST
        """

        query_lower = query.lower()

        hr_keywords = [
            "do i have",
            "how many",
            "my balance",
            "my remaining",
            "am i entitled",
            "my leave",
            "my employee",
            "my status",
            "submit",
            "update my",
        ]

        consultant_keywords = [
            "article",
            "law",
            "policy",
            "rule",
            "rules",
            "according to",
            "what does",
            "requirements",
            "conditions",
            "exceptions",
        ]

        hr_positions = [
            query_lower.find(keyword)
            for keyword in hr_keywords
            if query_lower.find(keyword) != -1
        ]

        consultant_positions = [
            query_lower.find(keyword)
            for keyword in consultant_keywords
            if query_lower.find(keyword) != -1
        ]

        hr_position = (
            min(hr_positions)
            if hr_positions
            else float("inf")
        )

        consultant_position = (
            min(consultant_positions)
            if consultant_positions
            else float("inf")
        )

        if hr_position < consultant_position:
            return "HR_FIRST"

        return "CONSULTANT_FIRST"

    # =========================================================
    # 4. HIGH-RISK SECURITY ACTION
    # =========================================================

    def handle_high_risk(self, query: str, user: dict, security_result: dict) -> dict:
        """
        Send HIGH-risk security events to the Manager Agent.
        The Manager decides the security action.
        """
    
        manager_result = self.manager_agent.run({
            "query": query,
            "user": user,
            "hr_result": {},
            "consultant_result": {},
            "security": security_result,
            "intent": "SECURITY_BLOCK",
            "execution_order": [],
        })
    
        return {
            "status": manager_result.get("decision", "FAIL"),
            "response": manager_result.get(
                "response",
                "Your request was blocked because a high-risk security threat was detected."
            ),
            "sources": [],
            "intent": "SECURITY_BLOCK",
            "execution_order": ["MANAGER"],
            "security": security_result,
    
            # Manager's security decision
            "security_action": manager_result.get("security_action"),
            "account_action": manager_result.get("account_action"),
            "hr_notification": manager_result.get("hr_notification"),
    
            "employee_id": user.get("employee_id"),
        }
    # =========================================================
    # 5. ROUTE TO HR
    # =========================================================

    def run_hr(
        self,
        query: str,
        user: dict,
    ) -> dict:

        return self.hr_agent.run({
            "query": query,
            "user": user,
            "employee_id": user.get("employee_id"),
        })

    # =========================================================
    # 6. ROUTE TO CONSULTANT
    # =========================================================

    def run_consultant(
        self,
        query: str,
    ) -> dict:

        return self.consultant_agent.run({
            "query": query,
        })

    # =========================================================
    # 7. MAIN ORCHESTRATOR
    # =========================================================

    def run(self, input: dict) -> dict:
        """
        Main orchestration workflow:

        1. Security guardrail
        2. Intent classification
        3. Route request
        4. Collect results
        5. Send results to Manager
        6. Return final response
        """

        query = str(
            input.get("query") or ""
        )

        user = input.get("user") or {}

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
            )

        elif intent == "BOTH":

            order = self.detect_order(query)

            if order == "HR_FIRST":

                execution_order.append("HR")

                hr_result = self.run_hr(
                    query=query,
                    user=user,
                )

                execution_order.append("CONSULTANT")

                consultant_result = self.run_consultant(
                    query=query,
                )

            else:

                execution_order.append("CONSULTANT")

                consultant_result = self.run_consultant(
                    query=query,
                )

                execution_order.append("HR")

                hr_result = self.run_hr(
                    query=query,
                    user=user,
                )

        # =====================================================
        # STEP 5 — MANAGER
        # =====================================================

        manager_result = self.manager_agent.run({
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
            "status": manager_result["decision"],

            "response": manager_result["response"],

            "sources": (
                hr_result.get("sources", [])
                + consultant_result.get("sources", [])
            ),

            "intent": intent,

            "execution_order": execution_order,

            "security": security_result,
        }
