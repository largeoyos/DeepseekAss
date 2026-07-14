from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field

from core.agent.types import now_iso
from ui.continuation_dialogs import (
    DIRECTION_BACKGROUND_MAX_CHARS,
    DIRECTION_PLOT_MAX_CHARS,
    DIRECTION_REQUIREMENT_MAX_CHARS,
    DIRECTION_WORLD_MAX_CHARS,
    SUGGESTION_PROMPT,
    _build_world_summary,
    _safe_format,
)
from utils.prompts import Prompts


@dataclass
class AgentContinuationRun:
    run_id: str
    task: str
    book_title: str = ""
    selected_skills: list[dict] = field(default_factory=list)
    input_chars: int = 0
    output_summary: dict = field(default_factory=dict)
    status: str = "completed"
    created_at: str = field(default_factory=now_iso)



@dataclass
class SegmentationResult:
    """Lossless continuation segmentation plus execution diagnostics."""

    sections: list[tuple[str, str]] = field(default_factory=list)
    total_chars: int = 0
    covered_chars: int = 0
    chunks_total: int = 0
    agent_chunks: int = 0
    fallback_chunks: int = 0
    repair_attempts: int = 0
    errors: list[str] = field(default_factory=list)
    selected_skills: list[dict] = field(default_factory=list)

    @property
    def used_fallback(self) -> bool:
        return self.fallback_chunks > 0

    @property
    def coverage_ratio(self) -> float:
        return 1.0 if not self.total_chars else self.covered_chars / self.total_chars

