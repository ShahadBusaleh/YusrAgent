# Yusor — HR Multi-Agent Assistant
### Bring Your Own Idea — Agentic AI Project Report

A policy-grounded HR system using three specialized AI agents: Execution, Policy Analysis, and Validation.

## 1. Project Overview

Our project proposes an agentic AI system for Human Resources (HR). The system is designed to help HR employees retrieve employee information, check company policies, analyze requests, and validate recommendations before a final response is delivered. Instead of relying on one AI model to complete every step, the solution uses three specialized agents with clearly separated responsibilities.

The main goal is to make HR operations faster and more consistent while reducing the risk of unsupported answers, policy violations, or AI hallucinations.

## 2. Problem We Are Trying to Solve

HR teams often need to combine information from multiple sources before responding to a request. A single case may require checking an employee record, reading an HR policy, comparing the facts with eligibility rules, and deciding whether the proposed action is allowed. Performing these steps manually can take time and may lead to inconsistent decisions or missed policy requirements.

A standard chatbot may also generate a confident answer without verifying the employee data or the policy source. Our proposed system addresses this problem by separating execution, policy analysis, and final validation into different agents.

## 3. Target Users

- HR employees who handle routine employee requests and information checks.
- HR specialists and consultants who need policy-based analysis.
- HR managers who supervise decisions and ensure policy compliance.
- Organizations that want to automate repetitive HR tasks while keeping sensitive decisions controlled and auditable.

## 4. Market Gap Analysis — Saudi Market

### 4.1 Market Context

HR in Saudi Arabia runs under conditions that differ from most markets:

- **Vision 2030 digital transformation.** Private-sector employers are moving HR work from paper and spreadsheets to digital platforms, and government services are already digital.
- **Mandatory government platforms.** Employers must work with **Qiwa** (employment contracts, Saudization / Nitaqat), **Mudad** (Wage Protection System, WPS), and **GOSI** (social insurance). Each one covers a single compliance transaction.
- **A changing legal base.** The Saudi Labor Law was amended in 2025, and HRSD issues ministerial decisions regularly. HR answers have to follow the *current* text.
- **Data protection.** The Personal Data Protection Law (PDPL), enforced by SDAIA, sets rules on how employee personal data is processed, and where it goes, including when it is sent outside the Kingdom.
- **Bilingual workforce.** Saudi and expatriate employees work side by side, so HR must communicate in both Arabic and English.

### 4.2 Existing Solutions

| Category | Examples | Strengths | Gap Yusor targets |
|---|---|---|---|
| Regional cloud HRMS / payroll | Jisr, ZenHR, Bayzat, Menaitech | Payroll, WPS/Mudad files, GOSI, attendance, self-service portals; Arabic UI | Mostly form- and menu-driven. AI features, where present, do not give answers grounded in cited Labor Law articles or checked by a validation step. |
| Global HCM suites | SAP SuccessFactors, Oracle HCM, Workday | Broad functionality, enterprise AI copilots | High cost and long rollouts suited to large enterprises; copilots are not built around the Saudi Labor Law text; localization often depends on partners. |
| Government platforms | Qiwa, Mudad, GOSI | Official source of truth for contracts, wages and insurance | Transactional only. They do not advise, and they know nothing about an employer's internal policies. |
| General AI chatbots | ChatGPT, Microsoft Copilot, Arabic LLM assistants | Fluent Arabic and English; easy to use | No access to employee data, no citations to current law, no approval workflow or audit trail, and PDPL concerns when staff paste employee data into them. |

> Note: competitor capabilities are based on public product materials at the time of writing and change quickly. Re-check them before any external submission.

### 4.3 Identified Gaps and How Yusor Addresses Them

