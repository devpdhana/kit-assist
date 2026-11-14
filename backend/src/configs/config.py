"""Central settings. Secrets come only from environment variables."""

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _list(value: str | None, default: list[str]) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()] if value else default


class Settings:
    BASE_DIR: Path = Path(__file__).resolve().parents[2]
    IS_PROD: bool = os.getenv("ENV", os.getenv("NODE_ENV", "")).lower() == "production"

    # College system
    COLLEGE_API_BASE: str = os.getenv("COLLEGE_API_BASE", "https://ht.knowvally.com").rstrip("/")
    COLLEGE_API_KEY: str = os.getenv("COLLEGE_API_KEY", "")
    COLLEGE_TIMEOUT_S: float = 8.0

    # AI providers (probed 2026-11-14: all do reliable tool / structured output)
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_FAST_MODELS: list[str] = _list(
        os.getenv("GROQ_FAST_MODELS"), ["openai/gpt-oss-20b", "qwen/qwen3.8-27b", "openai/gpt-oss-120b"]
    )
    GROQ_SMART_MODELS: list[str] = _list(
        os.getenv("GROQ_SMART_MODELS"), ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"]
    )
    GROQ_STT_MODEL: str = os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    # "-latest" aliases survive model retirements.
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")

    # Workflow
    MAX_SERVICE_RETRIES: int = 2
    MAX_RESPONSE_RETRIES: int = 2
    EVAL_PASS_SCORE: int = 85
    EVAL_LLM_BAND: tuple[int, int] = (60, 85)  # ask the LLM judge only inside this band

    # App
    SESSION_SECRET: str = os.getenv("SESSION_SECRET") or secrets.token_hex(32)
    SESSION_TTL_S: int = 8 * 60 * 60
    DB_PATH: str = os.getenv("DB_PATH", str(BASE_DIR / "data" / "history.db"))
    DOCS_REFRESH_S: int = 300
    FRONTEND_DIST: Path = Path(os.getenv("FRONTEND_DIST", str(BASE_DIR.parent / "frontend" / "dist")))

    @property
    def has_llm(self) -> bool:
        return bool(self.GROQ_API_KEY or self.GEMINI_API_KEY)


settings = Settings()
