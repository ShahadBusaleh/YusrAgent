"""Offline cases for Arabic chat answers: fixed Arabic templates, direct
Arabic Consultant answers, and the HR glossary. No LLM calls."""
import unittest
from unittest.mock import patch

from app.agents import arabic_text, consultant_agent, orchestrator, translation
from app.agents.manager_agent import ManagerAgent, _fail
from app.security.governance import validate_output

USER = {"user_id": "U2", "employee_id": "EMP-0002", "username": "EMP-0002", "role": "employee"}

SOURCES = [{
    "id": "LAW_ART_109",
    "text": "Article: 109 Title: Annual leave. The worker is entitled to annual leave of 21 days, "
            "increased to 30 days after five years of service.",
}]


def manager_run(hr_result=None, consultant_result=None):
    return ManagerAgent().run({
        "query": "how many leave days do I have",
        "user": USER,
        "security": {"risk": "LOW"},
        "intent": "HR",
        "hr_result": hr_result or {"facts": {}, "sources": []},
        "consultant_result": consultant_result or {},
    })


class TemplateCases(unittest.TestCase):
    def test_leave_balance_rendered_without_llm(self):
        result = manager_run({
            "facts": {
                "leave_balance": {"annual_remaining": 17, "sick_remaining": 30, "emergency_remaining": 5},
                "leave_requests": [{}, {}],
            },
            "sources": ["leave_balances:EMP-0002"],
        })
        self.assertEqual(result["decision"], "PASS")
        with patch.object(orchestrator, "translate_to_arabic") as tr:
            text = orchestrator._render_arabic(result["response_blocks"])
        tr.assert_not_called()
        self.assertIn("السنوية 17", text)
        self.assertIn("المرضية 30", text)
        self.assertIn("الطارئة 5", text)
        self.assertIn(": 2.", text)

    def test_payroll_and_attendance_templates(self):
        self.assertEqual(
            arabic_text.payroll_ar({"pay_period": "2026-08", "gross_pay_sar": 12000, "net_pay_sar": 10920.5}),
            "الراتب (2026-08): إجمالي الراتب 12000 ريال، صافي الراتب 10920.5 ريال.",
        )
        self.assertIn("نسبة الحضور: 95%", arabic_text.attendance_ar({"attendance_rate_pct": 95}))

    def test_free_text_notice_is_translated_alone(self):
        result = manager_run({
            "facts": {
                "leave_balance": {"annual_remaining": 17},
                "payroll_notice": "No payroll record exists for this month yet.",
            },
            "sources": ["leave_balances:EMP-0002"],
        })
        with patch.object(orchestrator, "translate_to_arabic", return_value="ترجمة") as tr:
            text = orchestrator._render_arabic(result["response_blocks"])
        # The block has one free-text piece, so that paragraph is translated
        # once; the English still carries the exact numbers into it.
        tr.assert_called_once()
        self.assertEqual(text, "ترجمة")

    def test_failure_messages_have_arabic(self):
        result = _fail(["Requested 10 days exceeds the remaining balance of 4 days."])
        ar_text = result["response_blocks"][0][0][1]
        self.assertIn("(10)", ar_text)
        self.assertIn("(4)", ar_text)
        self.assertTrue(_fail(["Prompt injection detected."])["response_blocks"][0][0][1])

    def test_submission_notes(self):
        self.assertIn("P-1", arabic_text.submission_note_ar("submitted", "P-1", "Sara"))
        self.assertIn("Sara", arabic_text.submission_note_ar("submitted", "P-1", "Sara"))
        self.assertIn("تلقائياً", arabic_text.submission_note_ar("auto_approved", "P-2"))

    def test_english_response_unchanged(self):
        result = manager_run({
            "facts": {"leave_balance": {"annual_remaining": 17}},
            "sources": ["leave_balances:EMP-0002"],
        })
        self.assertEqual(result["response"], "Remaining leave balance: annual 17 days.")


class ArabicPolicyAnswerCases(unittest.TestCase):
    def test_grounded_arabic_answer_passes(self):
        answer = "يستحق العامل إجازة سنوية مدتها 21 يوماً وفق المادة 109 [Source: LAW_ART_109]."
        self.assertTrue(validate_output(answer, SOURCES))

    def test_arabic_answer_needs_citation(self):
        self.assertFalse(validate_output("يستحق العامل إجازة سنوية مدتها 21 يوماً.", SOURCES))

    def test_arabic_answer_with_invented_number_fails(self):
        answer = "يستحق العامل إجازة سنوية مدتها 25 يوماً [Source: LAW_ART_109]."
        self.assertFalse(validate_output(answer, SOURCES))

    def test_arabic_indic_digits_are_checked(self):
        answer = "يستحق العامل إجازة سنوية مدتها ٢١ يوماً [Source: LAW_ART_109]."
        self.assertTrue(validate_output(answer, SOURCES))

    def test_arabic_consultant_answer_used_directly(self):
        answer = "يستحق العامل إجازة سنوية مدتها 21 يوماً [Source: LAW_ART_109]."
        result = manager_run(consultant_result={
            "recommendation": answer, "language": "ar", "sources": SOURCES, "success": True,
        })
        self.assertEqual(result["decision"], "PASS")
        with patch.object(orchestrator, "translate_to_arabic") as tr:
            text = orchestrator._render_arabic(result["response_blocks"])
        tr.assert_not_called()
        self.assertNotIn("[Source", text)
        self.assertIn("21", text)

    def test_ungrounded_arabic_falls_back_to_english(self):
        chunks = [{"id": s["id"], "text": s["text"]} for s in SOURCES]
        outputs = {
            "ar": "مدتها 25 يوماً [Source: LAW_ART_109].",
            "en": "Annual leave is 21 days [Source: LAW_ART_109].",
        }
        with patch.object(
            consultant_agent, "_generate_consultant_recommendation",
            side_effect=lambda query, chunks, is_grievance, lang="en": outputs[lang],
        ):
            text, language = consultant_agent._generate_for_reader("q", chunks, False, "ar")
        self.assertEqual((text, language), (outputs["en"], "en"))


