import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from core.agent.changes import ChangeSetError, ChangeSetService
from core.agent.repository import AgentRepository
from core.novel_manager import NovelManager
from web.server import ChangeApprovalRequest, create_app
from web.services import WebRuntime


class ChangeApprovalSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = NovelManager(self.temp.name)
        self.manager.create_book("book")
        self.repository = AgentRepository(self.manager.get_workspace("book"))
        self.service = ChangeSetService(self.manager, "book", self.repository)
        self.change = self.service.propose_chapter("run", "book-id", 1, "first", "draft")

    def test_empty_and_unknown_selection_do_not_apply_or_create_snapshot(self):
        for selected in ([], ["unknown"], [self.change.operations[0].operation_id, "unknown"]):
            with self.subTest(selected=selected), patch.object(self.manager, "snapshot_service") as snapshot:
                with self.assertRaises(ChangeSetError):
                    self.service.approve(self.change.change_set_id, selected)
                snapshot.assert_not_called()
                self.assertEqual("pending", self.repository.load_change_set(self.change.change_set_id).status)
                self.assertIsNone(self.manager.read_active_chapter("book", 1))

    def test_omitted_selection_still_approves_all(self):
        self.assertEqual("applied", self.service.approve(self.change.change_set_id).status)
        self.assertEqual("draft", self.manager.read_active_chapter("book", 1))

    def test_partial_selection_applies_only_selected_operations(self):
        second = self.service.propose_chapter("run", "book-id", 2, "second", "unapproved")
        self.change.operations.extend(second.operations)
        self.repository.save_change_set(self.change)
        result = self.service.approve(self.change.change_set_id, [self.change.operations[0].operation_id])
        self.assertEqual("partially_applied", result.status)
        self.assertEqual("draft", self.manager.read_active_chapter("book", 1))
        self.assertIsNone(self.manager.read_active_chapter("book", 2))

    def test_web_returns_validation_error_for_empty_selection(self):
        app = create_app(WebRuntime(client_factory=lambda _: None))
        endpoint = next(route.endpoint for route in app.routes
                        if getattr(route, "path", "") == "/api/books/{title}/agent/changes/approve")
        with self.assertRaises(HTTPException) as caught:
            endpoint("book", ChangeApprovalRequest(change_set_id=self.change.change_set_id, operation_ids=[]),
                     ctx=SimpleNamespace(novel_manager=self.manager))
        self.assertEqual(400, caught.exception.status_code)
        self.assertEqual("pending", self.repository.load_change_set(self.change.change_set_id).status)


if __name__ == "__main__":
    unittest.main()
