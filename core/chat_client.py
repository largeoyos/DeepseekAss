"""
DeepSeek 聊天客户端 - 核心逻辑
负责 API 调用、对话管理、模型切换、参数控制，与具体模式（策略）解耦
"""
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
    def messages(self) -> list[dict]:
        """返回当前对话历史副本"""
        return list(self._messages)

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
            Config.MODEL_CHAT,
            Config.MODEL_REASONER,
            Config.MODEL_V4_FLASH,
            Config.MODEL_V4_PRO,
        }
        if model not in known:
            print(f"[警告] 未知模型 '{model}'，将尝试使用，但可能出错。")
        self._model = model

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

    def _build_api_kwargs(self, stream: bool = False) -> dict:
        """构造 API 调用参数字典"""
        return {
            "model": self._model,
            "messages": self._messages,
            "temperature": self._temperature,
            "top_p": self._top_p,
            "max_tokens": self._max_tokens,
            "frequency_penalty": self._frequency_penalty,
            "stream": stream,
        }

    def chat(self, user_input: str) -> str:
        """
        发送消息并获取完整回复（非流式）

        Args:
            user_input: 用户输入文本

        Returns:
            模型的完整回复文本
        """
        self._messages.append({"role": "user", "content": user_input})

        try:
            response = self._client.chat.completions.create(
                **self._build_api_kwargs(stream=False),
            )
            assistant_content = response.choices[0].message.content
            if assistant_content is None:
                assistant_content = "[模型返回了空回复]"
            self._messages.append({"role": "assistant", "content": assistant_content})
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
        self._messages.append({"role": "user", "content": user_input})

        try:
            stream = self._client.chat.completions.create(
                **self._build_api_kwargs(stream=True),
            )

            full_reply: list[str] = []
            for chunk in stream:
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    full_reply.append(delta.content)
                    yield delta.content

            # 流式结束后将完整回复写入对话历史
            self._messages.append({"role": "assistant", "content": "".join(full_reply)})

        except Exception as e:
            self._messages.pop()
            raise RuntimeError(f"API 流式调用失败: {e}") from e

    # ========== 内部方法 ==========

    def _reset_conversation(self) -> None:
        """重置对话，仅保留当前策略的 System Prompt"""
        system_prompt = self._strategy.get_system_prompt()
        self._messages = [{"role": "system", "content": system_prompt}]