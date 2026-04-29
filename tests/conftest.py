import pytest
import asyncio
from bot.config.schema import Config, RiskConfig, BotConfig
from bot.state.portfolio import Portfolio
from bot.data.market_data import Ticker


@pytest.fixture
def risk_config():
    return RiskConfig(
        starting_capital_jpy=100000,
        max_trade_amount_jpy=10000,
        max_daily_loss_jpy=5000,
        max_open_positions=3,
    )


@pytest.fixture
def portfolio(risk_config):
    p = Portfolio(risk_config.starting_capital_jpy)
    p.set_balance("bitflyer", jpy=50000.0, btc=0.1)
    p.set_balance("gmo", jpy=30000.0, btc=0.05)
    p.set_balance("bitbank", jpy=20000.0, btc=0.05)
    return p


@pytest.fixture
def sample_tickers():
    return {
        "bitflyer": Ticker("bitflyer", "BTC/JPY", bid=9990000, ask=10000000, last=9995000, timestamp=1700000000000),
        "gmo":      Ticker("gmo",      "BTC/JPY", bid=9985000, ask=9995000,  last=9990000, timestamp=1700000000000),
        "bitbank":  Ticker("bitbank",  "BTC/JPY", bid=9988000, ask=9998000,  last=9993000, timestamp=1700000000000),
    }
