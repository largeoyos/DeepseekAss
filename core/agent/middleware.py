from __future__ import annotations


class AgentMiddleware:
    """Extension points cannot grant tools or permissions."""

    def before_run(self, run, request, context_report) -> None:
        return None

    def before_turn(self, run) -> None:
        return None

    def after_tool(self, run, request, result) -> None:
        return None

    def after_run(self, run) -> None:
        return None


class SafetyMiddleware(AgentMiddleware):
    def before_turn(self, run) -> None:
        if run.iteration > 50:
            raise RuntimeError("Agent 迭代超过安全上限")

    def after_tool(self, run, request, result) -> None:
        if result.error_code == "tool_not_allowed":
            run.terminal_reason = "permission_violation"
