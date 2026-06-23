from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field

from core.agent.types import now_iso
from ui.continuation_dialogs import _build_world_summary, _safe_format, SUGGESTION_PROMPT
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


class AgentContinuationService:
    """Deterministic Agent facade for continuation import analysis, segmentation and direction planning."""

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
        text = (text or "").strip()
        if not text:
            return []
        skills = self._select_skills(book_title, "continuation_segmentation", text[:1000])
        prompt = (
            "你是续写导入分段 Agent。请把原文切分为适合导入章节树的连续段落。\n"
            "规则：\n"
            "1. 保持原文顺序，不改写正文。\n"
            "2. 每段必须有简短标题。\n"
            "3. 不要丢失任何正文内容。\n"
            "4. 只输出 JSON 数组，格式：[{\"title\":\"...\",\"content\":\"...\"}]。\n"
            f"\n【可用写作 Skills】\n{skills.text}\n"
            f"\n【用户偏好】\n{global_user_prompt}\n"
            f"\n【原文】\n{text[:50000]}"
        )
        response = self.client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=6000,
        )
        raw = response.choices[0].message.content or ""
        sections = self._parse_sections(raw)
        if not sections:
            raise ValueError("Agent 分段返回为空或格式无效")
        self._save_run(book_title, AgentContinuationRun(
            run_id=f"cont_seg_{uuid.uuid4().hex}",
            task="continuation_segmentation",
            book_title=book_title,
            selected_skills=skills.summaries,
            input_chars=len(text),
            output_summary={"segments": len(sections)},
        ))
        return sections

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
    ) -> list[str]:
        world_summary = _build_world_summary(world_data)
        skills = self._select_skills(book_title, "continuation_direction", "\n".join([setting, plot])[:1000])
        prompt = _safe_format(
            SUGGESTION_PROMPT,
            world=world_summary[:2500],
            setting=setting[:1800],
            plot=plot[:1800],
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

    def _parse_sections(self, raw: str) -> list[tuple[str, str]]:
        text = (raw or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("Agent 分段必须返回 JSON 数组")
        result = []
        for idx, item in enumerate(data, 1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or f"分段 {idx}").strip()
            content = str(item.get("content") or "").strip()
            if content:
                result.append((title, content))
        return result

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