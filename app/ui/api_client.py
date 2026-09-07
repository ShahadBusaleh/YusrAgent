from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402


def _base() -> str:
    return os.getenv("API_BASE_URL", get_settings().api_base_url).rstrip("/")


def _headers() -> dict[str, str]:
    token = st.session_state.get("access_token")
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _refresh() -> bool:
    refresh_token = st.session_state.get("refresh_token")
    if not refresh_token:
        return False
    try:
        response = httpx.post(
            f"{_base()}/auth/refresh",
            json={"refresh_token": refresh_token},
            timeout=20.0,
        )
    except httpx.HTTPError:
        return False
    if response.status_code != 200:
        return False
    data = response.json()
    st.session_state.access_token = data["access_token"]
    st.session_state.refresh_token = data["refresh_token"]
    return True


def request(method: str, path: str, **kwargs) -> httpx.Response:
    kwargs.setdefault("timeout", 30.0)
    kwargs.setdefault("headers", {})
    kwargs["headers"] = {**_headers(), **kwargs["headers"]}
    url = f"{_base()}{path}"
    response = httpx.request(method, url, follow_redirects=True, **kwargs)
    if response.status_code == 401 and _refresh():
        kwargs["headers"] = {**_headers(), **kwargs.get("headers", {})}
        response = httpx.request(method, url, follow_redirects=True, **kwargs)
    return response


def raise_for_api(response: httpx.Response) -> dict | list | None:
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except Exception:
            detail = response.text
        raise RuntimeError(f"{response.status_code}: {detail}")
    if response.status_code == 204 or not response.content:
        return None
    return response.json()
