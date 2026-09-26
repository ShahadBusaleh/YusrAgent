from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.deps import lang_from_header
from app.api.error_messages import localize_detail
from app.config import get_settings
from app.llm import pool_status

from app.api.routers import (
    agent,
    approvals,
    audit,
    auth,
    employees,
    experience_gap,
    grievances,
    growth,
    leave,
    onboarding,
    payroll,
    proposed_actions,
    records,
    regulations,
    users,
)

app = FastAPI(title="Yusor API", version="0.1.0")
from app.rag.embeddings import get_embedder

get_embedder()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(StarletteHTTPException)
async def localized_http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Same as FastAPI's default handler, except `detail` is translated to
    Arabic when the UI's language (Accept-Language) is Arabic."""
    lang = lang_from_header(request.headers.get("accept-language"))
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": localize_detail(exc.detail, lang)},
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(NotImplementedError)
async def not_implemented_handler(request: Request, exc: NotImplementedError) -> JSONResponse:
    return JSONResponse(
        status_code=501,
        content={"detail": str(exc) or "Manual implementation pending"},
    )


@app.get("/health")
def health() -> dict:
    # Key counts only (never key values): how many LLM keys are configured
    # and how many are resting after a rate limit.
    return {"status": "ok", "llm_keys": pool_status(get_settings())}


app.include_router(auth.router)
app.include_router(employees.router)
app.include_router(leave.router)
app.include_router(proposed_actions.router)
app.include_router(approvals.router)
app.include_router(grievances.router)
app.include_router(experience_gap.router)
app.include_router(growth.router)
app.include_router(audit.router)
app.include_router(users.router)
app.include_router(agent.router)
app.include_router(payroll.router)
app.include_router(onboarding.router)
app.include_router(records.router)
app.include_router(regulations.router)
