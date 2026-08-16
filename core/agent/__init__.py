"""Controlled, encrypted Agent runtime for DeepseekAss.

The package exports remain source compatible, while loading each implementation
only when requested.  This keeps the standalone control process independent from
the model/GUI stacks that it never uses.
"""

from importlib import import_module
from typing import Any

__all__ = [
    "AGENT_PROFILES", "AgentEvent", "AgentProfile", "AgentRunRequest", "AgentRuntime",
    "AgentChapterGenerationService", "AgentChapterPlan", "AgentChapterRequest", "AgentChapterResult",
    "AgentExtraGenerationService", "AgentExtraPlan", "AgentExtraRequest", "AgentExtraResult",
    "AgentContinuationService",
    "WorldBibleMaintenanceService", "WorldMaintenanceResult",
    "AdvisorRequest", "AdvisorResult", "WritingAdvisorService",
    "AgentSupervisionService", "SupervisionRequest", "SupervisionResult",
    "WorldBibleAgentService", "WorldChangePlan", "WorldDetailRequest",
    "ChangeOperation", "ChangeSet", "ToolCallRequest", "ToolResult", "get_agent_profile",
]

_EXPORT_MODULES = {
    "AGENT_PROFILES": "core.agent.profiles",
    "get_agent_profile": "core.agent.profiles",
    "AgentRuntime": "core.agent.runtime",
    "AgentChapterGenerationService": "core.agent.chapter_generation",
    "AgentChapterPlan": "core.agent.chapter_generation",
    "AgentChapterRequest": "core.agent.chapter_generation",
    "AgentChapterResult": "core.agent.chapter_generation",
    "AgentExtraGenerationService": "core.agent.extra_generation",
    "AgentExtraPlan": "core.agent.extra_generation",
    "AgentExtraRequest": "core.agent.extra_generation",
    "AgentExtraResult": "core.agent.extra_generation",
    "AgentContinuationService": "core.agent.continuation",
    "WorldBibleMaintenanceService": "core.agent.world_maintenance",
    "WorldMaintenanceResult": "core.agent.world_maintenance",
    "AdvisorRequest": "core.agent.advisor",
    "AdvisorResult": "core.agent.advisor",
    "WritingAdvisorService": "core.agent.advisor",
    "AgentSupervisionService": "core.agent.supervision_agent",
    "SupervisionRequest": "core.agent.supervision_agent",
    "SupervisionResult": "core.agent.supervision_agent",
    "WorldBibleAgentService": "core.agent.world_bible_agent",
    "WorldChangePlan": "core.agent.world_bible_agent",
    "WorldDetailRequest": "core.agent.world_bible_agent",
    "AgentEvent": "core.agent.types",
    "AgentProfile": "core.agent.types",
    "AgentRunRequest": "core.agent.types",
    "ChangeOperation": "core.agent.types",
    "ChangeSet": "core.agent.types",
    "ToolCallRequest": "core.agent.types",
    "ToolResult": "core.agent.types",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