| # | Market gap | Why it matters in KSA | Yusor's response |
|---|---|---|---|
| 1 | No AI assistant gives HR answers **grounded in Saudi Labor Law with citations**, in Arabic | Wrong answers on leave, notice periods or end-of-service create legal and labor-court risk | Consultant Agent answers from Labor Law, company policies and WPS documents using hybrid RAG, and cites every claim as `[Source: ID]` |
| 2 | Tools lag behind **regulation changes** | The 2025 amendments and frequent HRSD decisions make static policy content stale | Regulation Update Agent watches laws.boe.gov.sa and hrsd.gov.sa, proposes text updates, needs four-eyes approval, then re-indexes RAG |
| 3 | Low **trust in AI output** (hallucinations) | HR decisions affect pay and employment status | Manager Agent validates every answer (PASS/FAIL) against the retrieved evidence before the user sees it |
| 4 | AI either only chats or acts without control | Employers need automation **and** accountability (PDPL, internal audit) | Low-risk actions run automatically; risky actions wait for human approval; every step goes to `audit_log`; role-based access control |
| 5 | Approvers decide without context | Inconsistent approvals lead to grievances and disputes | AI **Decision Brief**: employee context, historical precedent, policy citation, termination profile |
| 6 | **End-of-service and final-settlement** calculations are error-prone | They are among the most common sources of labor disputes | Offboarding flow computes the notice period and final settlement under Labor Law Art. 74–88 |
| 7 | Few safe, **confidential grievance** channels | Employees often avoid raising issues openly | Anonymous or named grievances, reviewed by HR |
| 8 | Little **workforce development insight** for SMEs | Vision 2030 prioritises human-capability development | Team Insights (experience gap per department) and CV-based Growth Opportunities |
| 9 | Arabic is often a translation layer, not native | A mixed Saudi / expatriate workforce | Native bilingual chat: the user asks in either language and gets the answer in the same language |

### 4.4 Positioning

Yusor does **not** replace a payroll HRMS or the government platforms. It is an **AI advisory and self-service layer** on top of them: it answers "what does the law and our policy say, and what does it mean for this employee?", prepares the action, and keeps a human in control of risky decisions. The initial target segment is small and mid-size private-sector employers, which have to meet the same legal obligations as large companies but usually do not have in-house legal or HR-policy expertise.

### 4.5 Remaining Gaps (Our Own Limitations)

- **No live integration with Qiwa, Mudad or GOSI yet.** The prototype uses a mock SQLite HR database. Real deployment needs these integrations or an HRMS connector.
- **Data residency.** The current LLM provider and vector database are cloud services that may be hosted outside KSA. A production version must meet PDPL rules on cross-border transfer, for example by using in-Kingdom hosting or a local model.
- **Market validation.** This gap analysis is based on desk research. Interviews with HR teams at Saudi SMEs are needed to confirm demand and willingness to pay.

## 5. Proposed Multi-Agent Solution

The system consists of three agents. Each agent has a clear role, uses specific tools, and passes its output to the next agent. This separation creates checks and balances within the AI system.

### 5.1 HR Agent — Execution Agent

The HR Agent receives the HR employee's request, understands the task, and identifies the information needed to answer it. It acts as the execution layer of the system.

- Retrieves employee information from a Mock HR Database.
- Uses Retrieval-Augmented Generation (RAG) to find relevant HR policies or internal documents when needed.
- Organizes the retrieved facts and prepares an initial response or proposed action.
- Passes the result and its supporting sources to the Consultant Agent for review.

### 5.2 Consultant Agent — Policy & Analysis Agent

The Consultant Agent reviews the HR Agent's result from an HR policy and business perspective. Its main responsibility is to verify whether the proposed answer or action is consistent with the organization's HR policies.

- Retrieves the relevant policies using RAG.
- Compares employee facts with the requirements stated in the policy.
- Identifies policy conflicts, missing conditions, or exceptions.
- Produces a policy-grounded recommendation for the Manager Agent.

### 5.3 Manager Agent — Validation & Supervisor Agent

The Manager Agent is the final validation layer. It reviews the work of both the HR Agent and the Consultant Agent before the system returns a result to the HR employee.

It checks whether the data is accurate, the correct tools and sources were used, the recommendation follows policy, the agents stayed within their permissions, and the response is grounded in retrieved evidence rather than unsupported assumptions.

If the result is correct, the Manager Agent returns PASS. If it detects an error, unsupported claim, missing evidence, or policy problem, it returns FAIL and sends the request back to the HR Agent for correction.

## 6. Authentication & Role-Based Access Control

