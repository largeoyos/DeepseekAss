from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field

from core.agent.domain_tools import build_domain_tool_registry
from core.agent.repository import AgentRepository
from core.agent.skills import SkillSelection, SkillService
from core.agent.tools import ToolContext
from core.agent.types import ToolCallRequest, now_iso


@dataclass
class SupervisionRequest:
    book_title: str
    chapter_num: int
    chapter_title: str
    chapter_content: str
    chapter_outline: str
    requirements: str
    continuity_context: str
    target_words: int
    model: str
    global_prompt: str = ""
    xp_mode: bool = False
    style_audit: str = ""
    content_lock: str = ""
    max_repair_rounds: int = 2
    style_profile_metrics: dict = field(default_factory=dict)
    style_profile_name: str = ""


@dataclass
class SupervisionResult:
    run_id: str
    content: str
    report: dict
    selected_skills: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)


class AgentSupervisionService:
    """Bounded supervision Agent with read-only domain tools and two repair rounds."""

    def __init__(self, novel_manager, client_factory, *, skills_enabled: bool = True) -> None:
        self.manager = novel_manager
        self.client_factory = client_factory
        self.skills_enabled = skills_enabled

    def supervise(
        self,
        request: SupervisionRequest,
        progress=None,
        repair_change_callback=None,
    ) -> SupervisionResult:
        workspace = self.manager.get_workspace(request.book_title)
        manifest = workspace.ensure_manifest()
        repository = AgentRepository(workspace)
        run_id = f"supervision_{uuid.uuid4().hex}"
        registry = build_domain_tool_registry(self.manager)
        context = ToolContext(
            run_id=run_id,
            book_id=manifest.book_id,
            book_title=request.book_title,
            agent_kind="chapter_supervisor",
            permission_level="draft_write",
            repository=repository,
        )
        tool_calls = []
        evidence = []
        calls = [
            ("project.author_plan", {}),
            ("project.active_state", {}),
            ("chapter.read_range", {"center_chapter": request.chapter_num, "before": 2, "after": 0}),
            ("world_bible.search", {"query": f"{request.chapter_title} {request.chapter_outline}".strip(), "limit": 12}),
        ]
        allowed = [name for name, _args in calls]
        for index, (name, arguments) in enumerate(calls, 1):
            result = registry.execute(ToolCallRequest(f"{run_id}_{index}", name, arguments), context, allowed)
            item = {
                "tool_name": name,
                "arguments": arguments,
                "success": result.success,
                "error_code": result.error_code,
                "content_chars": len(result.content or ""),
                "artifact_id": result.artifact_id,
            }
            tool_calls.append(item)
            if result.success and result.content:
                evidence.append(f"【{name}】\n{result.content[:6000]}")
        skills = self._select_skills(repository, request)
        combined_context = "\n\n".join(filter(None, [
            request.continuity_context,
            "【监督 Agent 只读工具证据】\n" + "\n\n".join(evidence) if evidence else "",
            "【监督 Agent Skills】\n" + skills.text if skills.text else "",
        ]))

        from utils.supervision import supervise_chapter
        final_content, result = supervise_chapter(
            self.client_factory,
            chapter_content=request.chapter_content,
            chapter_title=f"Chapter {request.chapter_num}: {request.chapter_title}",
            chapter_outline=request.chapter_outline,
            requirements=request.requirements,
            continuity_context=combined_context,
            target_words=request.target_words,
            model=request.model,
            temperature=0.5,
            global_user_prompt=request.global_prompt,
            xp_mode=request.xp_mode,
            style_audit=request.style_audit,
            content_lock=request.content_lock,
            style_profile_metrics=request.style_profile_metrics,
            style_profile_name=request.style_profile_name,
            max_repair_rounds=max(0, min(2, int(request.max_repair_rounds))),
            progress=progress,
            repair_change_callback=repair_change_callback,
        )
        report = result.to_dict()
        report.update({
            "schema_version": 1,
            "run_id": run_id,
            "agent_kind": "chapter_supervisor",
            "selected_skills": skills.summaries,
            "tool_calls": tool_calls,
            "created_at": now_iso(),
        })
        workspace.storage.write_json(
            f"{workspace.agent_root}/supervision_runs/{run_id}.json",
            {
                "schema_version": 1,
                "run_id": run_id,
                "agent_kind": "chapter_supervisor",
                "request": {**asdict(request), "chapter_content": "[正文已单独加密保存]"},
                "report": report,
                "selected_skills": skills.summaries,
                "tool_calls": tool_calls,
                "created_at": now_iso(),
            },
        )
        repository.save_artifact(run_id, "supervised_chapter", final_content, {"chapter_num": request.chapter_num})
        return SupervisionResult(run_id, final_content, report, skills.summaries, tool_calls)

    def _select_skills(self, repository: AgentRepository, request: SupervisionRequest) -> SkillSelection:
        if not self.skills_enabled:
            return SkillSelection()
        return SkillService(repository).select_for_task(
            "chapter_supervision",
            "chapter_supervisor",
            "\n".join([request.chapter_title, request.chapter_outline, request.requirements]),
        )