class AgentContinuationService:
    """Deterministic Agent facade for continuation import analysis, segmentation and direction planning."""

    MAX_SEGMENT_CHARS = 40_000
    MIN_ANCHOR_CHARS = 20
    MAX_ANCHOR_CHARS = 60

    def __init__(self, novel_manager=None, client=None, *, skills_enabled: bool = True) -> None:
        self.manager = novel_manager
        self.client = getattr(client, "raw_client", client)
        self.skills_enabled = skills_enabled

    def segment_text(
        self,
        text: str,
        model: str,
        *,
        book_title: str = "",
        global_user_prompt: str = "",
    ) -> list[tuple[str, str]]:
        """Return sections only for backwards-compatible callers."""
        return self.segment_text_with_report(
            text,
            model,
            book_title=book_title,
            global_user_prompt=global_user_prompt,
        ).sections

    def segment_text_with_report(
        self,
        text: str,
        model: str,
        *,
        book_title: str = "",
        global_user_prompt: str = "",
    ) -> SegmentationResult:
        """Segment losslessly: the model chooses boundaries, never returns body text."""
        source = "" if text is None else str(text)
        if not source:
            return SegmentationResult()

        skills = self._select_skills(book_title, "continuation_segmentation", source[:1000])
        chunks = self._split_source_chunks(source)
        result = SegmentationResult(
            total_chars=len(source),
            chunks_total=len(chunks),
            selected_skills=skills.summaries,
        )
        for chunk_index, chunk in enumerate(chunks, 1):
            sections, repairs, error = self._segment_chunk(
                chunk, model, skills.text, global_user_prompt
            )
            result.repair_attempts += repairs
            if error:
                result.fallback_chunks += 1
                result.errors.append(f"第 {chunk_index}/{len(chunks)} 块：{error}")
                sections = self._split_text_locally_preserving_content(chunk)
            else:
                result.agent_chunks += 1
            if not self._is_lossless(sections, chunk):
                if not error:
                    result.agent_chunks -= 1
                    result.fallback_chunks += 1
                result.errors.append(
                    f"第 {chunk_index}/{len(chunks)} 块：覆盖校验失败，已使用全文回退"
                )
                sections = [("全文", chunk)]
            result.sections.extend(sections)

        result.covered_chars = sum(len(content) for _, content in result.sections)
        if not self._is_lossless(result.sections, source):
            result.sections = [("全文", source)]
            result.covered_chars = len(source)
            result.agent_chunks = 0
            result.fallback_chunks = len(chunks)
            result.errors.append("合并后的分段未能完整覆盖原文，已安全回退为全文")

        self._save_run(book_title, AgentContinuationRun(
            run_id=f"cont_seg_{uuid.uuid4().hex}",
            task="continuation_segmentation",
            book_title=book_title,
            selected_skills=skills.summaries,
            input_chars=len(source),
            output_summary={
                "segments": len(result.sections),
                "total_chars": result.total_chars,
                "covered_chars": result.covered_chars,
                "coverage_ratio": result.coverage_ratio,
                "chunks_total": result.chunks_total,
                "agent_chunks": result.agent_chunks,
                "fallback_chunks": result.fallback_chunks,
                "repair_attempts": result.repair_attempts,
                "fallback": result.used_fallback,
                "errors": result.errors[:10],
            },
        ))
        return result

    def _segment_chunk(
        self,
        source: str,
        model: str,
        skills_text: str,
        global_user_prompt: str,
    ) -> tuple[list[tuple[str, str]], int, str]:
        raw = ""
        try:
            raw = self._request_segmentation(source, model, skills_text, global_user_prompt)
            return self._parse_sections(raw, source), 0, ""
        except Exception as first_error:
            try:
                repaired = self._request_segmentation(
                    source,
                    model,
                    skills_text,
                    global_user_prompt,
                    repair_error=str(first_error),
                    invalid_response=raw,
                )
                return self._parse_sections(repaired, source), 1, ""
            except Exception as repair_error:
                return [], 1, f"Agent 输出无效（{repair_error}）"

    def _request_segmentation(
        self,
        source: str,
        model: str,
        skills_text: str,
        global_user_prompt: str,
        *,
        repair_error: str = "",
        invalid_response: str = "",
    ) -> str:
        prompt = (
            "你是续写导入分段 Agent。请为给定原文块标记连续段落的起点。\n"
            "规则：\n"
            "1. 正文由程序按原文切片，绝不输出 content、正文、改写文本或 Markdown。\n"
            "2. 只输出 JSON 数组，每项只含 title 和 start_quote。\n"
            "3. 第一项 start_quote 必须是空字符串，表示当前原文块的开头。\n"
            f"4. 后续 start_quote 必须逐字复制原文中该段开头连续 {self.MIN_ANCHOR_CHARS}-{self.MAX_ANCHOR_CHARS} 个字符。\n"
            "5. 标题简短准确；锚点必须按原文顺序且不得重复。\n"
            "格式：[{\"title\":\"开端\",\"start_quote\":\"\"},{\"title\":\"转折\",\"start_quote\":\"原文连续片段\"}]。\n"
            f"\n【可用写作 Skills】\n{skills_text}\n"
            f"\n【用户偏好】\n{global_user_prompt}\n"
        )
        if repair_error:
            prompt += (
                "\n【上次输出无效】\n"
                f"错误：{repair_error[:500]}\n"
                f"无效输出：{invalid_response[:4000]}\n"
                "请仅返回修正后的合法 JSON 数组，不要解释。\n"
            )
        prompt += f"\n【原文块】\n{source}"
        response = self.client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=2000,
        )
        return str(response.choices[0].message.content or "")

    def generate_settings_from_world_data(
        self,
        world_data: dict,
        model: str,
        *,
        book_title: str = "",
        global_user_prompt: str = "",
        xp_mode: bool = False,
    ) -> dict:
        from utils.summarize import generate_novel_settings_from_world_bible
        skills = self._select_skills(book_title, "continuation_analysis", json.dumps(world_data, ensure_ascii=False)[:1000])
        prompt = global_user_prompt
        if skills.text:
            prompt = (prompt + "\n\n" if prompt else "") + "【续写分析 Agent Skills】\n" + skills.text
        settings = generate_novel_settings_from_world_bible(
            self.client,
            world_data,
            model,
            global_user_prompt=prompt,
            xp_mode=xp_mode,
        )
        self._save_run(book_title, AgentContinuationRun(
            run_id=f"cont_analysis_{uuid.uuid4().hex}",
            task="continuation_analysis",
            book_title=book_title,
            selected_skills=skills.summaries,
            input_chars=len(json.dumps(world_data, ensure_ascii=False)),
            output_summary={"settings_fields": sorted(settings.keys())},
        ))
        return settings

    def suggest_directions(
        self,
        setting: str,
        plot: str,
        model: str,
        *,
        book_title: str = "",
        world_data: dict | None = None,
        global_user_prompt: str = "",
        xp_mode: bool = False,
        continuation_requirement: str = "",
        requested_plot: str = "",
    ) -> list[str]:
        world_summary = _build_world_summary(world_data)
        skills = self._select_skills(book_title, "continuation_direction", "\n".join([setting, plot, continuation_requirement, requested_plot])[:1000])
        prompt = _safe_format(
            SUGGESTION_PROMPT,
            world=world_summary[:DIRECTION_WORLD_MAX_CHARS],
            setting=setting[:DIRECTION_BACKGROUND_MAX_CHARS],
            plot=plot[:DIRECTION_PLOT_MAX_CHARS],
            requirement=(continuation_requirement or "（无）")[:DIRECTION_REQUIREMENT_MAX_CHARS],
            requested_plot=(requested_plot or "（无）")[:DIRECTION_PLOT_MAX_CHARS],
        )
        if skills.text:
            prompt += f"\n\n【续写方向 Agent Skills】\n{skills.text}"
        if global_user_prompt.strip():
            prompt += f"\n\n用户偏好参考: {global_user_prompt}"
        if xp_mode:
            prompt += f"\n\n{Prompts.XP_MODE_SYSTEM}\n\n{Prompts.XP_SUGGESTION_GUIDE}"
        response = self.client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
            temperature=0.8,
        )
        text = response.choices[0].message.content or ""
        directions = [line.strip() for line in text.split("\n") if line.strip() and ("方向" in line or "：" in line)]
        result = directions[:5] if directions else [text[:200]]
        self._save_run(book_title, AgentContinuationRun(
            run_id=f"cont_direction_{uuid.uuid4().hex}",
            task="continuation_direction",
            book_title=book_title,
            selected_skills=skills.summaries,
            input_chars=len(prompt),
            output_summary={"directions": len(result)},
        ))
        return result

    def _parse_sections(self, raw: str, source_text: str = "") -> list[tuple[str, str]]:
        payload = str(raw or "").strip()
        payload = re.sub(r"^```(?:json)?\s*", "", payload)
        payload = re.sub(r"\s*```$", "", payload)
        data = json.loads(payload)
        if not isinstance(data, list) or not data:
            raise ValueError("Agent 分段必须返回非空 JSON 数组")
        if not source_text:
            raise ValueError("缺少用于锚点校验的原文")
        return self._sections_from_anchors(data, source_text)

    def _sections_from_anchors(self, data: list, source: str) -> list[tuple[str, str]]:
        boundaries: list[tuple[int, str]] = []
        search_from = 0
        for index, item in enumerate(data):
            if not isinstance(item, dict) or set(item) != {"title", "start_quote"}:
                raise ValueError("每个分段只能包含 title 和 start_quote")
            title = item["title"]
            quote = item["start_quote"]
            if not isinstance(title, str) or not title.strip():
                raise ValueError("分段标题必须是非空字符串")
            title = title.strip()
            if not isinstance(quote, str):
                raise ValueError("start_quote 必须是字符串")
            if index == 0:
                if quote != "":
                    raise ValueError("第一段 start_quote 必须为空字符串")
                boundaries.append((0, title))
                continue
            if not self.MIN_ANCHOR_CHARS <= len(quote) <= self.MAX_ANCHOR_CHARS:
                raise ValueError(
                    f"start_quote 长度必须为 {self.MIN_ANCHOR_CHARS}-{self.MAX_ANCHOR_CHARS} 字符"
                )
            position = source.find(quote, search_from)
            if position < 0:
                raise ValueError(f"Agent 分段锚点无法在原文定位: {quote[:40]}")
            if position <= boundaries[-1][0]:
                raise ValueError("start_quote 必须严格递增且不得重复")
            boundaries.append((position, title))
            search_from = position + len(quote)
        return self._sections_from_boundaries(source, boundaries)

    @classmethod
    def _split_source_chunks(cls, source: str) -> list[str]:
        if len(source) <= cls.MAX_SEGMENT_CHARS:
            return [source]
        chunks: list[str] = []
        start, total = 0, len(source)
        while total - start > cls.MAX_SEGMENT_CHARS:
            end_limit = start + cls.MAX_SEGMENT_CHARS
            lower_bound = start + cls.MAX_SEGMENT_CHARS // 2
            tail = source[lower_bound:end_limit]
            paragraph_breaks = list(re.finditer(r"\n[ \t\r]*\n+", tail))
            if paragraph_breaks:
                end = lower_bound + paragraph_breaks[-1].end()
            else:
                line_break = source.rfind("\n", lower_bound, end_limit)
                end = line_break + 1 if line_break >= lower_bound else end_limit
            chunks.append(source[start:end])
            start = end
        chunks.append(source[start:])
        return chunks

    @staticmethod
    def _sections_from_boundaries(source: str, boundaries: list[tuple[int, str]]) -> list[tuple[str, str]]:
        ordered = sorted(boundaries, key=lambda item: item[0])
        if not ordered or ordered[0][0] != 0:
            raise ValueError("分段边界必须从原文开头开始")
        if len({position for position, _ in ordered}) != len(ordered):
            raise ValueError("分段边界不能重复")
        sections = []
        for index, (start, title) in enumerate(ordered):
            end = ordered[index + 1][0] if index + 1 < len(ordered) else len(source)
            if end <= start:
                raise ValueError("分段边界必须严格递增")
            sections.append((title, source[start:end]))
        return sections

    @classmethod
    def _split_text_locally_preserving_content(cls, source: str) -> list[tuple[str, str]]:
        if not source:
            return []
        headings = []
        for match in re.finditer(r"(?m)^#{1,6}[ \t]+([^\r\n]+)", source):
            title = match.group(1).strip()
            if match.start() == 0:
                headings = [(0, title)]
            elif title:
                headings.append((match.start(), title))
        if headings:
            if headings[0][0] != 0:
                headings.insert(0, (0, "前置内容"))
            return cls._sections_from_boundaries(source, headings)

        boundaries = [(0, "段落 1")]
        paragraph_breaks = list(re.finditer(r"\n[ \t\r]*\n+", source))
        if paragraph_breaks:
            for index, match in enumerate(paragraph_breaks, 2):
                if match.end() < len(source):
                    boundaries.append((match.end(), f"段落 {index}"))
            return cls._sections_from_boundaries(source, boundaries)

        line_breaks = list(re.finditer(r"\n", source))
        if line_breaks:
            for index, match in enumerate(line_breaks, 2):
                if match.end() < len(source):
                    boundaries.append((match.end(), f"段落 {index}"))
            if len(boundaries) > 1:
                return cls._sections_from_boundaries(source, boundaries)
        if len(source) <= cls.MAX_SEGMENT_CHARS:
            return [("全文", source)]
        boundaries = [(0, "片段 1")]
        for index, start in enumerate(range(cls.MAX_SEGMENT_CHARS, len(source), cls.MAX_SEGMENT_CHARS), 2):
            boundaries.append((start, f"片段 {index}"))
        return cls._sections_from_boundaries(source, boundaries)

    @staticmethod
    def _is_lossless(sections: list[tuple[str, str]], source: str) -> bool:
        return bool(sections) and "".join(content for _, content in sections) == source

    def _select_skills(self, book_title: str, task: str, query: str):
        from core.agent.repository import AgentRepository
        from core.agent.skills import SkillSelection, SkillService
        if not self.skills_enabled or not self.manager or not book_title:
            return SkillSelection()
        try:
            return SkillService(AgentRepository(self.manager.get_workspace(book_title))).select_for_task(
                task, "writing_orchestrator", query, max_skills=6
            )
        except Exception:
            return SkillSelection()

    def _save_run(self, book_title: str, run: AgentContinuationRun) -> None:
        if not self.manager or not book_title:
            return
        try:
            workspace = self.manager.get_workspace(book_title)
            workspace.storage.write_json(
                f"{workspace.agent_root}/continuation_runs/{run.run_id}.json",
                asdict(run),
            )
        except Exception:
            return
