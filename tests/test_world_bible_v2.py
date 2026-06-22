import json
import os
import tempfile
import unittest
from pathlib import Path

from core.novel_manager import NovelManager
from core.world_bible import (
    CharacterEntry,
    LocationEntry,
    PlotThread,
    Relationship,
    WorldBible,
    WorldRule,
    _flat_view_dict,
    _split_chapter_for_world_extraction,
    apply_manual_overrides,
    audit_world_bible_consistency,
    confirm_duplicate_candidate,
    dict_to_world_bible,
    format_relevant_world_bible_for_prompt,
    merge_extracted_world_bible_data,
    record_manual_view_changes,
    undo_entity_merge,
    world_bible_to_dict,
)


class WorldBibleV2Tests(unittest.TestCase):
    def test_legacy_migration_adds_ids_rules_and_snapshot_mirror(self):
        legacy = {
            "characters": [{"name": "林青", "traits": "剑客", "relationships": []}],
            "locations": [{"name": "北城", "description": "雪城"}],
            "rules": ["夜晚不能使用法术"],
            "chapter_world_entries": {"ch0001_v001": {"chapter": 1, "version": 1, "data": {}}},
        }
        bible = dict_to_world_bible(legacy)
        data = world_bible_to_dict(bible)
        self.assertEqual(data["schema_version"], 2)
        self.assertTrue(bible.characters[0].id.startswith("char_"))
        self.assertTrue(bible.locations[0].id.startswith("loc_"))
        self.assertEqual(bible.world_rules[0].knowledge_type, "constraint")
        self.assertEqual(bible.chapter_snapshots, bible.chapter_world_entries)
        self.assertEqual(dict_to_world_bible(data).characters[0].id, bible.characters[0].id)

    def test_manual_override_survives_aggregate_rebuild(self):
        extracted = {
            "characters": [{"name": "阿离", "traits": "谨慎", "status": "alive", "current_goal": "进城"}],
            "rules": ["城门日落关闭"],
        }
        original = merge_extracted_world_bible_data(
            WorldBible(), extracted, chapter_num=1, chapter_version=1,
            store_chapter_entry=True, run_dedup=False,
        )
        before = _flat_view_dict(original)
        original.characters[0].current_goal = "留在城外"
        original.characters[0].locked = True
        record_manual_view_changes(original, before)

        rebuilt = merge_extracted_world_bible_data(
            WorldBible(
                chapter_snapshots=dict(original.chapter_snapshots),
                chapter_world_entries=dict(original.chapter_world_entries),
            ),
            extracted, chapter_num=1, chapter_version=1,
            run_dedup=False,
        )
        rebuilt.manual_overrides = list(original.manual_overrides)
        apply_manual_overrides(rebuilt)
        self.assertEqual(rebuilt.characters[0].current_goal, "留在城外")
        self.assertTrue(rebuilt.characters[0].locked)

    def test_fact_evolution_supports_revive_and_thread_reopen(self):
        bible = WorldBible()
        bible = merge_extracted_world_bible_data(
            bible,
            {
                "characters": [{"name": "顾川", "status": "dead", "current_location": "河底"}],
                "plot_threads": [{"name": "失踪案", "status": "resolved", "description": "已经结案"}],
            },
            chapter_num=2, run_dedup=False,
        )
        bible = merge_extracted_world_bible_data(
            bible,
            {
                "characters": [{"name": "顾川", "status": "alive", "current_location": "医馆"}],
                "plot_threads": [{"name": "失踪案", "status": "active", "description": "发现新证据"}],
            },
            chapter_num=5, run_dedup=False,
        )
        self.assertEqual(bible.characters[0].status, "alive")
        self.assertEqual(bible.characters[0].current_location, "医馆")
        self.assertEqual(bible.active_plot_threads[0].status, "active")
        status_facts = [item for item in bible.facts if item.predicate == "status"]
        self.assertGreaterEqual(len(status_facts), 4)
        self.assertTrue(any(item.supersedes for item in status_facts))

    def test_hybrid_retrieval_expands_relationships_and_respects_budget(self):
        alice = CharacterEntry(id="char_alice", name="爱丽丝", importance="major", traits="调查员", current_location="钟楼")
        bob = CharacterEntry(id="char_bob", name="鲍勃", traits="守钟人")
        alice.relationships.append(Relationship(target="鲍勃", target_id="char_bob", type="ally"))
        bible = WorldBible(
            characters=[alice, bob],
            locations=[LocationEntry(id="loc_clock", name="钟楼", description="旧城区最高建筑")],
            active_plot_threads=[PlotThread(id="thread_clock", name="钟楼谜案", status="active", involved_characters=["爱丽丝"], description="钟声异常")],
            world_rules=[WorldRule(id="rule_time", name="午夜规则", content="午夜钟响后所有门会锁死", locked=True, priority=100)],
        )
        text, diagnostics = format_relevant_world_bible_for_prompt(
            bible, "爱丽丝调查钟楼", token_budget=180, return_diagnostics=True,
        )
        self.assertIn("午夜钟响", text)
        self.assertIn("爱丽丝", text)
        self.assertIn("char_bob", diagnostics["reasons"]["relationship_expansion_ids"])
        self.assertLessEqual(diagnostics["estimated_tokens"], 190)

    def test_duplicate_confirmation_is_reversible(self):
        bible = WorldBible(characters=[
            CharacterEntry(id="char_a", name="周宁", traits="医生"),
            CharacterEntry(id="char_b", name="小周", traits="急诊医生"),
        ])
        bible.duplicate_candidates.append({
            "id": "candidate_1", "entity_type": "character",
            "entity_ids": ["char_a", "char_b"], "names": ["周宁", "小周"], "status": "pending",
        })
        self.assertTrue(confirm_duplicate_candidate(bible, "candidate_1"))
        self.assertEqual(len(bible.characters), 1)
        self.assertTrue(undo_entity_merge(bible))
        self.assertEqual({item.name for item in bible.characters}, {"周宁", "小周"})

    def test_audit_detects_dangling_references(self):
        bible = WorldBible(characters=[
            CharacterEntry(id="char_a", name="甲", relationships=[Relationship(target="乙", target_id="missing")])
        ])
        warnings = audit_world_bible_consistency(bible)
        types = {item["type"] for item in warnings}
        self.assertIn("关系引用悬空", types)
        self.assertTrue(all("suggestion" in item for item in warnings))

    def test_long_chapter_split_preserves_ending(self):
        ending = "结尾反转：真正的凶手出现。"
        text = ("开场。" * 9000) + "\n\n" + ending
        chunks = _split_chapter_for_world_extraction(text, max_chars=4000)
        self.assertGreater(len(chunks), 2)
        self.assertEqual("".join(chunks).replace("\n", ""), text.replace("\n", ""))
        self.assertIn(ending, chunks[-1])

    def test_manager_migrates_with_backup_and_protects_corruption(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = NovelManager(bookshelf_root=temp_dir)
            title = "迁移测试"
            manager.create_book(title)
            path = Path(manager._world_bible_path(title))
            path.write_text(json.dumps({"characters": [{"name": "旧角色", "relationships": []}]}), encoding="utf-8")
            bible = manager.load_world_bible(title)
            self.assertEqual(bible.schema_version, 2)
            backups = list(path.parent.glob("world_bible.json.backup-schema-v2-*"))
            self.assertTrue(backups)

            path.write_text("{broken", encoding="utf-8")
            broken = manager.load_world_bible(title)
            self.assertEqual(broken.diagnostics.get("load_state"), "error")
            with self.assertRaises(RuntimeError):
                manager.save_world_bible(title, WorldBible())
            self.assertEqual(path.read_text(encoding="utf-8"), "{broken")


    def test_manager_active_path_rebuild_replays_manual_overrides(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = NovelManager(bookshelf_root=temp_dir)
            title = "分支重建"
            manager.create_book(title)
            extracted = {"characters": [{"name": "沈舟", "current_goal": "离开"}]}
            bible = merge_extracted_world_bible_data(
                WorldBible(), extracted, chapter_num=1, chapter_version=1,
                store_chapter_entry=True, run_dedup=False,
            )
            before = _flat_view_dict(bible)
            bible.characters[0].current_goal = "留下"
            record_manual_view_changes(bible, before)
            manager.save_world_bible(title, bible)
            manager.get_active_path_nodes = lambda _title: [{
                "id": "node-1", "chapter_num": 1, "version": 1, "summary": "沈舟抵达。",
            }]
            report = manager.rebuild_world_bible_from_active(None, title)
            rebuilt = manager.load_world_bible(title)
            self.assertEqual(rebuilt.characters[0].current_goal, "留下")
            self.assertEqual(report["override_count"], 1)
            self.assertEqual(report["schema_version"], 2)

    def test_polished_version_keeps_branch_summary_and_world_snapshot_isolated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = NovelManager(bookshelf_root=temp_dir)
            title = "润色分支绑定"
            manager.create_book(title)
            manager.save_chapter_version(title, 1, "起点", "第一章", version=1)
            manager.save_chapter_version(title, 2, "相遇", "旧版本", version=1)

            original = manager.ensure_chapter_tree(title).chapter_nodes["ch0002_v001"]
            manager.save_chapter_version(
                title,
                2,
                "相遇",
                "润色版本",
                version=2,
                parent_id=original["parent_id"],
            )
            manager.set_chapter_node_summary(title, 2, 1, "旧版概要")
            manager.set_chapter_node_summary(title, 2, 2, "润色版概要")

            bible = merge_extracted_world_bible_data(
                WorldBible(),
                {"characters": [{"name": "旧版角色"}]},
                chapter_num=2,
                chapter_version=1,
                store_chapter_entry=True,
                run_dedup=False,
            )
            bible = merge_extracted_world_bible_data(
                bible,
                {"characters": [{"name": "润色版角色"}]},
                chapter_num=2,
                chapter_version=2,
                store_chapter_entry=True,
                run_dedup=False,
            )
            manager.save_world_bible(title, bible)

            self.assertTrue(manager.switch_active_node(title, "ch0002_v002"))
            manager.rebuild_plot_summary_from_tree(title)
            manager.rebuild_world_bible_from_active(None, title)

            meta = manager.ensure_chapter_tree(title)
            self.assertEqual(
                meta.chapter_nodes["ch0002_v002"]["parent_id"],
                original["parent_id"],
            )
            summary = manager.load_summary(title)
            self.assertIn("润色版概要", summary)
            self.assertNotIn("旧版概要", summary)
            names = {item.name for item in manager.load_world_bible(title).characters}
            self.assertIn("润色版角色", names)
            self.assertNotIn("旧版角色", names)

    def test_schema_validation_keeps_valid_categories(self):
        bible = merge_extracted_world_bible_data(
            WorldBible(),
            {"characters": "invalid", "rules": ["有效规则"], "story_clock": []},
            chapter_num=1, run_dedup=False,
        )
        self.assertFalse(bible.diagnostics["last_validation"]["valid"])
        self.assertEqual(bible.rules, ["有效规则"])
        self.assertEqual(bible.characters, [])
    def test_encrypted_round_trip_and_snapshot_deletion(self):
        class PlainCrypto:
            @staticmethod
            def encrypt_json(key, path, data):
                Path(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

            @staticmethod
            def decrypt_json(key, path):
                return json.loads(Path(path).read_text(encoding="utf-8"))

            @staticmethod
            def encrypt_text(key, path, text):
                Path(path).write_text(text, encoding="utf-8")

            @staticmethod
            def decrypt_text(key, path):
                return Path(path).read_text(encoding="utf-8")

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = NovelManager(bookshelf_root=temp_dir, crypto=PlainCrypto(), enc_key=b"test")
            title = "加密往返"
            manager.create_book(title)
            bible = merge_extracted_world_bible_data(
                WorldBible(), {"characters": [{"name": "密钥角色"}]},
                chapter_num=1, chapter_version=1, store_chapter_entry=True, run_dedup=False,
            )
            manager.save_world_bible(title, bible)
            actual = Path(manager._world_bible_path(title) + ".enc")
            self.assertTrue(actual.exists())
            loaded = manager.load_world_bible(title)
            self.assertEqual(loaded.characters[0].name, "密钥角色")
            manager._delete_world_bible_snapshot(title, 1, 1)
            reloaded = manager.load_world_bible(title)
            self.assertNotIn("ch0001_v001", reloaded.chapter_snapshots)
            self.assertNotIn("ch0001_v001", reloaded.chapter_world_entries)

if __name__ == "__main__":
    unittest.main()
