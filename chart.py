#!/usr/bin/env python3
"""
SOL/JPY チャート表示ツール
使い方: python chart.py [--timeframe 15m] [--limit 50] [--live]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

import plotext as plt

# プロジェクトルートを sys.path に追加
sys.path.insert(0, str(Path(__file__).parent))

from bot.config.loader import load_config
from bot.exchanges.bitbank import BitbankAdapter
from bot.indicators.moving_averages import ema_series


def _parse_args():
    p = argparse.ArgumentParser(description="SOL/JPY チャートをターミナルに表示")
    p.add_argument("--timeframe", default="15m", help="足種 (1m / 15m / 1h など)")
    p.add_argument("--pair", default="SOL/JPY", help="通貨ペア")
    p.add_argument("--limit", type=int, default=60, help="表示本数")
    p.add_argument("--live", action="store_true", help="自動更新モード（Ctrl+C で終了）")
    p.add_argument("--interval", type=int, default=30, help="自動更新間隔（秒）")
    return p.parse_args()


def _draw(candles, pair: str, timeframe: str, cfg):
    closes = [c.close for c in candles]
    highs  = [c.high  for c in candles]
    lows   = [c.low   for c in candles]
    times  = [c.timestamp / 1000 for c in candles]  # Unix秒

    ema20 = ema_series(closes, cfg.trend_ema_fast)   # 20
    ema50 = ema_series(closes, cfg.trend_ema_slow)   # 50

    # None を直前の値で埋める（プロット用）
    def fill_none(series):
        out = []
        last = None
        for v in series:
            if v is not None:
                last = v
            out.append(last)
        return out

    ema20f = fill_none(ema20)
    ema50f = fill_none(ema50)

    # 最新値
    last_close  = closes[-1]  if closes  else 0
    last_ema20  = next((v for v in reversed(ema20) if v is not None), 0)
    last_ema50  = next((v for v in reversed(ema50) if v is not None), 0)
    trend = "↑ 上昇" if last_ema20 > last_ema50 else "↓ 下落"
    trend_color = "green" if last_ema20 > last_ema50 else "red"

    plt.clf()
    plt.theme("dark")

    term_w = os.get_terminal_size().columns
    term_h = os.get_terminal_size().lines - 6
    plt.plotsize(term_w, max(term_h, 20))

    # ローソク足（高値・安値）をバーで表現
    plt.bar(times, highs,  color="white",  label="High", width=0.4)
    plt.bar(times, lows,   color=234,      label="Low",  width=0.4)  # 暗い灰色で上書き

    # 終値ライン
    plt.plot(times, closes, color="white",  label=f"Close  {last_close:,.2f}", marker="braille")

    # EMA ライン
    plt.plot(times, ema20f, color="cyan",   label=f"EMA{cfg.trend_ema_fast}  {last_ema20:,.2f}", marker="braille")
    plt.plot(times, ema50f, color="orange", label=f"EMA{cfg.trend_ema_slow}  {last_ema50:,.2f}", marker="braille")

    updated = time.strftime("%H:%M:%S")
    plt.title(f"{pair}  [{timeframe}]   トレンド: {trend}   更新: {updated}")
    plt.xlabel("時刻")
    plt.ylabel("価格 (JPY)")
    plt.date_form("H:M", "d/m H:M")

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
        print(f"ライブモード開始（{args.interval}秒ごとに更新）  Ctrl+C で終了")
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
    asyncio.run(main())
