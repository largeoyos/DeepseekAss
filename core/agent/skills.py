from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_SKILL_CHARS = 12000
SKILL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
FORBIDDEN_PATTERNS = [r"\bshell\b", r"\bsubprocess\b", r"忽略.*系统", r"绕过.*权限", r"读取.*api.?key", r"直接.*文件系统"]

HUMANIZER_ZH_STYLE_BRIEF = """
【humanizer-zh 风格硬约束】
- 正文避免 AI 腔、宣传腔、总结腔和金句式段尾；不要把普通事件拔高成象征、证明、时代意义。
- 尽量不用“不是……而是……”“不仅……而且……”等否定式排比；如必须表达对比，改成直接陈述动作、事实或感受。
- 少用“此外、关键、至关重要、复杂性、彰显、体现、标志着、充满活力、深刻、命运、宿命”等抽象套话。
- 描写要多样化：动作、感官、环境、对话、停顿、身体反应、物件细节交替使用，避免反复写眼神、沉默、空气、心头一震。
- 句长和段落节奏要有变化；允许短句，但不要连续堆三段式排比。
- 用具体场景承载情绪，不用旁白解释主题；信任读者能读懂。
""".strip()


def _skill(name: str, description: str, tasks: str, content: str, *, agents: str = "writing_orchestrator", keywords: str = "", priority: int = 50, version: str = "1") -> str:
    return f"""---
name: {name}
description: {description}
agents: {agents}
tasks: {tasks}
keywords: {keywords}
priority: {priority}
version: {version}
---

{content.strip()}
"""


