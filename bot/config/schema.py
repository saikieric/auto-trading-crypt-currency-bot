from __future__ import annotations

from typing import Literal, List, Dict
from pydantic import BaseModel, Field, field_validator


class ExchangeConfig(BaseModel):
    enabled: bool = True
    trading_fee_pct: float = 0.0
    api_key: str = ""
    api_secret: str = ""


class RiskConfig(BaseModel):
    starting_capital_jpy: float = 100000.0
    max_trade_amount_jpy: float = 10000.0
    max_daily_loss_jpy: float = 5000.0
    max_open_positions: int = 3

    @field_validator("max_trade_amount_jpy")
    @classmethod
    def trade_amount_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("max_trade_amount_jpy must be positive")
        return v

    @field_validator("max_daily_loss_jpy")
    @classmethod
    def daily_loss_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("max_daily_loss_jpy must be positive")
        return v


class TrendConfig(BaseModel):
    timeframe: str = "15m"
    fast_period: int = 9
    slow_period: int = 21
    signal_confirmation_bars: int = 2
    min_signal_confidence: float = 0.3
    use_maker_orders: bool = False
    maker_timeout_seconds: int = 30

    @field_validator("slow_period")
    @classmethod
    def slow_greater_than_fast(cls, v: int, info) -> int:
        fast = info.data.get("fast_period", 0)
        if v <= fast:
            raise ValueError("slow_period must be greater than fast_period")
        return v


class ArbitrageConfig(BaseModel):
    min_spread_pct: float = 0.40
    cooldown_seconds: int = 60
    exchanges: List[str] = Field(default_factory=lambda: ["bitflyer", "gmo", "bitbank"])


class StrategyConfig(BaseModel):
    active: Literal["trend", "arbitrage", "both"] = "both"
    trend: TrendConfig = Field(default_factory=TrendConfig)
    sol_trend: TrendConfig = Field(default_factory=TrendConfig)
    arbitrage: ArbitrageConfig = Field(default_factory=ArbitrageConfig)


class DataConfig(BaseModel):
    ohlcv_poll_interval_seconds: int = 60
    ticker_refresh_interval_seconds: int = 2
    ohlcv_history_candles: int = 50


class NotificationConfig(BaseModel):
    discord_webhook_url: str = ""
    discord_bot_token: str = ""
    discord_guild_id: str = ""       # スラッシュコマンドを登録するサーバーID
    discord_command_channel_id: str = ""  # コマンドを受け付けるチャンネルID（空=全チャンネル）
    heartbeat_interval_hours: int = 6
    notify_on: List[str] = Field(
        default_factory=lambda: [
            "trade_open", "trade_close", "risk_rejected",
            "daily_summary", "error", "bot_start", "bot_stop",
        ]
    )


class StorageConfig(BaseModel):
    db_path: str = "storage/trades.db"
    state_path: str = "storage/state.json"
    log_path: str = "logs/bot.log"
    log_rotation: str = "100 MB"
    log_retention: str = "30 days"


class BotConfig(BaseModel):
    dry_run: bool = True
    log_level: str = "INFO"


class Config(BaseModel):
    bot: BotConfig = Field(default_factory=BotConfig)
    exchanges: Dict[str, ExchangeConfig] = Field(default_factory=dict)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
