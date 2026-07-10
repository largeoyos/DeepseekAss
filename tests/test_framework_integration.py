from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.agent.backends import AutoFallbackAgentBackend, BackendStatus
from core.agent.langgraph_backend import EncryptedAgentCheckpointer, LangGraphAgentBackend
from core.agent.profiles import get_agent_profile
from core.agent.repository import AgentRepository
from core.agent.types import AgentRun
from core.agent.domain_tools import build_domain_tool_registry
from core.agent.tools import ToolRegistry, ToolSpec
from core.agent.types import AgentRunRequest
from core.novel_manager import NovelManager
from core.retrieval import ClassicRetrievalBackend, LlamaIndexHybridBackend


class PlainCrypto:
    @staticmethod
    def encrypt_text(_key, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(base64.b64encode(text.encode("utf-8")).decode("ascii"))

    @staticmethod
    def decrypt_text(_key, path):
        with open(path, "r", encoding="utf-8") as handle:
            return base64.b64decode(handle.read()).decode("utf-8")

    @staticmethod
    def encrypt_json(key, path, data):
        PlainCrypto.encrypt_text(key, path, json.dumps(data, ensure_ascii=False))

    @staticmethod
    def decrypt_json(key, path):
        if not os.path.exists(path):
            return None
        return json.loads(PlainCrypto.decrypt_text(key, path))


class FakeEmbedder:
    def get_text_embedding(self, text):
        return self._vector(text)

    def get_query_embedding(self, text):
        return self._vector(text)

    @staticmethod
    def _vector(text):
        value = str(text)
        return [float("主角" in value), float("秘密" in value), min(len(value), 1000) / 1000.0]


class FailingEmbedder(FakeEmbedder):
    def get_query_embedding(self, text):
        raise RuntimeError("embedding unavailable")


class FrameworkIntegrationTests(unittest.TestCase):
    def _book_with_two_chapters(self, root, *, encrypted=False):
        manager = NovelManager(
            bookshelf_root=root,
            crypto=PlainCrypto() if encrypted else None,
            enc_key=b"key" if encrypted else None,
        )
        manager.create_book("book")
        manager.save_chapter_version("book", 1, "开端", "主角在车站发现秘密。", version=1)
        manager.save_chapter_version("book", 2, "追踪", "主角沿着线索继续追踪。", version=1)
        manager.switch_active_node("book", "ch0002_v001")
        return manager

    def test_classic_retrieval_filters_non_active_if_branch(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self._book_with_two_chapters(root)
            manager.save_extra_node(
                "book",
                run_id="if-run",
                extra_type="if_line",
                chapter_title="另一种选择",
                content="支线独有的黑塔事件。",
                start_node_id="ch0001_v001",
                end_node_id="ch0002_v001",
            )
            results = ClassicRetrievalBackend(manager).search("book", "黑塔事件", limit=10)
            self.assertFalse(any(item.source_id.startswith("extra_") for item in results))

    def test_classic_retrieval_uses_configured_limit_and_min_score(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("book")
            manager.save_chapter_version("book", 1, "Alpha", "alpha beta", version=1)
            manager.save_chapter_version("book", 2, "Only Alpha", "alpha", version=1)
            manager.switch_active_node("book", "ch0001_v001")
            backend = ClassicRetrievalBackend(
                manager,
                {"retrieval_default_limit": 1, "retrieval_min_score": 1.0},
            )
            results = backend.search("book", "alpha")
            self.assertEqual(1, len(results))
            self.assertEqual("ch0001_v001", results[0].source_id)
            self.assertGreaterEqual(results[0].score, 0.6)

    def test_hybrid_retrieval_uses_configured_weights(self):
        class WeightedEmbedder:
            def get_query_embedding(self, _text):
                return [1.0, 0.0]

            def get_text_embedding(self, text):
                return [1.0, 0.0] if "semantic-only" in str(text) else [0.0, 1.0]

        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("book")
            manager.save_chapter_version("book", 1, "Keyword", "target keyword document", version=1)
            manager.save_chapter_version("book", 2, "Semantic", "semantic-only document", version=1)
            manager.switch_active_node("book", "ch0002_v001")
            settings = {
                "embedding_model": "fake",
                "framework_auto_fallback": True,
                "retrieval_default_limit": 1,
                "retrieval_keyword_weight": 0,
                "retrieval_semantic_weight": 100,
                "retrieval_min_score": 0.5,
            }
            with patch.object(LlamaIndexHybridBackend, "_build_embedder", return_value=WeightedEmbedder()):
                results = LlamaIndexHybridBackend(manager, settings).search("book", "target")
            self.assertEqual(1, len(results))
            self.assertEqual("ch0002_v001", results[0].source_id)
            self.assertIn("×1.00", results[0].reason)

    def test_hybrid_index_is_encrypted_incremental_and_contains_no_plain_chapter(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self._book_with_two_chapters(root, encrypted=True)
            settings = {"embedding_model": "fake", "framework_auto_fallback": True}
            with patch.object(LlamaIndexHybridBackend, "_build_embedder", return_value=FakeEmbedder()):
                backend = LlamaIndexHybridBackend(manager, settings)
                results = backend.search("book", "主角秘密", limit=5)
                self.assertTrue(results)
                workspace = manager.get_workspace("book")
                logical = f"{workspace.agent_root}/retrieval/index.json"
                actual = workspace.storage.actual_path(logical)
                self.assertTrue(actual.endswith(".enc"))
                self.assertTrue(os.path.exists(actual))
                with open(actual, "r", encoding="utf-8") as handle:
                    raw = handle.read()
                self.assertNotIn("主角在车站发现秘密", raw)
                revision = backend.status("book")["revision"]

                manager.configure_retrieval(settings)
                manager._retrieval_backend_instance = backend
                manager.save_chapter_version("book", 3, "逼近", "主角逼近秘密核心。", version=1)
                self.assertTrue(backend.status("book")["dirty"])
                backend.search("book", "秘密核心", limit=5)
                self.assertGreater(backend.status("book")["revision"], revision)

    def test_hybrid_embedding_failure_uses_classic_results(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self._book_with_two_chapters(root)
            with patch.object(LlamaIndexHybridBackend, "_build_embedder", return_value=FailingEmbedder()):
                backend = LlamaIndexHybridBackend(
                    manager,
                    {"embedding_model": "fake", "framework_auto_fallback": True},
                )
                results = backend.search("book", "车站秘密", limit=5)
                self.assertTrue(results)
                self.assertIn("embedding unavailable", backend._last_fallback_reason)

    def test_encrypted_langgraph_checkpointer_roundtrip(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self._book_with_two_chapters(root, encrypted=True)
            repository = AgentRepository(manager.get_workspace("book"))
            saver = EncryptedAgentCheckpointer(repository)
            config = {"configurable": {"thread_id": "run-test", "checkpoint_ns": ""}}
            saved = saver.put(
                config,
                {
                    "id": "checkpoint-1",
                    "channel_values": {},
                    "channel_versions": {},
                    "versions_seen": {},
                    "pending_sends": [],
                    "v": 4,
                    "ts": "now",
                },
                {"source": "input", "step": 0, "parents": {}},
                {},
            )
            loaded = saver.get_tuple(saved)
            self.assertEqual("checkpoint-1", loaded.checkpoint["id"])
            files = manager.get_workspace("book").storage.list_files(
                f"{manager.get_workspace('book').agent_root}/langgraph/run-test"
            )
            self.assertTrue(files)

    def test_real_langgraph_graph_builds_with_wrapped_domain_tools(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self._book_with_two_chapters(root)
            client = SimpleNamespace(
                raw_client=SimpleNamespace(api_key="test", base_url="https://example.com/v1"),
                temperature=0.3,
            )
            backend = LangGraphAgentBackend(
                novel_manager=manager,
                client=client,
                tool_registry=build_domain_tool_registry(manager),
            )
            session = backend.create_session("book", "writing_advisor")
            run = AgentRun(
                "run-build",
                session.session_id,
                manager.ensure_workspace("book").book_id,
                "book",
                "writing_advisor",
                "test-model",
                status="running",
            )
            graph = backend._build_graph(
                run,
                AgentRepository(manager.get_workspace("book")),
                get_agent_profile("writing_advisor"),
                "",
            )
            self.assertEqual("_Saver", type(graph.checkpointer).__name__)

    def test_langgraph_approval_resume_does_not_repeat_tool_side_effect(self):
        try:
            from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
            from langchain_core.messages import AIMessage
        except ImportError:
            self.skipTest("LangChain test model unavailable")
        with tempfile.TemporaryDirectory() as root:
            manager = self._book_with_two_chapters(root)
            calls = {"count": 0}
            registry = ToolRegistry()

            def propose(_context, arguments):
                calls["count"] += 1
                return {
                    "requires_approval": True,
                    "change_set_id": "changes-test",
                    "value": arguments["value"],
                }

            registry.register(ToolSpec(
                "world_bible.propose_patch",
                "提出世界书字段变更",
                {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
                propose,
                required_permission="confirmed_write",
                read_only=False,
                produces_change_set=True,
                allowed_agents=["world_bible_manager"],
            ))
            class ToolCallingFakeModel(FakeMessagesListChatModel):
                def bind_tools(self, tools, *, tool_choice=None, **kwargs):
                    return self

            model = ToolCallingFakeModel(responses=[
                AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "world_bible.propose_patch",
                        "args": {"value": "新设定"},
                        "id": "tool-call-1",
                        "type": "tool_call",
                    }],
                ),
                AIMessage(content="变更已提交审批。"),
            ])
            backend = LangGraphAgentBackend(
                novel_manager=manager,
                client=SimpleNamespace(temperature=0.3),
                tool_registry=registry,
                services={"langchain_model": model},
            )
            session = backend.create_session("book", "world_bible_manager")
            request = AgentRunRequest(
                manager.ensure_workspace("book").book_id,
                session.session_id,
                "world_bible_manager",
                "提出新设定",
                [],
                "fake",
                "advisor",
                book_title="book",
            )
            run = backend.run(request)
            self.assertEqual("waiting_approval", run.status, f"error={run.error!r} terminal={run.terminal_reason!r} calls={run.tool_calls!r}")
            self.assertEqual(1, calls["count"])
            resumed = backend.resume(run.run_id, {"approved": True})
            self.assertEqual("completed", resumed.status)
            self.assertEqual(1, calls["count"])
    def test_runtime_failure_switches_to_legacy_fallback(self):
        class Primary:
            def run(self, request):
                raise RuntimeError("graph failed")

        class Fallback:
            def run(self, request):
                return SimpleNamespace(status="completed")

        status = BackendStatus("langgraph", "langgraph")
        backend = AutoFallbackAgentBackend(Primary(), Fallback(), status)
        result = backend.run(object())
        self.assertEqual("completed", result.status)
        self.assertEqual("legacy", status.active)
        self.assertEqual("graph failed", status.fallback_reason)


if __name__ == "__main__":
    unittest.main()
