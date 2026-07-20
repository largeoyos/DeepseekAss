import json
import unittest
from types import SimpleNamespace

from core.style_profiles import ResolvedStyle, StyleProfile, calculate_style_metrics
from core.style_rerank import build_content_lock, select_best_style_candidate


class _JudgeCompletions:
    def create(self, **_kwargs):
        payload = {
            "candidates": [
                {
                    "candidate_id": "A",
                    "style_score": 45,
                    "content_score": 35,
                    "naturalness_score": 60,
                    "content_lock_violations": ["改变指定结尾"],
                    "notes": ["解释性旁白偏多"],
                },
                {
                    "candidate_id": "B",
                    "style_score": 92,
                    "content_score": 96,
                    "naturalness_score": 90,
                    "content_lock_violations": [],
                    "notes": ["内容和文风均稳定"],
                },
            ],
            "winner_id": "B",
        }
        message = SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _JudgeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_JudgeCompletions())


class StyleRerankTests(unittest.TestCase):
    def test_content_lock_preserves_order_constraints_facts_and_ending(self):
        manifest = build_content_lock(
            chapter_title="雨夜",
            outline="林舟先进入仓库；随后发现账本；结尾停在门外第二声脚步。",
            requirements="必须保持林舟不知道真凶；不得新增死亡角色。",
            continuity_context="【当前事实】账本已经被水浸湿\n人物关系：林舟仍不信任周岚",
        )
        self.assertEqual(manifest.ordered_events[0], "林舟先进入仓库")
        self.assertTrue(any("不得新增" in item for item in manifest.explicit_constraints))
        self.assertTrue(any("账本已经" in item for item in manifest.continuity_facts))
        self.assertTrue(any("第二声脚步" in item for item in manifest.ending_constraints))

    def test_rerank_prioritizes_content_lock_style_and_naturalness(self):
        reference = "他把灯放低。雨声压在檐下。\n\n“走。”她说。" * 80
        profile = StyleProfile(name="短句", metrics=calculate_style_metrics(reference))
        manifest = build_content_lock(
            chapter_title="雨夜", outline="进入仓库；找到湿账本；门外响起脚步",
            requirements="不得揭露真凶", continuity_context="账本已经被水浸湿",
        )
        candidate_a = "然而，命运的齿轮在此刻缓缓转动。" * 120
        candidate_b = "他把灯放低。雨落在门外。\n\n“账本湿了。”她说。" * 80
        result = select_best_style_candidate(
            _JudgeClient(),
            candidates=[candidate_a, candidate_b],
            resolved_style=ResolvedStyle(profile, "strict"),
            content_lock=manifest,
            model="test",
            task_context="在雨夜进入仓库",
        )
        self.assertEqual(result.index, 1)
        self.assertEqual(result.content, candidate_b)
        self.assertTrue(result.judge_used)
        self.assertGreater(result.scores[1].total_score, result.scores[0].total_score)


if __name__ == "__main__":
    unittest.main()
