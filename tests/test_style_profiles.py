import base64
import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from core.auth_manager import AuthManager
from core.context_assembler import ContextAssembler
from core.novel_manager import NovelManager, NovelMeta
from core.style_profiles import (
    ResolvedStyle,
    StyleAnchor,
    StyleExtractionCancelled,
    StyleExtractionService,
    StyleProfile,
    StyleProfileRepository,
    StyleSourceDocument,
    calculate_style_metrics,
    render_style_prompt,
    split_style_text,
)


class _FakeCompletions:
    def __init__(self):
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        prompt = kwargs["messages"][0]["content"]
        first_person = "我沿着" in prompt or '"narrative_person": "第一人称"' in prompt
        payload = {
            "narrative_person": "第一人称" if first_person else "第三人称",
            "viewpoint_distance": "贴近人物感受" if first_person else "远距离全知",
            "sentence_rhythm": "短句急促" if first_person else "长句舒缓",
            "dialogue_habits": "对白简短" if first_person else "对白较少",
            "diction": "口语克制" if first_person else "典雅书面",
            "description_balance": "行动为主",
            "imagery": "冷色意象",
            "emotion_expression": "通过动作间接表达",
            "transitions": "动作转场",
            "endings": "短句收束",
            "stable_rules": [f"规则 {index}" for index in range(1, 9)],
            "scene_facets": {
                "general": ["叙述保持克制"],
                "dialogue": ["对白后接动作"],
                "action": ["动作使用短句"],
                "psychology": ["不直说情绪"],
                "environment": ["环境只取关键细节"],
            },
            "avoid_rules": ["避免总结情绪", "避免模板化抒情", "避免同义反复"],
        }
        message = SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_FakeCompletions())


class _RetryCompletions(_FakeCompletions):
    def __init__(self):
        super().__init__()
        self.failures_left = 2

    def create(self, **kwargs):
        if self.failures_left:
            self.failures_left -= 1
            self.calls += 1
            raise RuntimeError("temporary model failure")
        return super().create(**kwargs)


class _EmptyManager:
    def __getattr__(self, _name):
        raise RuntimeError("not available")


