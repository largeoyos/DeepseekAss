from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field
from typing import Callable

from core.agent.types import ToolCallRequest, ToolResult

PERMISSION_RANK = {"read_only": 0, "draft_write": 1, "confirmed_write": 2}


class AgentToolError(RuntimeError):
    pass


class AgentToolPermissionError(PermissionError):
    pass


@dataclass
class ToolContext:
    run_id: str
    book_id: str
    book_title: str
    agent_kind: str
    permission_level: str
    repository: object
    services: dict = field(default_factory=dict)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict
    handler: Callable[[ToolContext, dict], object]
    required_permission: str = "read_only"
    read_only: bool = True
    produces_change_set: bool = False
    allowed_agents: list[str] = field(default_factory=list)
    max_result_chars: int = 12000
    timeout_seconds: float = 30.0
    version: int = 1

    def openai_schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"工具重复注册: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise AgentToolError(f"未知工具: {name}") from exc

    def schemas_for(self, allowed_tools: list[str]) -> list[dict]:
        return [self._tools[name].openai_schema() for name in allowed_tools if name in self._tools]
    def specs_for(self, allowed_tools: list[str]) -> list[ToolSpec]:
        return [self._tools[name] for name in allowed_tools if name in self._tools]

    def execute(self, request: ToolCallRequest, context: ToolContext, allowed_tools: list[str]) -> ToolResult:
        if request.tool_name not in allowed_tools:
            return ToolResult(request.call_id, False, content=f"Agent 无权调用工具 {request.tool_name}", error_code="tool_not_allowed")
        try:
            spec = self.get(request.tool_name)
            if spec.allowed_agents and context.agent_kind not in spec.allowed_agents:
                raise AgentToolPermissionError("当前 Agent 类型无权调用此工具")
            if PERMISSION_RANK[context.permission_level] < PERMISSION_RANK[spec.required_permission]:
                raise AgentToolPermissionError("当前 Agent 权限不足")
            self._validate_arguments(spec.parameters, request.arguments)
            if spec.timeout_seconds and spec.timeout_seconds > 0:
                executor = ThreadPoolExecutor(max_workers=1)
                future = executor.submit(spec.handler, context, request.arguments)
                try:
                    value = future.result(timeout=spec.timeout_seconds)
                except TimeoutError as exc:
                    future.cancel()
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise AgentToolError(f"Tool timed out: {spec.name}") from exc
                else:
                    executor.shutdown(wait=True)
            else:
                value = spec.handler(context, request.arguments)
            if isinstance(value, str):
                content, structured = value, {}
            else:
                structured = value if isinstance(value, dict) else {"result": value}
                content = json.dumps(structured, ensure_ascii=False, default=str)
            artifact_id = None
            if len(content) > spec.max_result_chars:
                artifact_id = context.repository.save_artifact(context.run_id, "tool_result", content, {"tool_name": spec.name})
                content = content[:spec.max_result_chars] + f"\n\n[结果已截断，完整内容保存在 Artifact {artifact_id}]"
            return ToolResult(request.call_id, True, content, structured, artifact_id=artifact_id)
        except AgentToolPermissionError as exc:
            return ToolResult(request.call_id, False, content=str(exc), error_code="permission_denied")
        except Exception as exc:
            return ToolResult(request.call_id, False, content=str(exc), error_code="tool_failed")

    @staticmethod
    def _validate_arguments(schema: dict, arguments: dict) -> None:
        if not isinstance(arguments, dict):
            raise AgentToolError("工具参数必须是对象")
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in arguments:
                raise AgentToolError(f"缺少工具参数: {name}")
        expected_types = {"string": str, "integer": int, "array": list, "object": dict, "boolean": bool}
        for name, value in arguments.items():
            expected = properties.get(name, {}).get("type")
            if expected in expected_types and not isinstance(value, expected_types[expected]):
                raise AgentToolError(f"参数 {name} 类型错误")