The system requires login — including for the plain self-service case (an
employee logging in only to check their own leave balance). Access control is
handled by two new database tables:

- **users** — one account per employee (`employee_id` FK, 1:1), holding
  `username`, `password_hash`, `role`, `is_active`, `created_at`, `last_login_at`.
  Kept as its own table rather than columns on `employees` so login/session
  concerns stay separate from HR profile data.
- **roles** — a small lookup table defining the four roles in the system:
  - `employee` — self-service: check own leave balance, submit leave/personal-info
    requests, view own request status.
  - `hr_specialist` — can view and update employee records (mobile, address,
    etc.) on behalf of employees; cannot approve high-risk actions.
  - `hr_manager` — reviews and approves/rejects items in `pending_approvals`
    (leave requests, sensitive changes) within their scope.
  - `admin` — full system access: manages users/roles, oversees `audit_log`,
    system configuration.

RBAC is enforced at the API layer: every endpoint checks the caller's `role`
before executing, and the Manager Agent's validation step should treat
"agent stayed within its permissions" (Section 5.3) as checking against the
requesting user's role, not just the agent's own scope.

## 7. System Architecture (from architecture diagram)

**Overall flow:** User → Identity & Access → Policy Engine → Orchestrator Agent → (HR Specialist Agent + RAG Specialist Agent, in parallel) → Governance & Security Layer → Response to User (Pass / Fail / Replan-Escalate). An Audit & Observability layer runs alongside the whole pipeline, logging every step.

**User & Access layer**
- User: Employee or HR Staff.
- Identity & Access: login via the `users` table (Section 6), identity verification, user context.
- Policy Engine: RBAC via `users.role` / `roles` (Section 6), permission check, data access policy.

**1. Orchestrator Agent** — responsibilities: intent classification, task decomposition, agent selection, state management, result synthesis, error handling. It performs five steps:
1. Understand — understand user request & intent.
2. Plan — decompose into sub-tasks.
3. Delegate — select the right specialist agent.
4. Monitor — monitor progress & handle errors.
5. Synthesize — combine results & generate final response.

**2. HR Specialist Agent** — domain expert for structured HR data and operations. Capabilities: employee info, leave management, payroll information, benefits & claims, HR operations. It handles HR reasoning & calculations, HR workflows & validations, tool/API calling, and transactional tasks. It connects to the **HR Tool Layer**: Employee API, Leave API, Payroll API, Benefits API, HR Database (SQL).

**3. RAG Specialist Agent** — expert for retrieving and providing evidence from HR knowledge. Capabilities: policy search, document Q&A, regulations, procedures, guidelines. It handles query understanding & rewrite, hybrid retrieval (vector + keyword), reranking & filtering, and evidence with citations. It connects to the **Knowledge Layer**: HR Policies, Employee Handbook, Regulations & Laws, Procedures, Benefits Documents, all backed by a Vector Database.

**Governance & Security Layer (cross-cutting)** — six checks applied to every response:
- Authorization: enforce access control, user/data/tool-level permissions, least privilege.
- PII Protection: detect and mask PII, prevent data leakage, data minimization.
- Policy Enforcement: enforce HR policies, business rules, compliance checks.
- Prompt Injection Defense: detect malicious prompts, block injection attempts, sanitize inputs & docs.
- Output Validation: groundedness check, hallucination detection, source verification.
- Rate Limit & Safety: rate limiting, abuse detection, safe interactions.
- Human Approval: required for critical actions, high-risk operations, escalation rules.

**Outcomes:**
- PASS (Approve) — all checks passed; safe & compliant response delivered to user.
- Response to User — clear, accurate, grounded, and policy-compliant answer with sources.
- FAIL (Reject) — one or more checks failed; replan, correct, or escalate to human review.
- Replan / Escalate — orchestrator re-plans, requests clarification, or escalates to a human for review.

**Audit & Observability layer** (runs alongside the pipeline): logs every action, traces agents & tools, tracks metrics & monitoring, produces compliance reports.

**Key benefits called out in the diagram:** secure by design, accurate & grounded, policy compliant, auditable & traceable, scalable & extensible, human-in-the-loop.