class StyleProfileTests(unittest.TestCase):
    def test_chunking_covers_full_text_without_truncation(self):
        source = "".join(f"第{i}章\n" + ("风吹过长街。" * 430) + "\n\n" for i in range(14))
        chunks = split_style_text(source)
        self.assertEqual("".join(chunks), source)
        self.assertGreater(len(chunks), 5)
        self.assertTrue(all(1 <= len(chunk) <= 6000 for chunk in chunks))

    def test_local_metrics_detect_chinese_person_and_dialogue(self):
        metrics = calculate_style_metrics("我看见他。\n\n“我们走。”她说。")
        self.assertIn("lexical_markers_per_1000", metrics)
        self.assertIn("lexical_categories_per_1000", metrics)
        self.assertIn("punctuation_per_1000", metrics)
        self.assertIn("sentence_length_median", metrics)
        self.assertIn("short_sentence_ratio", metrics)
        self.assertIn("dialogue_paragraph_ratio", metrics)
        self.assertGreaterEqual(metrics["first_person_hits"], 2)
        self.assertGreaterEqual(metrics["third_person_hits"], 2)
        self.assertGreater(metrics["dialogue_ratio"], 0)

    def test_encrypted_repository_roundtrip_copy_and_reference_delete(self):
        with tempfile.TemporaryDirectory() as root:
            full_key = AuthManager._derive_full_key("style-test", b"0" * 16)
            key = base64.urlsafe_b64encode(full_key[32:])
            manager = NovelManager(root, crypto=AuthManager, enc_key=key)
            manager.create_book("甲书")
            profile = StyleProfile(name="冷峻短句", stable_rules=["使用短句"])
            repository = StyleProfileRepository(manager)
            repository.save(profile)
            actual_path = os.path.join(root, "style_profiles.json.enc")
            self.assertTrue(os.path.exists(actual_path))
            with open(actual_path, "rb") as encrypted_file:
                self.assertNotIn("冷峻短句", encrypted_file.read().decode("latin1"))
            loaded = repository.get(profile.profile_id)
            self.assertEqual(loaded.name, "冷峻短句")
            copied = repository.duplicate(profile.profile_id)
            self.assertNotEqual(copied.profile_id, profile.profile_id)
            manager.save_meta("甲书", style_profile_id=profile.profile_id, style_strength="strict")
            affected = repository.delete(profile.profile_id)
            self.assertEqual(affected, ["甲书"])
            self.assertEqual(manager.load_meta("甲书").style_profile_id, "")

    def test_old_meta_defaults_are_compatible(self):
        meta = NovelMeta(**{"title": "旧书"})
        self.assertEqual(meta.style_profile_id, "")
        self.assertEqual(meta.style_strength, "standard")

    def test_strengths_select_three_five_and_six_diverse_examples(self):
        texts = [
            "形式样本：雨夜里只剩檐角滴水。",
            "形式样本：铁门骤然合拢，脚步逼近。",
            "形式样本：她没有回答，只把茶盏推远。",
            "形式样本：山脊被晨雾切成深浅两层。",
            "形式样本：旧信压在抽屉底部，墨迹已经褪色。",
            "形式样本：他冲过回廊，侧身避开迎面的刀。",
            "形式样本：灯芯爆响一下，房间重归寂静。",
            "形式样本：门外无人，雪地却多了一行脚印。",
            "形式样本：潮声隔着窗纸，一阵近，一阵远。",
            "形式样本：杯沿留着一道浅淡的口红印。",
            "形式样本：他把钥匙放下，金属声很轻。",
        ]
        profile = StyleProfile(
            name="样本文风",
            stable_rules=["使用动作承载情绪"],
            avoid_rules=["避免总结"],
            anchors=[StyleAnchor(facet="general", text=text) for text in texts],
        )
        for strength, count in (("reference", 3), ("standard", 5), ("strict", 6)):
            prompt = render_style_prompt(ResolvedStyle(profile, strength))
            self.assertEqual(prompt.count("形式样本"), count)
            self.assertIn("以本文风档案为准", prompt)
            self.assertLess(prompt.index("核心模仿例文"), prompt.index("稳定写法"))
            self.assertIn("共同构成文风的首要依据", prompt)

    def test_fifty_thousand_characters_are_all_analyzed(self):
        source = "\n\n".join(f"我沿着第{index}条长街往前走，风把灯影压低。" * 12 for index in range(250))
        fake = _FakeClient()
        service = StyleExtractionService(fake)
        profiles = service.extract_documents(
            [StyleSourceDocument("五万字样本.txt", source)], "fake-model", base_name="全文文风"
        )
        self.assertEqual(len(profiles), 1)
        profile = profiles[0]
        self.assertEqual(profile.sample_chars, len(source))
        self.assertEqual(profile.chunk_count, len(split_style_text(source)))
        self.assertEqual(fake.chat.completions.calls, profile.chunk_count + 1)
        self.assertTrue(12 <= len(profile.anchors) <= 20)
        self.assertIn("ending", {item.facet for item in profile.anchors})

    def test_strict_runtime_prefers_scene_facets_and_keeps_ending(self):
        profile = StyleProfile(
            name="场景文风",
            anchors=[
                StyleAnchor(facet="action", text="动作片段甲：刀锋贴着石墙掠过。"),
                StyleAnchor(facet="action", text="动作片段乙：他伏身冲进狭窄门洞。"),
                StyleAnchor(facet="dialogue", text="对白片段：她问完便不再开口。"),
                StyleAnchor(facet="psychology", text="心理片段：迟疑像细刺停在心口。"),
                StyleAnchor(facet="environment", text="环境片段：长街的雾慢慢漫过台阶。"),
                StyleAnchor(facet="general", text="通用片段：钟声落下，众人继续赶路。"),
                StyleAnchor(facet="ending", text="章末片段：门后传来第二个人的呼吸。"),
                StyleAnchor(facet="general", text="补充片段：纸页翻到一半忽然停住。"),
                StyleAnchor(facet="dialogue", text="对白片段乙：他说到这里，忽然看向窗外。"),
                StyleAnchor(facet="psychology", text="心理片段乙：那个念头沉下去，又浮上来。"),
                StyleAnchor(facet="environment", text="环境片段乙：潮气沿着砖缝缓缓上升。"),
            ],
        )
        prompt = render_style_prompt(
            ResolvedStyle(profile, "strict"), task_context="战斗追逐，并在章末留下悬念"
        )
        self.assertEqual(prompt.count("片段"), 6)
        self.assertIn("例文1（action）", prompt)
        self.assertIn("（ending）", prompt)

    def test_mixed_documents_split_into_multiple_profiles(self):
        first = ("我沿着长街往前走。" * 700) + "\n\n" + ("“走。”我说。" * 300)
        third = ("群山在雾中沉默，他缓慢地越过河谷。" * 700)
        profiles = StyleExtractionService(_FakeClient()).extract_documents(
            [StyleSourceDocument("第一.txt", first), StyleSourceDocument("第二.txt", third)],
            "fake-model",
            base_name="混合样本",
            source_kind="folder",
        )
        self.assertEqual(len(profiles), 2)
        self.assertNotEqual(profiles[0].name, profiles[1].name)

    def test_chunk_failure_retries_twice(self):
        client = _FakeClient()
        client.chat.completions = _RetryCompletions()
        profile = StyleExtractionService(client).extract_documents(
            [StyleSourceDocument("retry.txt", "我沿着长街走。" * 200)], "fake-model"
        )[0]
        self.assertEqual(profile.chunk_count, 1)
        self.assertEqual(client.chat.completions.calls, 4)

    def test_cancelled_run_keeps_checkpoint_and_resumes(self):
        source = "\n\n".join(f"我沿着第{index}条长街走。" * 40 for index in range(90))
        chunks = split_style_text(source)
        self.assertGreater(len(chunks), 2)
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(root)
            repository = StyleProfileRepository(manager)
            first_client = _FakeClient()
            service = StyleExtractionService(first_client, repository)
            with self.assertRaises(StyleExtractionCancelled):
                service.extract_documents(
                    [StyleSourceDocument("resume.txt", source)],
                    "fake-model",
                    run_id="resume-run",
                    cancelled=lambda: first_client.chat.completions.calls >= 1,
                )
            checkpoint = repository.load_checkpoint("resume-run:0")
            self.assertEqual(len(checkpoint["analyses"]), 1)

            resumed_client = _FakeClient()
            profiles = StyleExtractionService(resumed_client, repository).extract_documents(
                [StyleSourceDocument("resume.txt", source)],
                "fake-model",
                run_id="resume-run",
            )
            self.assertEqual(len(profiles), 1)
            self.assertEqual(resumed_client.chat.completions.calls, len(chunks))
            self.assertEqual(repository.load_checkpoint("resume-run:0"), {})

    def test_continuation_keeps_tail_8000_and_reports_omission(self):
        source = "开" + ("甲" * 11998) + "终"
        report = ContextAssembler(_EmptyManager()).assemble_continuation(
            "书", 2, "续章", source, "", ""
        )
        original = next(section for section in report.sections if section.title == "原文内容")
        self.assertEqual(original.original_chars, len(source))
        self.assertEqual(len(original.content), 8000)
        marker, kept_tail = original.content.split("\n\n", 1)
        self.assertIn(str(len(source) - 8000), marker)
        self.assertEqual(original.omitted_chars, len(source) - len(kept_tail))
        self.assertTrue(original.content.endswith("终"))
        self.assertNotIn("开", original.content)


    def test_same_style_documents_merge_whole_corpus_metrics(self):
        sample = "我沿着长街往前走，却没有回头。\n\n“走吧。”我说。" * 220
        profiles = StyleExtractionService(_FakeClient()).extract_documents(
            [
                StyleSourceDocument("上卷.txt", sample),
                StyleSourceDocument("下卷.txt", sample),
            ],
            "fake-model",
            base_name="合并文风",
            source_kind="folder",
        )
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].metrics["total_chars"], len(sample) * 2)

if __name__ == "__main__":
    unittest.main()
