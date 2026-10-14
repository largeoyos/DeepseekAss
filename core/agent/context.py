from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class AgentContextReport:
    content: str
    sources: list[dict] = field(default_factory=list)
    total_chars: int = 0
    omitted_chars: int = 0


class AgentContextAssembler:
    """Builds inspectable book facts before the model begins its tool loop."""

    def __init__(self, novel_manager, max_chars: int = 30000) -> None:
        self.manager = novel_manager
        self.max_chars = max_chars

    def assemble_for_request(self, request) -> AgentContextReport:
        if request.agent_kind == "writing_advisor":
            return self.assemble_advisor(
                request.book_title, request.context_query or request.user_message,
                request.manual_references,
            )
        return self.assemble(request.book_title, request.manual_references)

    def assemble_advisor(self, book_title: str, question: str,
                         manual_references: list[str] | None = None) -> AgentContextReport:
        """Cheap local context; no chapter pipeline, embedding search, or model calls.

        Only stored summaries on the active branch can be preloaded. Unavailable
        facts remain available through tools rather than being guessed or rebuilt.
        Explicit references take priority over the automatic 6000-character budget.
        """
        meta = self.manager.load_meta(book_title)
        parts, sources = [], []
        omitted = 0
        remaining = min(self.max_chars, 6000)

        def add(source, title, text, budget, *, explicit=False):
            nonlocal remaining, omitted
            text = str(text or "").strip()
            if not text:
                return
            heading = f"【{title}】\n"
            allowed = budget if explicit else max(0, min(budget, remaining - len(heading) - 2))
            if not allowed:
                omitted += len(text)
                return
            clipped = text
            skipped = 0
            if len(text) > allowed:
                marker = "\n[节选，完整资料可通过工具读取]"
                kept = max(0, allowed - len(marker))
                clipped = text[:kept] + marker[:allowed]
                skipped = len(text) - kept
            omitted += skipped
            parts.append(heading + clipped)
            sources.append({"source": source, "title": title, "chars": len(clipped),
                            "reason": "用户手动引用" if explicit else "顾问本地预读", "omitted_chars": skipped})
            if not explicit:
                remaining -= len(heading) + len(clipped) + 2

        add("scope", "资料范围", "以下仅为本地设定节选与命中的已存摘要，不代表已读取全书正文或世界书。"
            "资料足够时直接回答；需要核实书中事实时按需调用工具，不能把未加载视为不存在。", 250)
        add("book", "当前作品", f"书名：{book_title}\n题材：{meta.genre}\n文风：{meta.style_tone}", 300)
        references = list(dict.fromkeys(str(ref) for ref in manual_references or [] if str(ref).strip()))
        policies = self.manager.get_workspace(book_title).load_context_policies()
        resident_ids = {entity_id for entity_id, policy in policies.items()
                        if policy.get("enabled", True) and policy.get("load_mode") == "resident"}
        manual_details, resident_details = [], []
        if references or resident_ids:
            from core.context_assembler import _entity_text, _world_entities
            bible = self.manager.load_world_bible(book_title)
            for entity_id, kind, _name, item in _world_entities(bible):
                if not dict(policies.get(entity_id) or {}).get("enabled", True):
                    continue
                if entity_id in references:
                    manual_details.append(f"[{entity_id}]\n" + _entity_text(kind, item))
                elif entity_id in resident_ids:
                    resident_details.append(f"[{entity_id}]\n" + _entity_text(kind, item))
        # Honor resident policies without loading/serializing the world book at
        # all when neither resident entries nor explicit references are requested.
        add("world_resident", "世界书常驻条目", "\n\n".join(resident_details), 2000)
        add("writing_demand", "写作要求", meta.writing_demand, 600)
        add("protagonist_bio", "主角设定", meta.protagonist_bio, 700)
        add("background_story", "背景设定", meta.background_story, 700)
        add("author_plan", "作者规划（尚未发生的意图）", meta.author_plan, 900)

        query = re.sub(r"\s+", "", "\n".join([question, *references])).lower()
        chapter_numbers = set()
        for chinese, english in re.findall(r"第([0-9零〇一二两三四五六七八九十百千]+)章|chapter(\d+)", query):
            chapter_numbers.add(self._chapter_number(chinese or english))
        recent = any(word in query for word in (
            "下一章", "后续", "接下来", "续写", "最近", "上一章", "最新",
            "nextchapter", "continue", "latest", "previouschapter",
        ))
        active = [meta.chapter_nodes[node_id] for node_id in meta.active_path
                  if node_id in meta.chapter_nodes and not meta.chapter_nodes[node_id].get("virtual")]
        selected = [node for node in active if int(node.get("chapter_num") or 0) in chapter_numbers
                    or (len(str(node.get("title") or "")) >= 2
                        and re.sub(r"\s+", "", str(node["title"])).lower() in query)]
        if recent:
            selected.extend(node for node in active[-2:] if node not in selected)
        for node in selected[:3]:
            summary = str(node.get("summary") or "").strip()
            if summary:
                add("chapter_summary", f"第{node.get('chapter_num')}章 {node.get('title', '')}（活跃版本摘要）", summary, 750)

        if references:
            # Preserve literal references even when they are prose rather than IDs.
            # This also lets the model resolve references omitted by the detail budget.
            text = "\n".join(references)
            add("manual_reference", "用户指定参考（未展开的引用需按需查询）", text, len(text), explicit=True)
            add("manual_entities", "手动引用的世界书条目", "\n\n".join(manual_details), 8000, explicit=True)

        content = "\n\n".join(parts)
        return AgentContextReport(content, sources, len(content), omitted)

    @staticmethod
    def _chapter_number(text: str) -> int:
        if text.isdigit():
            return int(text)
        digits = dict(zip("零〇一二两三四五六七八九", (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
        if not any(char in "十百千" for char in text):
            return int("".join(str(digits[char]) if char in digits else char for char in text))
        total = number = 0
        for char in text:
            if char in digits or char.isdigit():
                number = digits[char] if char in digits else int(char)
            else:
                total += (number or 1) * {"十": 10, "百": 100, "千": 1000}[char]
                number = 0
        return total + number

    def assemble(self, book_title: str, manual_entity_ids: list[str] | None = None) -> AgentContextReport:
        meta = self.manager.load_meta(book_title)
        next_chapter = max(1, int(meta.total_chapters or 0) + 1)
        report = self.manager.context_assembler().assemble_chapter(
            book_title,
            next_chapter,
            "Agent 当前任务",
            meta.writing_demand,
            manual_entity_ids=manual_entity_ids or [],
        )
        rendered = report.render()
        omitted = report.omitted_chars
        if len(rendered) > self.max_chars:
            omitted += len(rendered) - self.max_chars
            rendered = rendered[:self.max_chars] + f"\n\n[Agent 初始上下文已截断 {omitted} 字，可通过工具按需读取。]"
        sources = [{"source": section.source, "title": section.title, "chars": len(section.content), "reason": section.reason, "omitted_chars": section.omitted_chars} for section in report.sections]
        return AgentContextReport(rendered, sources, len(rendered), omitted)
