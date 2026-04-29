# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

BTC/JPY 自動売買Bot（Python）。bitFlyer・GMOコイン・Bitbank の3取引所に同時接続し、トレンドフォロー（EMAクロス）とアービトラージの2戦略を asyncio で並列実行する。初期資本10万円、リスク管理必須、Telegramで取引通知。

## Commands

**Windows（初回セットアップ）**
```bat
setup.bat          # 仮想環境作成・依存インストール・設定ファイル生成
start.bat          # Bot起動
test.bat           # テスト実行
```

**Windows（手動）/ Linux共通**
```bash
# 仮想環境作成
python -m venv .venv

# 依存インストール（Windows）
.venv\Scripts\pip install -r requirements.txt
# 依存インストール（Linux/Mac）
.venv/bin/pip install -r requirements.txt

# テスト実行
.venv\Scripts\pytest tests/ -v                  # Windows
.venv/bin/pytest tests/ -v                       # Linux

# 単一テストファイル
.venv\Scripts\pytest tests/test_risk_manager.py -v

# Bot起動
.venv\Scripts\python main.py config.yaml         # Windows
.venv/bin/python main.py config.yaml             # Linux
```

## Architecture

### Signal-to-Execution Pipeline

```
WebSocket/REST → FeedManager → StrategyComposer → RiskManager → OrderRouter → Exchange
```

`RiskManager.approve()` が唯一の注文承認経路。`ApprovedOrder` 型（`RejectedOrder` ではない）のみが `OrderRouter.execute()` に渡せる。型システムでリスクチェックのバイパスを構造的に防止している。

### Key Modules

| パス | 役割 |
|---|---|
| [bot/config/schema.py](bot/config/schema.py) | Pydantic設定モデル。全コンポーネントはここから設定を受け取る |
| [bot/config/loader.py](bot/config/loader.py) | config.yaml + .env を読み込み `Config` オブジェクトを返す |
| [bot/exchanges/base.py](bot/exchanges/base.py) | `ExchangeAdapter` ABC。exchanges/ 外からは必ずこのインターフェース経由 |
| [bot/exchanges/gmo.py](bot/exchanges/gmo.py) | GMOコイン専用 aiohttp 実装（ccxt非対応のため手書き） |
| [bot/risk/risk_manager.py](bot/risk/risk_manager.py) | リスクゲート。`ApprovedOrder` / `RejectedOrder` を返す |
| [bot/strategies/composer.py](bot/strategies/composer.py) | 複数戦略へのファンアウト。config で `active: "trend" | "arbitrage" | "both"` |
| [bot/scheduler/main_loop.py](bot/scheduler/main_loop.py) | asyncio オーケストレーター。全コンポーネントをここで組み立てる |
| [bot/state/portfolio.py](bot/state/portfolio.py) | 全取引所の残高・損益・ポジション管理 |

### Exchange Adapters

- **bitFlyer / Bitbank**: `ccxt.pro` を使用（WebSocket対応）
- **GMOコイン**: `aiohttp` で直接実装。HMAC-SHA256署名は `_sign_request()` で生成
- 新しい取引所を追加する場合は `ExchangeAdapter` を継承し `main_loop.py` の `_build_adapters()` に追加する

### Risk Management Rules

1. `dry_run: true` → 注文シミュレーションのみ（デフォルト）
2. 日次損失が `max_daily_loss_jpy` 以上 → REJECT
3. オープンポジション数が `max_open_positions` 以上 → REJECT
4. 注文金額が `max_trade_amount_jpy` 超過 → CLAMP（拒否ではなく上限に切り詰め）
5. 残高不足 → REJECT

日次損失カウンターは `storage/state.json` に永続化（プロセス再起動でリセットされない）。日本時間深夜0時に自動リセット。

### Arbitrage Notes

取引所間のBTC送金は行わない。各取引所にJPYとBTCを事前配分しておき、スプレッドが `min_spread_pct` を超えたときに両取引所へ同時発注（`asyncio.gather()`）。片足失敗時は自動的に逆方向の成行注文でリバーサルを試みる。

## Configuration

`config.yaml` にシークレット以外の全設定。APIキーとTelegramトークンは `.env` のみに記載（`.gitignore` 済み）。

重要な設定値：
- `bot.dry_run`: 本番前は必ず `true` でテスト
- `strategy.active`: `"trend"` / `"arbitrage"` / `"both"`
- `risk.max_daily_loss_jpy`: 日次最大損失額（JPY）
- `risk.max_trade_amount_jpy`: 1回の最大取引額（JPY）
- `strategy.arbitrage.min_spread_pct`: アービトラージ最小スプレッド（%）。手数料を考慮して 0.40% 以上推奨

## Deployment (VPS)

```ini
# /etc/systemd/system/trading-bot.service
[Unit]
Description=BTC/JPY Trading Bot
After=network.target

[Service]
WorkingDirectory=/opt/trading-bot
ExecStart=/opt/trading-bot/.venv/bin/python main.py config.yaml
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable trading-bot
systemctl start trading-bot
journalctl -u trading-bot -f
```