class GlossaryCases(unittest.TestCase):
    def test_glossary_in_translation_prompts(self):
        for lang in ("ar", "en"):
            self.assertIn("مكافأة نهاية الخدمة", translation._DOCUMENT_PROMPTS[lang])

    def test_glossary_in_chat_translation(self):
        with patch.object(translation, "_translate", return_value="x") as tr:
            translation.translate_to_arabic("Annual leave is 21 days.")
            translation.translate_to_english("كم رصيد إجازتي؟")
        for call in tr.call_args_list:
            self.assertIn("إجازة سنوية", call.args[1])
        # Into English, the glossary maps Arabic terms to English ones; a
        # "use these Arabic terms" wording left Arabic in the English query.
        self.assertIn("إجازة سنوية -> annual leave", tr.call_args_list[1].args[1])
        self.assertIn("no Arabic", tr.call_args_list[1].args[1])


class KeywordSearchCases(unittest.TestCase):
    """BM25 half of hybrid retrieval (local files only, no Qdrant)."""

    def test_basic_annual_leave_rule_found(self):
        from app.rag.keyword import keyword_search
        ids = [c["id"] for c in keyword_search(
            "What is the duration of annual leave according to the Saudi Labor Law?", top_k=5
        )]
        # At least 21 days — dense search alone ranked it below the cut;
        # LAW038 (30 days after five years) comes from the dense side.
        self.assertIn("LAW037", ids)

    def test_chunk_shape_matches_retriever(self):
        from app.rag.keyword import keyword_search
        chunk = keyword_search("end of service award", top_k=1)[0]
        self.assertEqual(
            set(chunk),
            {"id", "source_ids", "source_table", "source_name", "filename", "text", "score"},
        )

    def test_wps_rules_merged_by_category(self):
        from app.rag.ingest import load_chunks
        from app.security.governance import verify_citations
        wage_data = next(c for c in load_chunks() if c["id"] == "WPS021-WPS030")
        self.assertEqual(wage_data["source_ids"], [f"WPS0{n}" for n in range(21, 31)])
        self.assertIn("Housing allowance", wage_data["text"])
        # An answer may cite a rule inside the section or the section itself.
        self.assertTrue(verify_citations("[Source: WPS027] [Source: WPS021-WPS030]", [wage_data]))
        self.assertFalse(verify_citations("[Source: WPS034]", [wage_data]))

    def test_stop_words_only_query_returns_nothing(self):
        from app.rag.keyword import keyword_search
        self.assertEqual(keyword_search("what is the"), [])


class CitedAnswerTranslationCases(unittest.TestCase):
    def test_citation_tags_removed_before_translation(self):
        with patch.object(translation, "_translate", return_value="x") as tr:
            translation.translate_to_arabic("Annual leave is 21 days [Source: LAW037].")
        self.assertNotIn("Source", tr.call_args.args[0])

    def test_list_markers_and_number_words_accepted(self):
        sources = [{"id": "LAW038", "text": "Annual leave rises to 30 days after five consecutive years."}]
        answer = "1. تزيد الإجازة إلى 30 يوماً بعد 5 سنوات [Source: LAW038]\n2. شرط الخدمة المتصلة."
        self.assertTrue(validate_output(answer, sources))
        self.assertFalse(validate_output("تزيد الإجازة إلى 45 يوماً [Source: LAW038]", sources))


class CitationStripCases(unittest.TestCase):
    def test_empty_citation_label_line_dropped(self):
        text = "- **المادة**: 109.\n   - **الاستشهاد**: [Source: LAW037]\nمدة الإجازة 21 يوماً [Source: LAW037]."
        self.assertEqual(
            arabic_text.strip_citation_tags(text), "- **المادة**: 109.\nمدة الإجازة 21 يوماً."
        )

    def test_label_with_content_kept(self):
        self.assertEqual(
            arabic_text.strip_citation_tags("- **المادة**: 109 [Source: LAW037]"), "- **المادة**: 109"
        )
