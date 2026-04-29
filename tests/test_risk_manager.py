import pytest
import pytest_asyncio
from bot.risk.risk_manager import RiskManager, ApprovedOrder, RejectedOrder
from bot.strategies.base import TradeSignal, SignalType


@pytest.fixture
def risk_manager(risk_config, portfolio, tmp_path):
    state_path = str(tmp_path / "state.json")
    return RiskManager(
        config=risk_config,
        portfolio=portfolio,
        state_path=state_path,
        dry_run=True,
    )


def _signal(amount_jpy=8000.0, strategy="trend", buy_ex="bitflyer", sell_ex="bitflyer"):
    return TradeSignal(
        strategy=strategy,
        signal=SignalType.BUY,
        pair="BTC/JPY",
        buy_exchange=buy_ex,
        sell_exchange=sell_ex,
        suggested_amount_jpy=amount_jpy,
        reason="test signal",
        confidence=0.8,
    )


@pytest.mark.asyncio
async def test_approve_normal_signal(risk_manager):
    result = await risk_manager.approve(_signal(), current_btc_price=10000000)
    assert isinstance(result, ApprovedOrder)
    assert result.amount_jpy == 8000.0


@pytest.mark.asyncio
async def test_clamp_oversized_signal(risk_manager):
    result = await risk_manager.approve(_signal(amount_jpy=50000), current_btc_price=10000000)
    assert isinstance(result, ApprovedOrder)
    assert result.amount_jpy == 10000.0  # clamped to max_trade_amount_jpy


@pytest.mark.asyncio
async def test_reject_when_daily_loss_exceeded(risk_manager, portfolio):
    portfolio._todays_realized_pnl = -6000.0  # exceeds 5000 limit
    result = await risk_manager.approve(_signal(), current_btc_price=10000000)
    assert isinstance(result, RejectedOrder)
    assert "Daily loss" in result.reason


@pytest.mark.asyncio
async def test_reject_when_max_positions_reached(risk_manager, portfolio):
    from bot.data.market_data import OrderResult
    import time
    for i in range(3):
        portfolio.add_open_position(OrderResult(
            exchange="bitflyer", order_id=f"x{i}", side="buy", pair="BTC/JPY",
            amount_btc=0.001, price=10000000, fee_jpy=0, status="open",
            timestamp=time.time() * 1000,
        ))
    result = await risk_manager.approve(_signal(), current_btc_price=10000000)
    assert isinstance(result, RejectedOrder)
    assert "positions" in result.reason
