from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

from loguru import logger

from bot.config.loader import load_config, ConfigurationError
from bot.scheduler.main_loop import BotMainLoop


def _setup_logging(log_path: str, log_level: str, rotation: str, retention: str) -> None:
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(sys.stderr, level=log_level, colorize=True,
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")
    logger.add(log_path, level=log_level, rotation=rotation,
               retention=retention, encoding="utf-8",
               format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} | {message}")


def main() -> None:
    # Windows: ProactorEventLoop is required for subprocess/pipe support,
    # but SelectorEventLoop works better with ccxt.pro WebSockets.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"

    try:
        config = load_config(config_path)
    except ConfigurationError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    _setup_logging(
        log_path=config.storage.log_path,
        log_level=config.bot.log_level,
        rotation=config.storage.log_rotation,
        retention=config.storage.log_retention,
    )

    if config.bot.dry_run:
        logger.warning("=" * 50)
        logger.warning("  DRY RUN MODE — 実際の注文は発注されません")
        logger.warning("=" * 50)

    bot = BotMainLoop(config)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _handle_signal() -> None:
        logger.info("Shutdown signal received")
        for task in asyncio.all_tasks(loop):
            task.cancel()

    # add_signal_handler is not supported on Windows; use signal.signal instead
    if sys.platform == "win32":
        signal.signal(signal.SIGINT, lambda s, f: loop.call_soon_threadsafe(_handle_signal))
    else:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _handle_signal)

    try:
        loop.run_until_complete(bot.run())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
