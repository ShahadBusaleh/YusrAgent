"""Pydantic request/response models matching agentic_hr.db columns."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class MeResponse(BaseModel):
    user_id: str
    employee_id: str
    username: str
    role: str
    is_active: bool
    full_name: str | None = None
    job_title: str | None = None


class UserOut(BaseModel):
    user_id: str
    employee_id: str
    username: str
    role: str
    is_active: bool
    created_at: str | None = None
    last_login_at: str | None = None


class UserUpdate(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    password: str | None = None


class RoleOut(BaseModel):
    role_name: str
    description: str


class EmployeeOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    employee_id: str
    national_id: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    gender: str | None = None
    email: str | None = None
    mobile: str | None = None
    address: str | None = None
    city: str | None = None
    nationality: str | None = None
    department_id: str | None = None
    department_name: str | None = None
    job_title: str | None = None
    employment_status: str | None = None
    hire_date: str | None = None
    manager_id: str | None = None
    bank_code: str | None = None
    bank_name: str | None = None
    iban: str | None = None
    basic_salary: float | None = None
    housing_allowance: float | None = None
    is_hr_approver: int | None = None
    created_at: str | None = None


class EmployeeSummary(BaseModel):
    employee_id: str
    full_name: str | None = None
    email: str | None = None
    mobile: str | None = None
    job_title: str | None = None
    employment_status: str | None = None
    department_id: str | None = None
    department_name: str | None = None
    manager_id: str | None = None


class EmployeeUpdate(BaseModel):
    mobile: str | None = None
    address: str | None = None
    city: str | None = None
    email: str | None = None


class LeaveBalanceOut(BaseModel):
    employee_id: str
    annual_entitlement: float | None = None
    annual_used: float | None = None
    annual_remaining: float | None = None
    sick_entitlement: float | None = None
    sick_used: float | None = None
    sick_remaining: float | None = None
    emergency_entitlement: float | None = None
    emergency_used: float | None = None
    emergency_remaining: float | None = None
    as_of_date: str | None = None


class LeaveRequestOut(BaseModel):
    request_id: str
    employee_id: str | None = None
    leave_type: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    days: float | None = None
    reason: str | None = None
    status: str | None = None
    submitted_at: str | None = None
    decided_at: str | None = None
    decided_by: str | None = None


class ProposedActionOut(BaseModel):
    proposal_id: str
    employee_id: str | None = None
    action_type: str | None = None
    payload_json: Any = None
    risk_level: str | None = None
    status: str | None = None
    created_at: str | None = None
    related_request_id: str | None = None

    @field_validator("payload_json", mode="before")
    @classmethod
    def parse_payload(cls, value: Any) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return value


class ApprovalOut(BaseModel):
    approval_id: str
    proposal_id: str | None = None
    employee_id: str | None = None
    action_summary: str | None = None
    risk_level: str | None = None
    status: str | None = None
    created_at: str | None = None
    decided_at: str | None = None
    decided_by: str | None = None
    decision_note: str | None = None


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "reject"]
    decision_note: str | None = None
    cover_employee_id: str | None = None


class AuditLogOut(BaseModel):
    log_id: int
    timestamp: str | None = None
    actor: str | None = None
    event_type: str | None = None
    employee_id: str | None = None
    details: str | None = None


class AgentQueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    identity_visible: bool | None = None
