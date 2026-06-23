from __future__ import annotations

import re
from dataclasses import dataclass

MAX_SKILL_CHARS = 12000
FORBIDDEN_PATTERNS = [r"\bshell\b", r"\bsubprocess\b", r"忽略.*系统", r"绕过.*权限", r"读取.*api.?key"]
BUILTIN_SKILLS = {
    "chapter_planning": ("writing_orchestrator", "先明确本章目标、冲突、转折、角色变化和结尾钩子，再生成正文。"),
    "continuity_review": ("continuity_editor", "逐项核对人物状态、地点、时间、能力边界、剧情线和伏笔。"),
    "character_arc": ("writing_orchestrator", "检查角色的目标、阻力、选择、代价和本章后的状态变化。"),
    "foreshadowing": ("continuity_editor", "记录新埋伏笔、推进状态、回收条件和来源章节。"),
    "scene_pacing": ("writing_orchestrator", "按场景目标、冲突升级、信息释放和节奏变化审查章节。"),
    "world_bible_cleanup": ("project_maintainer", "识别重复、缺失 ID、孤立引用和矛盾世界书条目。"),
    "long_context": ("project_maintainer", "压缩时保留事实、决策、已执行动作、未完成事项和硬约束。"),
}


@dataclass
class SkillDocument:
    skill_id: str
    name: str
    agent_kinds: list[str]
    content: str
    scope: str


class SkillValidationError(ValueError):
    pass


class SkillService:
    def __init__(self, repository) -> None:
        self.repository = repository

    def parse(self, skill_id: str, text: str, scope: str) -> SkillDocument:
        if len(text) > MAX_SKILL_CHARS or "\x00" in text:
            raise SkillValidationError("Skill 内容非法或超限")
        header, content = {}, text
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) == 3:
                content = parts[2].strip()
                for line in parts[1].splitlines():
                    if ":" in line:
                        key, value = line.split(":", 1)
                        header[key.strip()] = value.strip()
        if any(re.search(pattern, content, re.IGNORECASE) for pattern in FORBIDDEN_PATTERNS):
            raise SkillValidationError("Skill 包含越权或不安全指令")
        kinds = [item.strip() for item in header.get("agents", "").split(",") if item.strip()]
        return SkillDocument(skill_id, header.get("name", skill_id), kinds, content, scope)

    def render_for_agent(self, agent_kind: str) -> str:
        docs = [SkillDocument(skill_id, skill_id, [kind], content, "builtin") for skill_id, (kind, content) in BUILTIN_SKILLS.items() if kind == agent_kind]
        for skill_id, text in self.repository.list_skill_texts("book"):
            try:
                doc = self.parse(skill_id, text, "book")
            except SkillValidationError:
                continue
            if not doc.agent_kinds or agent_kind in doc.agent_kinds:
                docs.append(doc)
        return "\n\n".join(f"### {doc.name}\n{doc.content}" for doc in docs)
