import json
import unittest
from types import SimpleNamespace

from core.style_evaluation import evaluate_style_text
from core.style_profiles import StyleProfile, calculate_style_metrics
from utils.supervision import audit_chapter


class _AuditCompletions:
    def create(self, **_kwargs):
        payload = {
            "status": "passed",
            "outline_items": [],
            "hard_constraint_issues": [],
            "continuity_issues": [],
            "style_issues": [],
            "repair_instruction": "",
        }
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
        )])


class _AuditClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_AuditCompletions())


class StyleEvaluationTests(unittest.TestCase):
    def test_matching_text_scores_high_and_has_no_priorities(self):
        text = ("他把灯放低。雨落在檐下。\n\n“走。”她说。\n\n" * 100).strip()
        profile = StyleProfile(name="冷峻短句", metrics=calculate_style_metrics(text))
        report = evaluate_style_text(profile, text)
        self.assertGreaterEqual(report.style_match_score, 99)
        self.assertEqual(report.anti_ai_score, 100)
        self.assertEqual(report.status, "excellent")
        self.assertFalse(report.priorities)

    def test_drift_report_names_actionable_dimensions(self):
        reference = ("他停下。雨很密。\n\n“别走。”她说。\n\n" * 80).strip()
        candidate = ("然而在这个时候，远方漫长而复杂的城市轮廓逐渐显现出来，所有人都不得不重新思考此前发生的一切。" * 80)
        report = evaluate_style_text(
            StyleProfile(name="短句对白", metrics=calculate_style_metrics(reference)),
            candidate,
        )
        self.assertLess(report.style_match_score, 90)
        self.assertTrue(report.priorities)
        self.assertTrue(any(item.label in {"平均句长", "短句比例", "对白字符比例"} for item in report.metric_drifts[:8]))
        self.assertIn("优先调整", report.render_text())

    def test_ai_tic_penalty_uses_repetition_and_length_normalization(self):
        filler = "他沿着长街往前走。" * 600
        isolated = filler + "这不是结束，而是开始。"
        repeated = filler + ("这不是结束，而是开始。" * 8) + ("他终于明白这意味着什么。" * 8)
        metrics = calculate_style_metrics(filler)
        isolated_report = evaluate_style_text(metrics, isolated)
        repeated_report = evaluate_style_text(metrics, repeated)
        self.assertEqual(isolated_report.anti_ai_score, 100)
        self.assertLess(repeated_report.anti_ai_score, isolated_report.anti_ai_score)
        self.assertGreater(repeated_report.ai_tic_density_per_10000, 0)

    def test_supervision_report_persists_local_style_evaluation(self):
        reference = "他停下。雨落下来。\n\n“走。”她说。" * 60
        result = audit_chapter(
            _AuditClient(),
            chapter_content=reference,
            chapter_title="第一章",
            chapter_outline="雨夜离开",
            requirements="",
            continuity_context="",
            target_words=0,
            model="test",
            style_profile_metrics=calculate_style_metrics(reference),
            style_profile_name="测试文风",
        )
        evaluation = result.to_dict()["local_style_evaluation"]
        self.assertEqual(evaluation["profile_name"], "测试文风")
        self.assertGreaterEqual(evaluation["style_match_score"], 99)


if __name__ == "__main__":
    unittest.main()
