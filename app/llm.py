"""Shared LLM client that spreads calls across several API keys.

The provider's limits are per organization (Groq on-demand: 8,000 tokens
per minute, 200,000 per day), so with one key a busy minute makes every
caller wait out 429 retries, and a busy day stops the whole app. With
LLM_API_KEYS="k1,k2,k3,k4" (keys from separate organizations), calls are
distributed round-robin, and a key that hits a limit rests for as long as
the provider says while the next key serves the request.

Drop-in: llm_client(settings, ...) returns an object with the same
`.chat.completions.create(**kwargs)` interface as an OpenAI client.
Callers pass their own module's `OpenAI` as `openai_cls`, so tests that
patch `<module>.OpenAI` keep intercepting every call.

Keys are never logged; logs refer to "key 2 of 4".
"""

from __future__ import annotations

import logging
import re
import threading
import time

import openai

logger = logging.getLogger(__name__)

# Longest we block a request waiting for a resting key when every key is
# resting; beyond this the rate-limit error is raised so callers fall back
# (translation shows the original text, Manager shows "please retry").
MAX_WAIT_SECONDS = 20.0
DEFAULT_MINUTE_REST = 20.0
DEFAULT_DAY_REST = 15 * 60.0

_lock = threading.Lock()
_rest_until: dict[str, float] = {}   # key -> time.monotonic() when usable again
_last_limit: dict[str, Exception] = {}  # key -> the 429 that put it to rest
_next_index = 0
_clients: dict[tuple, object] = {}

_WAIT_RE = re.compile(r"try again in\s+(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:([\d.]+)s)?", re.IGNORECASE)


def _keys_from(settings) -> list[str]:
    keys = list(getattr(settings, "llm_api_keys", None) or ())
    if not keys and getattr(settings, "llm_api_key", ""):
        keys = [settings.llm_api_key]
    return keys


def _rest_seconds(exc: Exception) -> tuple[float, str]:
    """How long a rate-limited key should rest, and which limit it hit."""
    message = str(exc)
    kind = "daily" if "per day" in message.lower() else "per-minute"
    seconds = None
    response = getattr(exc, "response", None)
    header = getattr(response, "headers", {}).get("retry-after") if response is not None else None
    try:
        seconds = float(header) if header else None
    except (TypeError, ValueError):
        seconds = None
    if seconds is None:
        match = _WAIT_RE.search(message)
        if match and any(match.groups()):
            h, m, s = (float(x) if x else 0.0 for x in match.groups())
            seconds = h * 3600 + m * 60 + s
    if seconds is None:
        seconds = DEFAULT_DAY_REST if kind == "daily" else DEFAULT_MINUTE_REST
    return seconds, kind


def _order(keys: list[str]) -> list[str]:
    """Round-robin start; keys that are resting go last, soonest-free first."""
    global _next_index
    with _lock:
        start = _next_index % len(keys)
        _next_index += 1
        now = time.monotonic()
        rotated = keys[start:] + keys[:start]
        ready = [k for k in rotated if _rest_until.get(k, 0) <= now]
        resting = sorted((k for k in rotated if _rest_until.get(k, 0) > now), key=lambda k: _rest_until[k])
    return ready + resting


def _client_for(openai_cls, key: str, base_url: str, timeout):
    # Keyed by the class object, not id(): the cache then keeps it alive,
    # so a garbage-collected test mock's id can't be reused by a new mock
    # and hand it the old mock's cached client (made the suite flaky).
    cache_key = (openai_cls, key, base_url, timeout)
    with _lock:
        client = _clients.get(cache_key)
    if client is None:
        kwargs = {"api_key": key, "base_url": base_url, "max_retries": 0}
        if timeout is not None:
            kwargs["timeout"] = timeout
        client = openai_cls(**kwargs)
        with _lock:
            _clients[cache_key] = client
    return client


def pool_status(settings) -> dict:
    """Key count and how many are resting — for health checks. No key values."""
    keys = _keys_from(settings)
    now = time.monotonic()
    with _lock:
        resting = sum(1 for k in keys if _rest_until.get(k, 0) > now)
    return {"keys": len(keys), "resting": resting, "available": len(keys) - resting}


class _Completions:
    def __init__(self, owner: "RotatingClient") -> None:
        self._owner = owner

    def create(self, **kwargs):
        return self._owner._create(**kwargs)


class _Chat:
    def __init__(self, owner: "RotatingClient") -> None:
        self.completions = _Completions(owner)


class RotatingClient:
    def __init__(self, settings, *, timeout=None, openai_cls=None) -> None:
        self._keys = _keys_from(settings)
        self._base_url = getattr(settings, "llm_base_url", None)
        self._timeout = timeout
        # Resolved lazily so a test's patch of openai.OpenAI still applies.
        self._openai_cls = openai_cls
        self.chat = _Chat(self)

    def _create(self, **kwargs):
        if not self._keys:
            raise RuntimeError("LLM_API_KEY / LLM_API_KEYS is not configured.")
        openai_cls = self._openai_cls or openai.OpenAI
        total = len(self._keys)
        last_error: Exception | None = None
        # Each key once, plus one wait-and-retry when every key is resting.
        for _ in range(total + 1):
            key = _order(self._keys)[0]
            # Resting keys sort last, so if the first is resting, all are.
            wait = _rest_until.get(key, 0) - time.monotonic()
            if wait > 0:
                if wait > MAX_WAIT_SECONDS:
                    break
                time.sleep(wait)
            number = self._keys.index(key) + 1
            try:
                return _client_for(openai_cls, key, self._base_url, self._timeout).chat.completions.create(**kwargs)
            except openai.RateLimitError as exc:
                seconds, kind = _rest_seconds(exc)
                with _lock:
                    _rest_until[key] = time.monotonic() + seconds
                    _last_limit[key] = exc
                logger.warning("LLM key %d of %d hit its %s limit; resting %.0fs", number, total, kind, seconds)
                last_error = exc
            except (openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError) as exc:
                logger.warning("LLM key %d of %d: transient error (%s); trying next key", number, total, type(exc).__name__)
                last_error = exc
        if last_error is not None:
            raise last_error
        # Every key was already resting from earlier calls: surface the
        # provider's own rate-limit error for the soonest-free key.
        soonest = _order(self._keys)[0]
        if soonest in _last_limit:
            raise _last_limit[soonest]
        raise RuntimeError("All LLM keys are resting after rate limits; try again shortly.")


def llm_client(settings, *, timeout=None, openai_cls=None) -> RotatingClient:
    """A client that rotates across all configured keys (see module doc)."""
    return RotatingClient(settings, timeout=timeout, openai_cls=openai_cls)
