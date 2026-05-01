"""
基础策略抽象类
定义所有聊天模式的统一接口，遵循策略模式（Strategy Pattern）
任何新增模式只需继承本类并实现 get_system_prompt() 方法即可
"""

from abc import ABC, abstractmethod

from config import Config


class BaseStrategy(ABC):
    """策略抽象基类"""

    @abstractmethod
    def get_name(self) -> str:
        """
        返回模式名称，用于 UI 显示
        """
        ...

    @abstractmethod
    def get_system_prompt(self) -> str:
        """
        返回该模式对应的 System Prompt（系统提示词）
        """
        ...

    def get_welcome_message(self) -> str:
        """
        返回进入该模式时的欢迎语，子类可覆盖
        """
        return f"已进入【{self.get_name()}】模式，开始对话吧！"

    @property
    def recommended_model(self) -> str:
        """
        返回推荐模型（可被子类覆盖）
        默认使用 deepseek-chat
        """
        return Config.MODEL_CHAT

    @property
    def recommended_temperature(self) -> float:
        """
        返回推荐温度参数（可被子类覆盖）
        """
        return Config.DEFAULT_TEMPERATURE

    @property
    def recommended_top_p(self) -> float:
        """
        返回推荐 top_p 参数（可被子类覆盖）
        """
        return Config.DEFAULT_TOP_P

    @property
    def recommended_frequency_penalty(self) -> float:
        """
        返回推荐 frequency_penalty 参数（可被子类覆盖）
        """
        return Config.DEFAULT_FREQUENCY_PENALTY

    @property
    def recommended_max_tokens(self) -> int:
        """
        返回推荐 max_tokens 参数（可被子类覆盖）
        """
        return Config.DEFAULT_MAX_TOKENS