BUILTIN_SKILL_TEXTS = {
    "chapter-planning": _skill("chapter-planning", "生成新章节前建立可执行的场景计划。", "chapter_generation,extra_generation,continuation_segmentation,continuation_analysis,continuation_direction", """
# 章节规划
1. 明确本章在主线中的唯一目标、开场承接点和结尾钩子。
2. 按场景拆分目标、冲突、信息释放、转折和结果。
3. 区分已发生事实、作者未来规划和本章拟发生内容。
4. 每个角色变化必须由本章可见选择或代价支撑。
5. 不为凑字数增加无功能场景。
""", keywords="新章节,下一章,章节规划,场景", priority=100),
    "chapter-continuation": _skill("chapter-continuation", "根据前文、角色状态和世界设定自然续写章节。", "chapter_generation,extra_generation,continuation_segmentation,continuation_analysis,continuation_direction", """
# 章节续写
1. 优先承接上一章未完成动作、情绪和现场状态。
2. 读取近期摘要与必要旧章，不把作者规划误作历史事实。
3. 相关世界书条目必须作为事实边界，不机械复述设定。
4. 正文直接开始叙事，不输出标题、提纲、解释或 Markdown。
5. 完成本章计划后留下自然推进点，不强行制造悬念。
""", keywords="续写,承接,下一章,正文", priority=95),
    "continuity-review": _skill("continuity-review", "检查人物、地点、时间线、能力和剧情事实连续性。", "chapter_generation,extra_generation,continuation_segmentation,continuation_analysis,continuation_direction,chapter_polish,continuity_review,chapter_supervision", """
# 连续性审查
- 核对人物当前位置、身体状态、心理状态、目标、持有物和关系。
- 核对时间先后、移动距离、信息知情范围和能力边界。
- 旧事实被新事实替代时保留来源关系，不让冲突版本同时生效。
- 不允许仅为文采改变事实、事件顺序或人物动机。
""", agents="writing_orchestrator,continuity_editor,chapter_supervisor", keywords="连续性,人物状态,时间线,事实,世界观", priority=90),
    "character-arc": _skill("character-arc", "检查角色目标、阻力、选择、代价和状态变化。", "chapter_generation,extra_generation,continuation_segmentation,continuation_analysis,continuation_direction", """
# 角色弧线
对每个核心角色记录：进入场景时的目标、遭遇的阻力、作出的选择、承担的代价以及离开场景后的状态。变化必须由正文事件造成，不能用旁白宣告替代行动和对话。
""", keywords="角色,人物,成长,弧线,关系", priority=75),
    "foreshadowing": _skill("foreshadowing", "管理伏笔的埋设、推进、回收和来源。", "chapter_generation,extra_generation,continuation_segmentation,continuation_analysis,continuation_direction,world_maintenance,world_bible_management", """
# 伏笔管理
- 新伏笔必须有可识别载体、预期作用和来源章节。
- 推进伏笔时增加信息但避免提前解释答案。
- 回收伏笔必须与既有线索对应。
- 已回收伏笔标记 resolved，不再作为待处理伏笔注入。
""", agents="writing_orchestrator,continuity_editor,world_bible_manager", keywords="伏笔,线索,悬念,回收", priority=70),
    "scene-pacing": _skill("scene-pacing", "按场景功能、冲突升级和信息释放控制节奏。", "chapter_generation,extra_generation,continuation_segmentation,continuation_analysis,continuation_direction,chapter_polish,chapter_supervision", """
# 场景节奏
- 每个场景只承担少量清晰功能。
- 动作、对话、感官和内心描写随冲突强度变化。
- 删除重复解释和无推进对话。
- 重要转折前给足因果铺垫，转折后展示即时影响。
""", keywords="节奏,场景,拖沓,紧张,信息释放", priority=65),
    "chapter-polish": _skill("chapter-polish", "在不改变剧情事实的前提下润色完整章节。", "chapter_polish", """
# 章节润色
1. 原文是最高优先级依据。
2. 只调整措辞、句式、段落衔接、节奏、感官细节和表达准确性。
3. 不新增或删除事件，不改变事件顺序、人物行为、动机、关系或对白意图。
4. 保留专有名词、数字、地点、物品、能力和信息知情边界。
5. 若用户要求改变剧情，停止润色并建议使用重写。
""", keywords="润色,文笔,句式,表达,节奏", priority=100),
    "polish-fidelity": _skill("polish-fidelity", "审查润色稿是否忠实保留原文事实与对白意图。", "chapter_polish", """
# 润色保真
逐项比较原文和润色稿：事件集合、事件顺序、人物行动、动机、地点、时间、关系、物品、能力、关键对白意图和信息知情范围。任何漂移均为硬性失败；修复只能恢复原文事实，不能借机重写剧情。
""", keywords="保真,审查,事实漂移,对白意图", priority=95),
    "world-bible-maintenance": _skill("world-bible-maintenance", "按章节来源维护世界书状态和安全归档。", "world_maintenance,world_bible_management", """
# 世界书维护
- 稳定设定与章节后即时状态分开处理。
- 新状态通过来源和 supersedes 替代旧状态。
- 已完成剧情线和伏笔标记 resolved；暂不活跃标记 dormant。
- resident、人工修改和 manual override 条目禁止自动归档。
- 有效历史数据只隐藏归档，不物理删除。
""", agents="continuity_editor,project_maintainer,writing_orchestrator,world_bible_manager", keywords="世界书,归档,剧情线,角色状态", priority=90),
    "humanizer-zh": _skill("humanizer-zh", "去除 AI 写作痕迹，使中文小说表达更自然、具体、有人的节奏。", "chapter_generation,extra_generation,chapter_polish,polish_fidelity,chapter_supervision,continuation_segmentation,continuation_analysis,continuation_direction,writing_advice", """
# humanizer-zh
1. 去掉 AI 腔：少用抽象拔高、宣传腔、总结腔、金句式段尾。
2. 尽量不用“不是……而是……”“不仅……而且……”这类否定式排比；需要对比时直接写事实、动作或感受。
3. 减少套话：此外、关键、至关重要、复杂性、彰显、体现、标志着、充满活力、深刻、不可磨灭等。
4. 描写多样化：动作、感官、环境、物件、对话、停顿、身体反应交替使用，不要反复写眼神、沉默、空气、心头一震。
5. 句子长短混合，段落结尾不要总是升华主题。
6. 用具体场景承载情绪和主题，不用旁白替读者解释。
7. 保留原意、事实和人物行为；去 AI 腔不能改剧情。
""", agents="writing_orchestrator,chapter_supervisor,writing_advisor", keywords="去AI腔,人味,自然,润色,文风,描写,不是而是,不仅而且", priority=98),    "long-context": _skill("long-context", "长篇上下文筛选与压缩时保护关键事实。", "chapter_generation,extra_generation,continuation_segmentation,continuation_analysis,continuation_direction,chapter_polish,context_compaction,chapter_supervision,writing_advice", """
# 长篇上下文
优先保留系统约束、当前任务、上一章承接、近期摘要、当前角色状态、未完成剧情线、待回收伏笔和用户明确引用。压缩旧内容时保留已确认事实、用户决策、已执行操作、未完成事项和禁止遗失约束。
""", agents="writing_orchestrator,project_maintainer,chapter_supervisor,writing_advisor", keywords="长篇,上下文,压缩,历史剧情", priority=60),
}


@dataclass
class SkillDocument:
    skill_id: str
    name: str
    description: str
    agent_kinds: list[str]
    tasks: list[str]
    keywords: list[str]
    content: str
    scope: str
    priority: int = 50
    version: str = "1"

    def summary(self, reason: str = "") -> dict:
        return {"id": self.skill_id, "name": self.name, "description": self.description, "scope": self.scope, "version": self.version, "priority": self.priority, "reason": reason}


