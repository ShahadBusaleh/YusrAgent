"""Offline cases for Arabic outside chat; LLM responses are mocked."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.agents import growth_plan, translation
from app.api.deps import lang_from_header
from app.api.error_messages import localize_detail
from app.ui import i18n


SETTINGS = SimpleNamespace(llm_api_key="test", llm_base_url="https://example.invalid", llm_model="test")


def response(content):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=content, refusal=None), finish_reason="stop"
    )])


class LanguageHeaderCases(unittest.TestCase):
    def test_accept_language(self):
        self.assertEqual(lang_from_header("ar"), "ar")
        self.assertEqual(lang_from_header("ar-SA,en;q=0.8"), "ar")
        self.assertEqual(lang_from_header("en-US,ar;q=0.5"), "en")
        self.assertEqual(lang_from_header(None), "en")


class ErrorMessageCases(unittest.TestCase):
    def test_exact_and_pattern_messages(self):
        self.assertEqual(localize_detail("Grievance not found.", "ar"), "الشكوى غير موجودة.")
        self.assertEqual(
            localize_detail("No payroll records found for 2026-06.", "ar"),
            "لا توجد سجلات رواتب للفترة 2026-06.",
        )

    def test_passthrough(self):
        self.assertEqual(localize_detail("Grievance not found.", "en"), "Grievance not found.")
        self.assertEqual(localize_detail("Some new message", "ar"), "Some new message")
        self.assertEqual(localize_detail([{"loc": ["body"]}], "ar"), [{"loc": ["body"]}])


class LocalizeTextCases(unittest.TestCase):
    def setUp(self):
        translation._cache.clear()
        translation._recent_failures.clear()
        # Keep the persistent cache out of these tests (it would write to
        # the real agentic_hr.db); PersistentCacheCases covers it.
        self.db = {}
        for name, fake in (
            ("_stored_translation", lambda text, lang, db_path: self.db.get((lang, text))),
            ("_store_translation", lambda text, lang, out, db_path: self.db.__setitem__((lang, text), out)),
        ):
            patcher = patch.object(translation, name, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_skips_english_and_arabic_input(self):
        with patch.object(translation, "_translate") as tr:
            self.assertEqual(translation.localize_text("Hello", "en"), "Hello")
            self.assertEqual(translation.localize_text("مرحبا بك", "ar"), "مرحبا بك")
            tr.assert_not_called()

    def test_caches_success_only(self):
        with patch.object(translation, "_translate", return_value="لم أتلقَ راتبي.") as tr:
            translation.localize_text("I did not get my salary.", "ar")
            translation.localize_text("I did not get my salary.", "ar")
            self.assertEqual(tr.call_count, 1)
        with patch.object(translation, "_translate", side_effect=RuntimeError("down")) as tr:
            self.assertEqual(translation.localize_text("Other text.", "ar"), "Other text.")
            translation.localize_text("Other text.", "ar")
            # Failure is not cached as a translation, but it isn't retried
            # during the cooldown either (that hammered a rate-limited API).
            self.assertEqual(tr.call_count, 1)

    def test_failure_retried_after_cooldown(self):
        translation._recent_failures.clear()
        with patch.object(translation, "_translate", side_effect=RuntimeError("down")):
            translation.localize_text("Retry me.", "ar")
        key = ("ar", "Retry me.")
        translation._recent_failures[key] -= translation._FAILURE_COOLDOWN_SECONDS + 1
        with patch.object(translation, "_translate", return_value="أعد المحاولة.") as tr:
            self.assertEqual(translation.localize_text("Retry me.", "ar"), "أعد المحاولة.")
            self.assertEqual(tr.call_count, 1)
            self.assertNotIn(key, translation._recent_failures)

    def test_rejects_reply_instead_of_translation(self):
        reply = "عزيزي الموظف، نأسف لسماع ذلك. " * 10
        with patch.object(translation, "_translate", return_value=reply):
            self.assertEqual(translation.localize_text("I did not get my salary.", "ar"), "I did not get my salary.")

    def test_uses_document_prompt(self):
        with patch.object(translation, "_translate", return_value="هل يمكنك الموافقة؟") as tr:
            translation.localize_text("Can you approve?", "ar")
            self.assertIn("Never answer", tr.call_args.args[1])

    def test_arabic_to_english(self):
        with patch.object(translation, "_translate", return_value="I did not get my salary.") as tr:
            out = translation.localize_text("لم أستلم راتبي.", "en")
            self.assertEqual(out, "I did not get my salary.")
            self.assertIn("from Arabic into English", tr.call_args.args[1])
        with patch.object(translation, "_translate") as tr:
            self.assertEqual(translation.localize_text("Already English.", "en"), "Already English.")
            tr.assert_not_called()

    def test_survives_memory_cache_loss(self):
        with patch.object(translation, "_translate", return_value="لم أتلقَ راتبي.") as tr:
            translation.localize_text("I did not get my salary.", "ar", "request.db")
            translation._cache.clear()  # simulate an API restart
            self.assertEqual(translation.localize_text("I did not get my salary.", "ar", "request.db"), "لم أتلقَ راتبي.")
            self.assertEqual(tr.call_count, 1)

    def test_localize_many_keeps_order(self):
        fake = {"One.": "واحد.", "Two.": "اثنان."}
        with patch.object(translation, "_translate", side_effect=lambda text, prompt: fake[text]):
            self.assertEqual(translation.localize_many(["One.", None, "Two."], "ar"), ["واحد.", "", "اثنان."])

    def test_no_db_path_never_opens_a_database(self):
        with patch.object(translation, "_translate", return_value="مرحباً."),                 patch("sqlite3.connect") as connect:
            translation.localize_text("Hello there.", "ar")
            connect.assert_not_called()

    def test_prefetch_can_be_disabled(self):
        with patch.dict("os.environ", {"YUSOR_PREFETCH_TRANSLATIONS": "0"}),                 patch.object(translation.threading, "Thread") as thread:
            translation.prefetch_translations(["Some text."])
            thread.assert_not_called()


class PrefetchCases(unittest.TestCase):
    def test_background_job_writes_to_callers_db(self):
        """The thread writes to the DB it was given, never to settings'
        DB: re-reading settings once leaked a test's row into the real
        agentic_hr.db."""
        import os
        import sqlite3
        import tempfile
        import threading
        from app.db.translation_cache import get_cached_translation

        path = os.path.join(tempfile.mkdtemp(), "prefetch.db")
        sqlite3.connect(path).close()
        translation._cache.clear()
        done = threading.Event()
        real_localize = translation.localize_text

        def localize_then_signal(*args):
            try:
                return real_localize(*args)
            finally:
                done.set()

        with patch.dict("os.environ", {"YUSOR_PREFETCH_TRANSLATIONS": "1"}),                 patch.object(translation, "_translate", return_value="خطة صالحة"),                 patch.object(translation, "localize_text", side_effect=localize_then_signal):
            translation.prefetch_translations(["A valid plan."], path)
            self.assertTrue(done.wait(10))
        conn = sqlite3.connect(path)
        self.assertEqual(get_cached_translation(conn, "A valid plan.", "ar"), "خطة صالحة")


class PersistentCacheCases(unittest.TestCase):
    def test_round_trip(self):
        import sqlite3
        from app.db.translation_cache import get_cached_translation, save_translation

        conn = sqlite3.connect(":memory:")
        self.assertIsNone(get_cached_translation(conn, "Hello.", "ar"))
        save_translation(conn, "Hello.", "ar", "مرحباً.")
        self.assertEqual(get_cached_translation(conn, "Hello.", "ar"), "مرحباً.")
        self.assertIsNone(get_cached_translation(conn, "Hello.", "en"))

    def test_read_never_creates_table(self):
        import sqlite3
        from app.db.translation_cache import database_path, get_cached_translation

        conn = sqlite3.connect(":memory:")
        self.assertIsNone(get_cached_translation(conn, "Hello.", "ar"))
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        self.assertEqual(tables, [])
        self.assertIsNone(database_path(conn))
        self.assertIsNone(database_path(None))


class GrowthPlanLanguageCases(unittest.TestCase):
    def generate(self, **kwargs):
        client = Mock()
        client.chat.completions.create.return_value = response(json.dumps({
            "status": "ok", "strengths": "خبرة في Excel", "gaps": "تحليل البيانات", "next_steps": "دورة SQL",
        }))
        with patch.object(growth_plan, "get_settings", return_value=SETTINGS), \
                patch.object(growth_plan, "OpenAI", return_value=client):
            plan = growth_plan.generate_growth_plan(
                {"job_title": "HR Assistant"}, {"skill_name": "HR Data Analyst"},
                "HR assistant, two years of Excel reporting.", **kwargs,
            )
        system = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        return plan, system

    def test_arabic_plan(self):
        plan, system = self.generate(lang="ar")
        self.assertIn("## ما تملكه بالفعل", plan)
        self.assertIn("## الخطوات التالية", plan)
        self.assertIn("Modern Standard Arabic", system)

    def test_english_default_unchanged(self):
        plan, system = self.generate()
        self.assertIn("## What you already bring", plan)
        self.assertNotIn("Modern Standard Arabic", system)


class UiStringCases(unittest.TestCase):
    def test_every_key_has_both_languages(self):
        en, ar = i18n.STRINGS["en"], i18n.STRINGS["ar"]
        self.assertEqual(set(en), set(ar))

    def test_localized_summaries(self):
        from app.ui import views

        with patch.object(i18n, "get_lang", return_value="ar"):
            self.assertEqual(
                views._localized_summary("bank_update", {"new_iban": "SA0380000000608010167519"}),
                "تغيير الآيبان البنكي إلى •••• 7519",
            )
            self.assertEqual(
                views._localized_summary("personal_info_update", {"field_name": "mobile", "new_value": "0551234567"}),
                "تحديث رقم الجوال إلى 0551234567",
            )
            self.assertEqual(views._localized_summary("something_new", {}), "")


if __name__ == "__main__":
    unittest.main()
