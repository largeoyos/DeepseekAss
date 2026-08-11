import tempfile
import unittest

from core.token_log_manager import TokenLogManager


class TokenLogManagerReasoningTests(unittest.TestCase):
    def _manager(self) -> TokenLogManager:
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        return TokenLogManager(root.name)

    def _usage(self, completion: int, reasoning: int) -> dict:
        return {
            "prompt_tokens": 100,
            "completion_tokens": completion,
            "total_tokens": 100 + completion,
            "completion_tokens_details": {"reasoning_tokens": reasoning},
        }

    def test_reasoning_fields_roundtrip(self):
        manager = self._manager()
        entry = manager.add_entry(
            operation="章节生成", direction="receive", strategy="novel", model="deepseek-v4-flash",
            content="正文内容",
            reasoning_content="先分析主角动机，再安排钟楼冲突。",
            reasoning_tokens=321,
            usage=self._usage(321, 321),
        )
        self.assertEqual(321, entry.reasoning_tokens)
        self.assertEqual("先分析主角动机，再安排钟楼冲突。", entry.reasoning_content_full)
        self.assertEqual("先分析主角动机，再安排钟楼冲突。", entry.reasoning_content_preview)

        loaded = manager.list_entries()[0]
        self.assertEqual(321, loaded.reasoning_tokens)
        self.assertEqual(entry.reasoning_content_full, loaded.reasoning_content_full)
        self.assertEqual(entry.reasoning_content_preview, loaded.reasoning_content_preview)

    def test_reasoning_preview_truncated(self):
        manager = self._manager()
        long_reasoning = "很长的推理内容。" * 20
        entry = manager.add_entry(
            operation="生成", direction="receive", strategy="novel", model="m",
            content="", reasoning_content=long_reasoning, reasoning_tokens=999,
            usage=self._usage(999, 999),
        )
        self.assertTrue(entry.reasoning_content_preview.endswith("..."))
        self.assertLessEqual(len(entry.reasoning_content_preview), 63)
        self.assertEqual("", entry.content_preview)
        self.assertEqual(999, entry.reasoning_tokens)

    def test_legacy_entries_default_reasoning_fields(self):
        manager = self._manager()
        entry = manager.add_entry(
            operation="旧版", direction="receive", strategy="novel", model="m",
            content="只有正文", usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        )
        self.assertEqual("", entry.reasoning_content_preview)
        self.assertEqual("", entry.reasoning_content_full)
        self.assertIsNone(entry.reasoning_tokens)
        self.assertEqual("只有正文", manager.list_entries()[0].content_preview)

    def test_manager_preserves_reasoning_when_provided(self):
        # 方向过滤由调用方负责（发送方向不会传推理内容）；底层存储应原样保留。
        manager = self._manager()
        entry = manager.add_entry(
            operation="生成", direction="send", strategy="novel", model="m",
            content="提示词", reasoning_content="推理内容", reasoning_tokens=7,
            usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        )
        self.assertEqual("推理内容", entry.reasoning_content_preview)
        self.assertEqual(7, entry.reasoning_tokens)


if __name__ == "__main__":
    unittest.main()