@dataclass
class SkillSelection:
    documents: list[SkillDocument] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n\n".join(f"## Skill: {doc.name} (v{doc.version})\n{doc.content}" for doc in self.documents)

    @property
    def summaries(self) -> list[dict]:
        return [doc.summary(self.reasons.get(doc.skill_id, "")) for doc in self.documents]


class SkillValidationError(ValueError):
    pass


class SkillService:
    """Nova-style scoped SKILL.md loader with deterministic task selection."""

    def __init__(self, repository) -> None:
        self.repository = repository

    def parse(self, skill_id: str, text: str, scope: str) -> SkillDocument:
        if not SKILL_NAME_PATTERN.match(skill_id):
            raise SkillValidationError("Skill ID 格式无效")
        if len(text) > MAX_SKILL_CHARS or "\x00" in text:
            raise SkillValidationError("Skill 内容非法或超限")
        header, content = self._frontmatter(text)
        name = header.get("name", skill_id).strip()
        if name != skill_id or not SKILL_NAME_PATTERN.match(name):
            raise SkillValidationError("Skill name 必须与 ID 一致")
        description = header.get("description", "").strip()
        if not description:
            raise SkillValidationError("Skill 缺少 description")
        if any(re.search(pattern, content, re.IGNORECASE) for pattern in FORBIDDEN_PATTERNS):
            raise SkillValidationError("Skill 包含越权或不安全指令")
        return SkillDocument(skill_id, name, description, self._csv(header.get("agents") or header.get("agent", "")), self._csv(header.get("tasks", "")), self._csv(header.get("keywords", "")), content.strip(), scope, self._priority(header.get("priority", "50")), header.get("version", "1").strip() or "1")

    def active_documents(self) -> list[SkillDocument]:
        active: dict[str, SkillDocument] = {}
        for skill_id, text in BUILTIN_SKILL_TEXTS.items():
            active[skill_id] = self.parse(skill_id, text, "builtin")
        for skill_id, text in self.repository.list_skill_texts("book"):
            try:
                active[skill_id] = self.parse(skill_id, text, "book")
            except SkillValidationError:
                continue
        return sorted(active.values(), key=lambda item: (-item.priority, item.skill_id))

    def select_for_task(self, task: str, agent_kind: str, query: str = "", *, max_skills: int = 6) -> SkillSelection:
        query_lower = str(query or "").lower()
        selected: list[tuple[SkillDocument, str, int]] = []
        for doc in self.active_documents():
            if doc.agent_kinds and agent_kind not in doc.agent_kinds and "all" not in doc.agent_kinds and "*" not in doc.agent_kinds:
                continue
            task_match = not doc.tasks or task in doc.tasks or "*" in doc.tasks
            keyword_hits = [item for item in doc.keywords if item.lower() in query_lower]
            if not task_match and not keyword_hits:
                continue
            reason = f"任务匹配：{task}" if task_match else ""
            if keyword_hits:
                reason += ("；" if reason else "") + "关键词命中：" + "、".join(keyword_hits[:5])
            score = doc.priority + (100 if task_match else 0) + len(keyword_hits) * 10
            selected.append((doc, reason, score))
        selected.sort(key=lambda item: (-item[2], item[0].skill_id))
        docs = [item[0] for item in selected[:max(1, max_skills)]]
        return SkillSelection(docs, {item[0].skill_id: item[1] for item in selected})

    def render_for_agent(self, agent_kind: str) -> str:
        docs = [doc for doc in self.active_documents() if not doc.agent_kinds or agent_kind in doc.agent_kinds or "all" in doc.agent_kinds or "*" in doc.agent_kinds]
        return SkillSelection(docs).text

    @staticmethod
    def _frontmatter(text: str) -> tuple[dict[str, str], str]:
        value = text.strip()
        if not value.startswith("---"):
            raise SkillValidationError("Skill 必须包含 SKILL.md frontmatter")
        parts = value.split("---", 2)
        if len(parts) != 3:
            raise SkillValidationError("Skill frontmatter 未闭合")
        header: dict[str, str] = {}
        for line in parts[1].splitlines():
            if ":" in line:
                key, raw = line.split(":", 1)
                header[key.strip().lower()] = raw.strip().strip("\"'")
        return header, parts[2].strip()

    @staticmethod
    def _csv(value: str) -> list[str]:
        return [item.strip() for item in re.split(r"[,;，；]", str(value or "")) if item.strip()]

    @staticmethod
    def _priority(value: str) -> int:
        try:
            return max(0, min(100, int(value)))
        except (TypeError, ValueError):
            return 50
