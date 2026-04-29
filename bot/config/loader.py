from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import ValidationError

from bot.config.schema import Config, ExchangeConfig


class ConfigurationError(Exception):
    pass


def load_config(config_path: str = "config.yaml") -> Config:
    load_dotenv()

    path = Path(config_path)
    if not path.exists():
        raise ConfigurationError(f"Config file not found: {config_path}")

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    _inject_secrets(raw)

    try:
        return Config.model_validate(raw)
    except ValidationError as e:
        raise ConfigurationError(f"Invalid configuration:\n{e}") from e


def _inject_secrets(raw: dict) -> None:
    """Overlay API keys and Discord credentials from environment variables."""
    exchanges_raw = raw.setdefault("exchanges", {})

    mappings = {
        "bitflyer": ("BITFLYER_API_KEY", "BITFLYER_API_SECRET"),
        "gmo": ("GMO_API_KEY", "GMO_API_SECRET"),
        "bitbank": ("BITBANK_API_KEY", "BITBANK_API_SECRET"),
    }

    for name, (key_env, secret_env) in mappings.items():
        if name not in exchanges_raw:
            continue
        exchanges_raw[name]["api_key"] = os.getenv(key_env, "")
        exchanges_raw[name]["api_secret"] = os.getenv(secret_env, "")

    notifications_raw = raw.setdefault("notifications", {})
    notifications_raw["discord_webhook_url"] = os.getenv("DISCORD_WEBHOOK_URL", "")
    notifications_raw["discord_bot_token"] = os.getenv("DISCORD_BOT_TOKEN", "")
    notifications_raw["discord_guild_id"] = os.getenv("DISCORD_GUILD_ID", "")
    notifications_raw["discord_command_channel_id"] = os.getenv("DISCORD_COMMAND_CHANNEL_ID", "")
