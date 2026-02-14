from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(ValueError):
    """Raised when configuration is invalid."""


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    owner_telegram_id: int
    polza_api_key: str
    polza_base_url: str
    polza_model: str
    system_prompt: str
    database_url: str
    max_context_messages: int
    llm_timeout_seconds: float
    unauthorized_mode: str
    unauthorized_message: str
    auto_message_enabled: bool
    auto_message_idle_hours_min: float
    auto_message_idle_hours_max: float
    auto_message_check_minutes: int

    @property
    def sqlite_path(self) -> str:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            raise ConfigError(
                "Only sqlite DATABASE_URL is supported in this MVP. "
                "Expected format: sqlite:///path/to/db.sqlite3"
            )
        path = self.database_url[len(prefix) :]
        if not path:
            raise ConfigError("DATABASE_URL sqlite path cannot be empty")
        return path



def _get_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required env var: {name}")
    return value



def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, str(default)).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean (true/false)")


SYSTEM_PROMPT = """
Тут необходимо указать системный промт
"""


def load_settings() -> Settings:
    owner_raw = _get_required("OWNER_TELEGRAM_ID")
    try:
        owner_telegram_id = int(owner_raw)
    except ValueError as exc:
        raise ConfigError("OWNER_TELEGRAM_ID must be integer") from exc

    max_context_raw = os.getenv("MAX_CONTEXT_MESSAGES", "40").strip()
    try:
        max_context_messages = int(max_context_raw)
    except ValueError as exc:
        raise ConfigError("MAX_CONTEXT_MESSAGES must be integer") from exc

    timeout_raw = os.getenv("LLM_TIMEOUT_SECONDS", "60").strip()
    try:
        llm_timeout_seconds = float(timeout_raw)
    except ValueError as exc:
        raise ConfigError("LLM_TIMEOUT_SECONDS must be number") from exc

    unauthorized_mode = os.getenv("UNAUTHORIZED_MODE", "deny").strip().lower()
    if unauthorized_mode not in {"deny", "ignore"}:
        raise ConfigError("UNAUTHORIZED_MODE must be 'deny' or 'ignore'")

    auto_message_enabled = _get_bool("AUTO_MESSAGE_ENABLED", False)

    min_idle_raw = os.getenv("AUTO_MESSAGE_IDLE_HOURS_MIN", "1").strip()
    max_idle_raw = os.getenv("AUTO_MESSAGE_IDLE_HOURS_MAX", "3").strip()
    try:
        auto_message_idle_hours_min = float(min_idle_raw)
        auto_message_idle_hours_max = float(max_idle_raw)
    except ValueError as exc:
        raise ConfigError("AUTO_MESSAGE_IDLE_HOURS_MIN/MAX must be numbers") from exc
    if auto_message_idle_hours_min <= 0 or auto_message_idle_hours_max <= 0:
        raise ConfigError("AUTO_MESSAGE_IDLE_HOURS_MIN/MAX must be > 0")
    if auto_message_idle_hours_min > auto_message_idle_hours_max:
        raise ConfigError("AUTO_MESSAGE_IDLE_HOURS_MIN cannot be greater than MAX")

    check_raw = os.getenv("AUTO_MESSAGE_CHECK_MINUTES", "10").strip()
    try:
        auto_message_check_minutes = int(check_raw)
    except ValueError as exc:
        raise ConfigError("AUTO_MESSAGE_CHECK_MINUTES must be integer") from exc
    if auto_message_check_minutes < 1:
        raise ConfigError("AUTO_MESSAGE_CHECK_MINUTES must be >= 1")

    return Settings(
        telegram_bot_token=_get_required("TELEGRAM_BOT_TOKEN"),
        owner_telegram_id=owner_telegram_id,
        polza_api_key=_get_required("POLZA_API_KEY"),
        polza_base_url=os.getenv("POLZA_BASE_URL", "https://api.polza.ai/api/v1").strip(),
        polza_model=_get_required("POLZA_MODEL"),
        system_prompt=SYSTEM_PROMPT,
        database_url=os.getenv("DATABASE_URL", "sqlite:///data.db").strip(),
        max_context_messages=max_context_messages,
        llm_timeout_seconds=llm_timeout_seconds,
        unauthorized_mode=unauthorized_mode,
        unauthorized_message=os.getenv("UNAUTHORIZED_MESSAGE", "Access denied").strip(),
        auto_message_enabled=auto_message_enabled,
        auto_message_idle_hours_min=auto_message_idle_hours_min,
        auto_message_idle_hours_max=auto_message_idle_hours_max,
        auto_message_check_minutes=auto_message_check_minutes,
    )
