from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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


@app.exception_handler(NotImplementedError)
async def not_implemented_handler(request: Request, exc: NotImplementedError) -> JSONResponse:
    return JSONResponse(
        status_code=501,
        content={"detail": str(exc) or "Manual implementation pending"},
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


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
