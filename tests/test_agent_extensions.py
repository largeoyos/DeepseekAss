import tempfile
import time
import unittest

from core.agent.changes import ChangeSetService
from core.agent.domain_tools import build_domain_tool_registry
from core.agent.profiles import get_agent_profile
from core.agent.repository import AgentRepository
from core.agent.skills import SkillService
from core.agent.tools import ToolContext, ToolRegistry, ToolSpec
from core.agent.types import ToolCallRequest
from core.agent.web_search import WebSearchClient, WebSearchConfig
from core.novel_manager import NovelManager


class ExtendedAgentTests(unittest.TestCase):
    def test_new_agent_profiles_and_tools_are_registered(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("book")
            registry = build_domain_tool_registry(manager)
            advisor = get_agent_profile("writing_advisor")
            supervisor = get_agent_profile("chapter_supervisor")
            world = get_agent_profile("world_bible_manager")
            self.assertIn("chapter.read_node", advisor.allowed_tools)
            self.assertIn("project.active_state", supervisor.allowed_tools)
            self.assertIn("world_bible.propose_patch", world.allowed_tools)
            names = {item["function"]["name"] for item in registry.schemas_for(advisor.allowed_tools)}
            self.assertIn("chapter.summary_search", names)
            self.assertIn("agent.save_advice", names)
            self.assertNotIn("web.search", names)

    def test_web_search_rejects_private_endpoint(self):
        config = WebSearchConfig(enabled=True, endpoint="https://127.0.0.1/search")
        from core.agent.web_search import WebSearchError
        with self.assertRaises(WebSearchError):
            WebSearchClient(config).search("test")

    def test_web_search_tool_is_hidden_until_configured(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("book")
            disabled = build_domain_tool_registry(manager, web_search_config={"agent_web_enabled": False})
            self.assertEqual([], disabled.schemas_for(["web.search"]))
            enabled = build_domain_tool_registry(manager, web_search_config={
                "agent_web_enabled": True,
                "agent_web_endpoint": "https://search.example.com/api",
            })
            self.assertEqual("web.search", enabled.schemas_for(["web.search"])[0]["function"]["name"])

    def test_tool_timeout_returns_failure_without_waiting_for_completion(self):
        registry = ToolRegistry()
        registry.register(ToolSpec(
            "slow", "slow", {"type": "object", "properties": {}},
            lambda _ctx, _args: time.sleep(0.2), timeout_seconds=0.02,
        ))
        context = ToolContext("run", "book", "book", "writing_advisor", "read_only", object())
        started = time.monotonic()
        result = registry.execute(ToolCallRequest("1", "slow", {}), context, ["slow"])
        self.assertFalse(result.success)
        self.assertEqual("tool_failed", result.error_code)
        self.assertIn("timed out", result.content)
        self.assertLess(time.monotonic() - started, 0.15)

    def test_world_patch_requires_approval_and_applies_field_change(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("book")
            manifest = manager.ensure_workspace("book")
            repository = AgentRepository(manager.get_workspace("book"))
            service = ChangeSetService(manager, "book", repository)
            change = service.propose_world_patch("run", manifest.book_id, [{
                "operation": "entity.create",
                "entity_type": "character",
                "entity_id": "character_test",
                "payload": {"id": "character_test", "name": "测试角色", "traits": "谨慎"},
            }])
            self.assertEqual("pending", change.status)
            self.assertFalse(manager.load_world_bible("book").characters)
            service.approve(change.change_set_id)
            bible = manager.load_world_bible("book")
            self.assertEqual("测试角色", bible.characters[0].name)
            self.assertTrue(any(item.entity_id == "character_test" for item in bible.manual_overrides))
            self.assertTrue(manager.snapshot_service("book").list())

    def test_new_agents_receive_matching_skills(self):
        with tempfile.TemporaryDirectory() as root:
            manager = NovelManager(bookshelf_root=root)
            manager.create_book("book")
            skills = SkillService(AgentRepository(manager.get_workspace("book")))
            supervisor = {item.skill_id for item in skills.select_for_task(
                "chapter_supervision", "chapter_supervisor", "连续性和场景节奏"
            ).documents}
            world = {item.skill_id for item in skills.select_for_task(
                "world_bible_management", "world_bible_manager", "世界书伏笔"
            ).documents}
            self.assertIn("continuity-review", supervisor)
            self.assertIn("world-bible-maintenance", world)
            self.assertIn("foreshadowing", world)


if __name__ == "__main__":
    unittest.main()
