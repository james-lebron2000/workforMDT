"""集中化配置 - 从环境变量加载,所有模块统一引用 settings。"""
from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # App
    app_env: str = "development"
    app_secret: str = "dev-secret-change-me"
    app_name: str = "TumorBoard AI"

    # DB
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "tumorboard"
    postgres_user: str = "tumorboard"
    postgres_password: str = "change-me"

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # MinIO
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "tumorboard"
    minio_secret_key: str = "change-me"
    minio_bucket: str = "tumorboard"
    minio_secure: bool = False
    minio_region: str = "us-east-1"
    minio_public_endpoint: str = "http://localhost:9000"

    # AI services
    # OCR 默认走「火山引擎通用文字识别」(见 services/volcengine_ocr.py),AK/SK 鉴权,5000 次/月免费;
    # 旧的 ocr_service_url 仅在 V1 自部署 PaddleOCR 路径需要时填(默认 None)。
    ocr_service_url: Optional[str] = None
    # asr_service_url 仅在 asr_provider="funasr" 时需要(自部署 GPU 节点)
    asr_service_url: str = "http://ai-gpu:8002"

    # 火山引擎 OCR(general_basic)凭证 — AK/SK,通过 volcengine SDK 自动签名
    volcengine_ak: Optional[str] = None
    volcengine_sk: Optional[str] = None

    # ASR 提供方:"volcengine" (默认,走豆包音频理解 API) | "funasr" (自部署 GPU 节点)
    # ⚠️ 红线:asr_provider=volcengine 时原音频会上传到火山自有云;
    # 必须与 docs/privacy-policy.md §2/§3 中"音频去向"声明保持一致,
    # 用户同意书 REQUIRED_AFFIRMATIONS 必须明确告知。
    asr_provider: str = "volcengine"
    # 豆包音频理解 model id — 全模态理解模型
    # 参考:https://www.volcengine.com/docs/82379/2377589
    # 与文本 doubao_model 解耦(同 endpoint 不同模型);控制台 endpoint 页面可复制确切 API ID。
    doubao_audio_model: str = "doubao-seed-2.0-lite"
    # 单次音频段最长时长(秒) — 超过则切片;火山限制约 60min,留足余量
    audio_segment_max_seconds: int = 1800

    # LLM provider
    llm_provider: str = "doubao"
    llm_fallback_providers: str = "qwen,kimi"

    # Doubao
    doubao_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    doubao_api_key: Optional[str] = None
    doubao_model: str = "doubao-seed-1-6-250615"

    # Qwen
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_api_key: Optional[str] = None
    qwen_model: str = "qwen-max"

    # Kimi
    kimi_base_url: str = "https://api.moonshot.cn/v1"
    kimi_api_key: Optional[str] = None
    kimi_model: str = "moonshot-v1-32k"

    # Claude
    claude_base_url: str = "https://api.anthropic.com/v1"
    claude_api_key: Optional[str] = None
    claude_model: str = "claude-sonnet-4-6"

    # GPT
    gpt_base_url: str = "https://api.openai.com/v1"
    gpt_api_key: Optional[str] = None
    gpt_model: str = "gpt-4o-mini"

    # WeChat
    wechat_appid: Optional[str] = None
    wechat_secret: Optional[str] = None

    # Retention
    retention_days_raw: int = 30
    retention_days_full: int = 365

    # Privacy policy version - 必须匹配 docs/privacy-policy.md 顶端的版本号
    # 当政策更新时,这里 bump 一次,所有旧 consent 自动失效,用户重新签
    # v1.1 (2026-05-27):ASR 切到火山豆包音频理解,原音频上传到火山自有云(不再仅在本服务器)
    policy_version: str = "v1.1"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_database_url(self) -> str:
        """alembic / celery 用同步驱动"""
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def fallback_providers_list(self) -> List[str]:
        return [p.strip() for p in self.llm_fallback_providers.split(",") if p.strip()]


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
