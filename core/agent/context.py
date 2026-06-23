from __future__ import annotations

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
