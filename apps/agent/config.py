"""Settings load repo-root `.env` by path so API worker processes and any cwd see the same config."""
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# apps/agent/config.py → parents[2] == mykare_ai_assignment/
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _REPO_ROOT / ".env"


class Settings(BaseSettings):
    # Supabase
    supabase_url: str = ""
    supabase_service_role_key: str = ""

    # LiveKit
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""

    # Beyond Presence (bey.chat) avatar
    bey_api_key: str = ""
    bey_avatar_id: str = "f30d7eef-6e71-433f-938d-cecdd8c0b653"  # Yuruo - Medical

    # Voice providers
    deepgram_api_key: str = ""
    cartesia_api_key: str = ""
    cartesia_voice_id: str = "694f9389-aac1-45b6-b726-9d9369183238"
    cartesia_speed: float = 1.15

    # LLM (OpenRouter — same stack as eval_scenarios.py)
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-4o"
    # OpenRouter extended reasoning (passed as extra_body.reasoning; exclude keeps CoT out of API text)
    openrouter_reasoning_enabled: bool = True
    openrouter_reasoning_effort: str = "medium"
    openrouter_reasoning_exclude: bool = True

    # Server — comma-separated; include every Vite origin (5174 if 5173 is in use)
    allowed_origins: str = (
        "http://localhost:5173,http://localhost:5174,"
        "http://127.0.0.1:5173,http://127.0.0.1:5174"
    )
    log_level: str = "INFO"
    demo_mode_enabled: bool = True

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.is_file() else None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("openrouter_model")
    @classmethod
    def reject_deepseek_model(cls, v: str) -> str:
        """Project policy: do not call DeepSeek via OpenRouter (or DeepSeek-native IDs)."""
        if v and "deepseek" in v.lower():
            raise ValueError(
                "OPENROUTER_MODEL must not be a DeepSeek model. "
                "Use another OpenRouter id (e.g. openai/gpt-4o)."
            )
        return v


settings = Settings()
