"""Offline cases for multi-key LLM rotation (app/llm.py). No network."""
import logging
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import openai

from app import llm


def rate_limit(message="Rate limit reached ... tokens per minute (TPM) ... Please try again in 7.5s.", retry_after=None):
    headers = {"retry-after": str(retry_after)} if retry_after is not None else {}
    response = httpx.Response(429, request=httpx.Request("POST", "https://example.invalid"), headers=headers)
    return openai.RateLimitError(message, response=response, body=None)


class FakeOpenAI:
    """Stands in for openai.OpenAI; `plan` maps api_key -> list of outcomes."""

    calls: list[str] = []
    plan: dict[str, list] = {}

    def __init__(self, api_key, base_url=None, max_retries=None, timeout=None):
        self.api_key = api_key
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        FakeOpenAI.calls.append(self.api_key)
        outcomes = FakeOpenAI.plan.get(self.api_key) or []
        outcome = outcomes.pop(0) if outcomes else "ok"
        if isinstance(outcome, Exception):
            raise outcome
        return f"answer from {self.api_key}"


def settings(*keys):
    return SimpleNamespace(llm_api_keys=keys, llm_api_key=keys[0] if keys else "", llm_base_url="https://example.invalid")


class RotationCases(unittest.TestCase):
    def setUp(self):
        llm._rest_until.clear()
        llm._last_limit.clear()
        llm._clients.clear()
        llm._next_index = 0
        FakeOpenAI.calls = []
        FakeOpenAI.plan = {}

    def client(self, *keys):
        return llm.llm_client(settings(*keys), openai_cls=FakeOpenAI)

    def test_round_robin_spreads_load(self):
        c = self.client("k1", "k2", "k3", "k4")
        for _ in range(8):
            c.chat.completions.create(model="m", messages=[])
        self.assertEqual(FakeOpenAI.calls, ["k1", "k2", "k3", "k4"] * 2)

    def test_rate_limited_key_fails_over_within_the_same_call(self):
        FakeOpenAI.plan = {"k1": [rate_limit(retry_after=30)]}
        c = self.client("k1", "k2")
        self.assertEqual(c.chat.completions.create(model="m", messages=[]), "answer from k2")
        self.assertEqual(FakeOpenAI.calls, ["k1", "k2"])
        # k1 now rests: the next calls avoid it instead of waiting on it.
        c.chat.completions.create(model="m", messages=[])
        c.chat.completions.create(model="m", messages=[])
        self.assertEqual(FakeOpenAI.calls[2:], ["k2", "k2"])

    def test_rest_time_from_header_and_message(self):
        self.assertEqual(llm._rest_seconds(rate_limit(retry_after=12)), (12.0, "per-minute"))
        daily = rate_limit("... tokens per day (TPD): Limit 200000 ... Please try again in 2m12.5s.")
        self.assertEqual(llm._rest_seconds(daily), (132.5, "daily"))
        self.assertEqual(llm._rest_seconds(rate_limit("no hint")), (llm.DEFAULT_MINUTE_REST, "per-minute"))

    def test_all_keys_on_daily_limit_raise_fast(self):
        daily = "tokens per day (TPD) ... Please try again in 1h5m0s."
        FakeOpenAI.plan = {"k1": [rate_limit(daily)], "k2": [rate_limit(daily)]}
        c = self.client("k1", "k2")
        start = time.monotonic()
        with self.assertRaises(openai.RateLimitError):
            c.chat.completions.create(model="m", messages=[])
        self.assertLess(time.monotonic() - start, 1.0)  # no hour-long sleep
        with self.assertRaises(openai.RateLimitError):  # both still resting
            c.chat.completions.create(model="m", messages=[])
        self.assertEqual(FakeOpenAI.calls, ["k1", "k2"])

    def test_single_key_waits_out_a_short_limit(self):
        FakeOpenAI.plan = {"only": [rate_limit(retry_after=0.2)]}
        c = self.client("only")
        self.assertEqual(c.chat.completions.create(model="m", messages=[]), "answer from only")
        self.assertEqual(FakeOpenAI.calls, ["only", "only"])

    def test_transient_error_tries_next_key(self):
        FakeOpenAI.plan = {"k1": [openai.APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))]}
        c = self.client("k1", "k2")
        self.assertEqual(c.chat.completions.create(model="m", messages=[]), "answer from k2")

    def test_other_errors_are_not_retried(self):
        FakeOpenAI.plan = {"k1": [ValueError("bad request shape")]}
        c = self.client("k1", "k2")
        with self.assertRaises(ValueError):
            c.chat.completions.create(model="m", messages=[])
        self.assertEqual(FakeOpenAI.calls, ["k1"])

    def test_falls_back_to_single_llm_api_key(self):
        c = llm.llm_client(SimpleNamespace(llm_api_key="legacy", llm_base_url=None), openai_cls=FakeOpenAI)
        c.chat.completions.create(model="m", messages=[])
        self.assertEqual(FakeOpenAI.calls, ["legacy"])

    def test_keys_never_logged(self):
        FakeOpenAI.plan = {"sk-secret-1": [rate_limit(retry_after=30)]}
        c = self.client("sk-secret-1", "sk-secret-2")
        with self.assertLogs("app.llm", level=logging.WARNING) as logs:
            c.chat.completions.create(model="m", messages=[])
        text = "\n".join(logs.output)
        self.assertIn("key 1 of 2", text)
        self.assertNotIn("sk-secret", text)

    def test_pool_status(self):
        FakeOpenAI.plan = {"k1": [rate_limit(retry_after=30)]}
        c = self.client("k1", "k2", "k3")
        c.chat.completions.create(model="m", messages=[])
        self.assertEqual(llm.pool_status(settings("k1", "k2", "k3")), {"keys": 3, "resting": 1, "available": 2})


class ConfigCases(unittest.TestCase):
    def test_llm_api_keys_parsing(self):
        from app.config import get_settings

        with patch.dict("os.environ", {"LLM_API_KEYS": " a, b ,a,,c\nd"}):
            s = get_settings()
        self.assertEqual(s.llm_api_keys, ("a", "b", "c", "d"))
        self.assertEqual(s.llm_api_key, "a")
        with patch.dict("os.environ", {"LLM_API_KEYS": "", "LLM_API_KEY": "single"}):
            self.assertEqual(get_settings().llm_api_keys, ("single",))


if __name__ == "__main__":
    unittest.main()
