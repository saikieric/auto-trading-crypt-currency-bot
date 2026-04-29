from __future__ import annotations

from typing import Optional
import pandas as pd


def sma(values: list[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values: list[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    s = pd.Series(values)
    result = s.ewm(span=period, adjust=False).mean()
    return float(result.iloc[-1])


def ema_series(values: list[float], period: int) -> list[Optional[float]]:
    if len(values) < period:
        return [None] * len(values)
    s = pd.Series(values)
    result = s.ewm(span=period, adjust=False).mean()
    out: list[Optional[float]] = [None] * (period - 1)
    out.extend(result.iloc[period - 1:].tolist())
    return out


def detect_crossover(
    fast_values: list[float],
    slow_values: list[float],
    fast_period: int,
    slow_period: int,
    confirmation_bars: int = 2,
) -> Optional[str]:
    """
    Returns "golden" (buy signal) or "death" (sell signal) if a confirmed crossover
    occurred within the last `confirmation_bars` candles, else None.
    """
    fast = ema_series(fast_values, fast_period)
    slow = ema_series(slow_values, slow_period)

    # Need at least confirmation_bars + 1 valid values
    valid_pairs = [
        (f, s)
        for f, s in zip(fast, slow)
        if f is not None and s is not None
    ]
    if len(valid_pairs) < confirmation_bars + 1:
        return None

    recent = valid_pairs[-(confirmation_bars + 1):]

    # Check if cross happened at bar -(confirmation_bars)
    prev_fast, prev_slow = recent[0]
    if prev_fast is None or prev_slow is None:
        return None

    # Verify the cross held for confirmation_bars
    cross_type: Optional[str] = None
    if prev_fast < prev_slow:
        # Potential golden cross: fast crosses above slow
        if all(f > s for f, s in recent[1:]):
            cross_type = "golden"
    elif prev_fast > prev_slow:
        # Potential death cross: fast crosses below slow
        if all(f < s for f, s in recent[1:]):
            cross_type = "death"

    return cross_type
