"""
配置管理模块
使用 .env 文件读取 API Key 与 Base URL
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# 从脚本所在目录加载 .env，不依赖当前工作目录
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    load_dotenv(dotenv_path=str(_env_path), override=True)
else:
    # 兜底：尝试当前工作目录
    load_dotenv(override=True)


class Config:
    """全局配置类，集中管理所有配置项"""

    # DeepSeek API 配置（可从 .env 预填，也可在运行时通过 GUI 设置）
    API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    IMAGE_API_KEY: str = os.getenv("IMAGE_API_KEY", "")
    IMAGE_BASE_URL: str = os.getenv("IMAGE_BASE_URL", "")
    IMAGE_MODEL: str = os.getenv("IMAGE_MODEL", "")

    # 模型列表
    MODEL_V4_FLASH: str = "deepseek-v4-flash"
    MODEL_V4_PRO: str = "deepseek-v4-pro"

    # 默认参数
    DEFAULT_TEMPERATURE: float = 0.7
    DEFAULT_TOP_P: float = 0.9
    DEFAULT_MAX_TOKENS: int = 16384
    DEFAULT_FREQUENCY_PENALTY: float = 0.0
    API_TIMEOUT_SECONDS: float = float(os.getenv("DEEPSEEK_API_TIMEOUT_SECONDS", "180"))
    API_MAX_RETRIES: int = int(os.getenv("DEEPSEEK_API_MAX_RETRIES", "1"))

    @classmethod
    def validate(cls) -> None:
        """验证必要配置是否存在（不再强制要求 .env 中已配置，支持运行时输入）"""
        if cls.API_KEY and cls.API_KEY == "your_deepseek_api_key_here":
            raise ValueError(
                "请在 .env 文件中设置有效的 DEEPSEEK_API_KEY，"
                "或在 GUI 启动后手动输入。"
            )
