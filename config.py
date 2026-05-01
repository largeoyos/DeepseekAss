"""
配置管理模块
使用 .env 文件读取 API Key 与 Base URL
"""
import os
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()


class Config:
    """全局配置类，集中管理所有配置项"""

    # DeepSeek API 配置（可从 .env 预填，也可在运行时通过 GUI 设置）
    API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    # 模型列表
    MODEL_V4_FLASH: str = "deepseek-v4-flash"
    MODEL_V4_PRO: str = "deepseek-v4-pro"

    # 默认参数
    DEFAULT_TEMPERATURE: float = 0.7
    DEFAULT_TOP_P: float = 0.9
    DEFAULT_MAX_TOKENS: int = 16384
    DEFAULT_FREQUENCY_PENALTY: float = 0.0

    @classmethod
    def validate(cls) -> None:
        """验证必要配置是否存在（不再强制要求 .env 中已配置，支持运行时输入）"""
        if cls.API_KEY and cls.API_KEY == "your_deepseek_api_key_here":
            raise ValueError(
                "请在 .env 文件中设置有效的 DEEPSEEK_API_KEY，"
                "或在 GUI 启动后手动输入。"
            )