**Worked example shown in the diagram:**
- User request: "How many annual leave days do I have remaining and can I carry them forward?"
- Orchestrator plan: (1) Get leave balance → HR Specialist. (2) Get carry-over policy → RAG Specialist. (3) Combine results & generate answer.
- Final response: "You have 12 days of annual leave remaining. According to the Annual Leave Policy (Section 4.2), up to 5 days can be carried forward to the next year." Sources: Annual Leave Policy (v3.1); HR System (Leave Balance).

## 8. End-to-End Workflow (from workflow table)

| # | Step | Function | Tools Used |
|---|------|----------|------------|
| 1 | User Input | User submits an HR request through the interface. | Streamlit |
| 2 | API Gateway | Receives the request and routes it to the system. | FastAPI |
| 3 | Manager Agent – Intent Extraction | Understands the request and extracts intent, entities, and required_agents as structured JSON. | LLM API, Structured Output, Pydantic |
| 4 | Manager Agent – Routing Logic | Determines which agents are required and controls the execution sequence. | Python |
| 5a | HR Execution Agent | Retrieves or modifies employee and HR data and performs authorized HR actions. | SQLite, Function/Tool Calling, Pydantic |
| 5b | Consultant Agent – RAG | Retrieves relevant HR policies and generates an answer based only on policy documents. | sentence-transformers, Qdrant Cloud, qdrant-client, Python Text Splitter, LLM |
| 6 | Security Layer | Evaluates the request and determines whether it can be executed automatically or requires human approval. | Python if/else Rules, SQLite |
| 6a | Auto-Execute | Executes low-risk approved actions automatically. | Python, execute_action(), SQLite |
| 6b | Pending Human Approval | Sends higher-risk actions to an approver for approval or rejection. | Streamlit, SQLite (pending_approvals) |
| 7 | Response Synthesis – Manager Agent | Combines the results from the agents and generates the final response using predefined logic. | Python Rules, LLM |
| 8 | Audit Logging | Records requests, decisions, approvals, executions, and results for auditing. | Python Logging, SQLite (audit_log) |
| 9 | Response Output | Displays the final response to the user. | Streamlit |

**Shared project tools (used across the whole system):** FastAPI (unified API layer for the system), Pydantic (input and structured-output validation), python-dotenv (API key and environment variable management), requirements.txt (dependency management and documentation).

## 9. Example Use Case

An HR employee asks: "Is this employee eligible for a specific benefit?" The HR Agent retrieves the employee's relevant information from the Mock HR Database and retrieves the benefit policy through RAG. It produces an initial answer. The Consultant Agent then compares the employee data with the policy conditions and provides a recommendation. Finally, the Manager Agent verifies that the correct employee record and policy were used and that the recommendation is fully supported. If everything is correct, it returns PASS; otherwise, it returns FAIL and sends the case back for correction.

## 10. Expected Benefits

- Faster handling of routine HR requests.
- More consistent policy-based recommendations.
- Reduced risk of human error and unsupported AI answers.
- Better grounding because responses are linked to employee data and retrieved policies.
- A built-in validation layer that can reduce hallucinations.
- Improved traceability of which data, policies, and checks were used.
- More time for HR teams to focus on complex and human-centered work.

## 11. Open Questions and Feedback Needed

We would like feedback on how much decision-making authority should be given to the AI agents. Low-risk requests may be suitable for automatic recommendations, while sensitive cases such as disciplinary actions, compensation changes, or employment decisions should likely require human approval.

We are also considering whether the three-agent structure is the best balance between reliability and complexity, or whether some responsibilities could be combined without reducing the quality of validation.

## 12. Conclusion

The HR Multi-Agent Assistant, named **Yusor**, is designed to combine employee data, HR policies, automated analysis, and independent validation in one controlled system. By assigning execution, policy analysis, and supervision to separate agents, the system can support faster HR operations while improving consistency, explainability, and reliability. The project also provides a practical way to demonstrate core agentic AI concepts such as tool use, RAG, multi-agent collaboration, iterative correction, decision-making, and human oversight.

## Suggested Tools

Streamlit, FastAPI, LLM API, Pydantic, Python, SQLite, Qdrant Cloud, qdrant-client, Text Splitter, transformers
