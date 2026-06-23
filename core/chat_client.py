"""
DeepSeek 聊天客户端 - 核心逻辑
负责 API 调用、对话管理、模型切换、参数控制，与具体模式（策略）解耦
"""
from datetime import datetime
from typing import Generator

from openai import OpenAI

from config import Config
from strategies.base_strategy import BaseStrategy


class DeepSeekChatClient:
    """
    DeepSeek 聊天客户端

    职责：
    - 管理与 DeepSeek API 的连接
    - 维护对话上下文（消息列表）
    - 根据当前策略生成回复
    - 支持流式输出
    - 支持运行时调整 temperature / top_p / max_tokens / frequency_penalty
    - 不与任何具体模式耦合（依赖抽象 BaseStrategy）
    """

    def __init__(self, strategy: BaseStrategy, model: str | None = None):
        """
        初始化客户端

        Args:
            strategy: 当前使用的聊天模式策略
            model: 模型名称（若不指定则使用策略推荐的模型）
        """
        Config.validate()

        self._client = OpenAI(
            api_key=Config.API_KEY,
            base_url=Config.BASE_URL,
        )
        self._strategy = strategy
        self._model = model or strategy.recommended_model
        self._temperature = strategy.recommended_temperature
        self._top_p = strategy.recommended_top_p
        self._max_tokens = strategy.recommended_max_tokens
        self._frequency_penalty = strategy.recommended_frequency_penalty
        self._messages: list[dict] = []
        self._global_user_prompt: str = ""
        self._last_usage: dict | None = None

        # 取消机制
        self._cancel_requested = False
        self._current_stream = None

        # 初始化系统提示词
        self._reset_conversation()

    # ========== 公开属性 ==========

    @property
    def strategy(self) -> BaseStrategy:
        return self._strategy

    @property
    def raw_client(self):
        """暴露底层 OpenAI 客户端，供 NovelManager 等组件使用"""
        return self._client

    @property
    def model(self) -> str:
        return self._model

    @property
    def temperature(self) -> float:
        return self._temperature

    @property
    def top_p(self) -> float:
        return self._top_p

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    @property
    def frequency_penalty(self) -> float:
        return self._frequency_penalty

    @property
    def recommended_temperature(self) -> float:
        """返回当前策略推荐的温度值"""
        return self._strategy.recommended_temperature

    @property
    def recommended_top_p(self) -> float:
        """返回当前策略推荐的 top_p 值"""
        return self._strategy.recommended_top_p

    @property
    def recommended_max_tokens(self) -> int:
        """返回当前策略推荐的 max_tokens 值"""
        return self._strategy.recommended_max_tokens

    @property
    def recommended_frequency_penalty(self) -> float:
        """返回当前策略推荐的 frequency_penalty 值"""
        return self._strategy.recommended_frequency_penalty

    @property
    def global_user_prompt(self) -> str:
        """返回用户全局提示词（写作偏好）"""
        return self._global_user_prompt

    @global_user_prompt.setter
    def global_user_prompt(self, value: str) -> None:
        self._global_user_prompt = value

    @property
    def messages(self) -> list[dict]:
        """返回当前对话历史副本"""
        return list(self._messages)

    @property
    def last_usage(self) -> dict | None:
        """Return usage from the most recent completed API call, if provided."""
        return dict(self._last_usage) if self._last_usage else None

    # ========== 模式切换 ==========

    def switch_strategy(self, strategy: BaseStrategy, model: str | None = None) -> None:
        """
        切换聊天模式（运行时动态切换策略）

        Args:
            strategy: 新的策略对象
            model: 可选，切换时同时更换模型
        """
        self._strategy = strategy
        if model:
            self._model = model
        else:
            self._model = strategy.recommended_model
        self._temperature = strategy.recommended_temperature
        self._top_p = strategy.recommended_top_p
        self._max_tokens = strategy.recommended_max_tokens
        self._frequency_penalty = strategy.recommended_frequency_penalty
        self._reset_conversation()

    def switch_model(self, model: str) -> None:
        """
        仅切换模型，保留当前策略和对话上下文

        Args:
            model: 模型名称
        """
        known = {
            Config.MODEL_V4_FLASH,
            Config.MODEL_V4_PRO,
        }
        if model not in known:
            print(f"[警告] 未知模型 '{model}'，将尝试使用，但可能出错。")
        self._model = model

    def reconfigure_connection(self, api_key: str, base_url: str, model: str | None = None) -> None:
        """Rebuild the OpenAI-compatible connection without clearing the conversation."""
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        if model:
            self._model = model

    # ========== 取消机制 ==========

    def cancel(self) -> None:
        """请求取消当前操作。关闭 HTTP 流并设置取消标志。"""
        self._cancel_requested = True
        if self._current_stream is not None:
            self._current_stream.close()
            self._current_stream = None

    def reset_cancel(self) -> None:
        """每次新任务开始前重置取消标志。"""
        self._cancel_requested = False

    # ========== 运行时参数设置 ==========

    def set_temperature(self, temperature: float) -> None:
        """设置生成温度 (0.0 ~ 2.0)"""
        self._temperature = max(0.0, min(2.0, temperature))

    def set_top_p(self, top_p: float) -> None:
        """设置 top_p (0.0 ~ 1.0)"""
        self._top_p = max(0.0, min(1.0, top_p))

    def set_max_tokens(self, max_tokens: int) -> None:
        """设置最大生成 token 数 (≥ 1)"""
        self._max_tokens = max(1, max_tokens)

    def set_frequency_penalty(self, penalty: float) -> None:
        """设置 frequency_penalty (-2.0 ~ 2.0)"""
        self._frequency_penalty = max(-2.0, min(2.0, penalty))

    def clear_context(self, keep_system: bool = True) -> None:
        """
        清除对话上下文
        Args:
            keep_system: 是否保留 System Prompt（默认保留）
        """
        if keep_system:
            self._messages = [self._messages[0]] if self._messages else []
        else:
            self._messages = []

    # ========== 核心 API 调用 ==========

    @staticmethod
    def _now_ts() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _usage_to_dict(usage) -> dict | None:
        if usage is None:
            return None
        if isinstance(usage, dict):
            return usage
        if hasattr(usage, "model_dump"):
            return usage.model_dump()
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }

    def _build_api_kwargs(self, stream: bool = False, include_usage: bool = False) -> dict:
        """构造 API 调用参数字典"""
        kwargs = {
            "model": self._model,
            "messages": self._messages,
            "temperature": self._temperature,
            "top_p": self._top_p,
            "max_tokens": self._max_tokens,
            "frequency_penalty": self._frequency_penalty,
            "stream": stream,
        }
        if stream and include_usage:
            kwargs["stream_options"] = {"include_usage": True}
        return kwargs

    def chat(self, user_input: str) -> str:
        """
        发送消息并获取完整回复（非流式）

        Args:
            user_input: 用户输入文本

        Returns:
            模型的完整回复文本
        """
        self._last_usage = None
        self._messages.append({"role": "user", "content": user_input, "timestamp": self._now_ts()})

        try:
            response = self._client.chat.completions.create(
                **self._build_api_kwargs(stream=False),
            )
            if self._cancel_requested:
                self._messages.pop()
                return "[用户取消了操作]"

            assistant_content = response.choices[0].message.content
            if assistant_content is None:
                assistant_content = "[模型返回了空回复]"
            self._last_usage = self._usage_to_dict(getattr(response, "usage", None))
            self._messages.append({"role": "assistant", "content": assistant_content, "timestamp": self._now_ts()})
            return assistant_content
        except Exception as e:
            self._messages.pop()
            raise RuntimeError(f"API 调用失败: {e}") from e

    def chat_stream(self, user_input: str) -> Generator[str, None, None]:
        """
        发送消息并以流式获取回复（逐 token 产出）

        Args:
            user_input: 用户输入文本

        Yields:
            每次产出一个 token 字符串
        """
        self._last_usage = None
        self._messages.append({"role": "user", "content": user_input, "timestamp": self._now_ts()})

        try:
            try:
                stream = self._client.chat.completions.create(
                    **self._build_api_kwargs(stream=True, include_usage=True),
                )
            except Exception:
                stream = self._client.chat.completions.create(
                    **self._build_api_kwargs(stream=True),
                )
            self._current_stream = stream

            full_reply: list[str] = []
            for chunk in stream:
                if self._cancel_requested:
                    break
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    self._last_usage = self._usage_to_dict(usage)
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    full_reply.append(delta.content)
                    yield delta.content

            if self._cancel_requested:
                self._messages.pop()  # 取消时不保留部分回复
            else:
                # 流式结束后将完整回复写入对话历史
                self._messages.append({
                    "role": "assistant",
                    "content": "".join(full_reply),
                    "timestamp": self._now_ts(),
                })

        except Exception as e:
            self._messages.pop()
            raise RuntimeError(f"API 流式调用失败: {e}") from e
        finally:
            self._current_stream = None

    # ========== 消息导入/导出 ==========

    def import_messages(self, messages: list[dict]) -> None:
        """
        导入外部消息列表（如从历史记录加载），替换当前对话

        会自动确保第一条消息为 system 角色（若外部消息不包含 system 则保留当前 system prompt）。
        导入后 system prompt 使用当前策略的提示词。

        Args:
            messages: 消息列表，格式为 [{"role": "...", "content": "..."}, ...]
        """
        system_prompt = self._strategy.get_system_prompt()
        # 如果外部消息以 system 开头 → 使用当前策略的 system prompt 替换
        if messages and messages[0].get("role") == "system":
            messages = messages[1:]  # 移除外部的 system prompt
        # 用当前策略的 system prompt 重新开头
        self._messages = [{"role": "system", "content": system_prompt}] + messages

    def export_messages(self) -> list[dict]:
        """
        导出完整消息列表（含 system prompt），用于保存

        Returns:
            消息列表副本
        """
        return list(self._messages)

    # ========== 内部方法 ==========

    def update_system_prompt(self) -> None:
        """用当前策略最新的 system prompt 刷新对话首条消息"""
        new_prompt = self._strategy.get_system_prompt()
        if self._messages:
            self._messages[0] = {"role": "system", "content": new_prompt}
        else:
            self._messages = [{"role": "system", "content": new_prompt}]

    def _reset_conversation(self) -> None:
        """重置对话，仅保留当前策略的 System Prompt"""
        system_prompt = self._strategy.get_system_prompt()
        self._messages = [{"role": "system", "content": system_prompt}]
