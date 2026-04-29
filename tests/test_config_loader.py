import os
import tempfile
import pytest
from bot.config.loader import load_config, ConfigurationError


_VALID_YAML = """
bot:
  dry_run: true
  log_level: INFO
exchanges:
  bitflyer:
    enabled: true
    trading_fee_pct: 0.15
risk:
  starting_capital_jpy: 100000
  max_trade_amount_jpy: 10000
  max_daily_loss_jpy: 5000
strategy:
  active: both
"""


def _write_tmp(content: str) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return f.name


def test_valid_config_loads():
    path = _write_tmp(_VALID_YAML)
    config = load_config(path)
    assert config.bot.dry_run is True
    assert config.risk.max_trade_amount_jpy == 10000
    assert config.strategy.active == "both"
    os.unlink(path)


def test_missing_file_raises():
    with pytest.raises(ConfigurationError, match="not found"):
        load_config("nonexistent_config.yaml")


def test_invalid_strategy_active():
    bad = _VALID_YAML.replace("active: both", "active: invalid_value")
    path = _write_tmp(bad)
    with pytest.raises(ConfigurationError):
        load_config(path)
    os.unlink(path)


def test_slow_period_greater_than_fast():
    bad = _VALID_YAML + "\n  trend:\n    fast_period: 21\n    slow_period: 9\n"
    path = _write_tmp(bad)
    with pytest.raises(ConfigurationError):
        load_config(path)
    os.unlink(path)
