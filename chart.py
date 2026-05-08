#!/usr/bin/env python3
"""
SOL/JPY チャート表示ツール
使い方: .venv/bin/python chart.py [--timeframe 15m] [--limit 50] [--live]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import plotext as plt

sys.path.insert(0, str(Path(__file__).parent))

from bot.config.loader import load_config
from bot.exchanges.bitbank import BitbankAdapter
from bot.indicators.moving_averages import ema_series


def _parse_args():
    p = argparse.ArgumentParser(description="SOL/JPY チャートをターミナルに表示")
    p.add_argument("--timeframe", default="15m")
    p.add_argument("--pair", default="SOL/JPY")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--live", action="store_true", help="自動更新モード（Ctrl+C で終了）")
    p.add_argument("--interval", type=int, default=30)
    return p.parse_args()


def _draw(candles, pair: str, timeframe: str, cfg):
    closes = [c.close for c in candles]
    highs  = [c.high  for c in candles]
    lows   = [c.low   for c in candles]

    ema20_s = ema_series(closes, cfg.trend_ema_fast)
    ema50_s = ema_series(closes, cfg.trend_ema_slow)

    # None を直前値で補完
    def fill(series):
        out, last = [], None
        for v in series:
            if v is not None:
                last = v
            out.append(last if last is not None else 0.0)
        return out

    ema20f = fill(ema20_s)
    ema50f = fill(ema50_s)

    # X軸ラベル（時刻文字列）
    labels = [
        datetime.fromtimestamp(c.timestamp / 1000).strftime("%H:%M")
        for c in candles
    ]
    xs = list(range(len(candles)))

    # Y軸レンジをローソク足の高低に合わせる
    y_min = min(lows)   * 0.9995
    y_max = max(highs)  * 1.0005

    # 最新値
    last_close = closes[-1]
    last_ema20 = next((v for v in reversed(ema20_s) if v is not None), 0.0)
    last_ema50 = next((v for v in reversed(ema50_s) if v is not None), 0.0)
    trend      = "↑ 上昇" if last_ema20 > last_ema50 else "↓ 下落"

    try:
        w = os.get_terminal_size().columns
        h = os.get_terminal_size().lines - 5
    except OSError:
        w, h = 120, 30

    plt.clf()
    plt.theme("dark")
    plt.plotsize(w, max(h, 20))
    plt.ylim(y_min, y_max)

    # 終値ライン
    plt.plot(xs, closes, color="white", label=f"Close {last_close:,.2f}", marker="braille")
    # EMA ライン
    plt.plot(xs, ema20f, color="cyan",   label=f"EMA{cfg.trend_ema_fast}  {last_ema20:,.2f}", marker="braille")
    plt.plot(xs, ema50f, color="orange", label=f"EMA{cfg.trend_ema_slow}  {last_ema50:,.2f}", marker="braille")

    # X軸ラベルを間引いて表示
    step = max(1, len(xs) // 8)
    tick_xs     = xs[::step]
    tick_labels = labels[::step]
    plt.xticks(tick_xs, tick_labels)

    updated = time.strftime("%H:%M:%S")
    plt.title(f"{pair}  [{timeframe}]   トレンド: {trend}   更新: {updated}")
    plt.xlabel("時刻 (JST)")
    plt.ylabel("価格 (JPY)")

    plt.show()


async def _fetch_and_draw(adapter: BitbankAdapter, args, cfg):
    candles = await adapter.fetch_ohlcv(args.pair, args.timeframe, args.limit)
    if not candles:
        print("ローソク足データが取得できませんでした。")
        return
    _draw(candles, args.pair, args.timeframe, cfg)


async def main():
    args = _parse_args()

    config = load_config("config.yaml")
    ex_cfg = config.exchanges.get("bitbank")
    if not ex_cfg or not ex_cfg.enabled:
        print("ERROR: bitbank が config.yaml で無効になっています。")
        sys.exit(1)

    adapter = BitbankAdapter(ex_cfg.api_key, ex_cfg.api_secret)
    scalp_cfg = config.strategy.sol_scalp

    if args.live:
        print(f"ライブモード（{args.interval}秒ごとに更新）  Ctrl+C で終了")
        await asyncio.sleep(1)
        while True:
            try:
                await _fetch_and_draw(adapter, args, scalp_cfg)
                await asyncio.sleep(args.interval)
            except KeyboardInterrupt:
                print("\n終了します。")
                break
    else:
        await _fetch_and_draw(adapter, args, scalp_cfg)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n終了します。")
