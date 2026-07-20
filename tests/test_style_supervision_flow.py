import tempfile
import unittest
from unittest.mock import patch

from core.agent.supervision_agent import AgentSupervisionService, SupervisionRequest
from core.novel_manager import NovelManager
from utils.supervision import SupervisionResult


class StyleSupervisionFlowTests(unittest.TestCase):
    def test_agent_supervisor_forwards_style_audit_separately(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("book")
            captured = {}

            def fake_supervise(_client_factory, **kwargs):
                captured.update(kwargs)
                return kwargs["chapter_content"], SupervisionResult(status="passed")

            request = SupervisionRequest(
                book_title="book",
                chapter_num=1,
                chapter_title="????",
                chapter_content="??" * 120,
                chapter_outline="???????",
                requirements="????????",
                continuity_context="",
                target_words=0,
                model="test",
                style_audit="???????????????",
                content_lock="人物身份和事件顺序不得改变",
                max_repair_rounds=1,
                style_profile_metrics={"sentence_length_avg": 12.0},
                style_profile_name="测试文风",
            )
            service = AgentSupervisionService(manager, lambda _action: None)
            with patch("utils.supervision.supervise_chapter", side_effect=fake_supervise):
                result = service.supervise(request)

            self.assertEqual("????????", captured["requirements"])
            self.assertEqual(request.style_audit, captured["style_audit"])
            self.assertEqual(request.chapter_content, result.content)
            self.assertEqual(request.content_lock, captured["content_lock"])
            self.assertEqual(1, captured["max_repair_rounds"])
            self.assertEqual(request.style_profile_metrics, captured["style_profile_metrics"])
            self.assertEqual("测试文风", captured["style_profile_name"])


if __name__ == "__main__":
    unittest.main()
