import logging
from zoneinfo import ZoneInfo

from telegram import BotCommand
from telegram.ext import Application

from bot import handlers
from bot.config import load_settings
from bot.football_api import FootballAPI
from bot.live import handlers as live_handlers
from bot.live import myscore
from bot.live.api_football import APIFootball
from bot.live.service import LiveService
from bot.live.storage import SubscriptionStore

COMMANDS = [
    BotCommand("live", "Матчи в прямом эфире"),
    BotCommand("subs", "Мои подписки на матчи"),
    BotCommand("leagues", "Список лиг"),
    BotCommand("matches", "Ближайшие матчи и анализ: /matches PL"),
    BotCommand("table", "Турнирная таблица: /table PL"),
    BotCommand("results", "Последние результаты: /results PL"),
    BotCommand("scorers", "Бомбардиры: /scorers PL"),
    BotCommand("team", "Статистика команды: /team PL Arsenal"),
    BotCommand("help", "Помощь"),
]


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands(COMMANDS)


async def post_shutdown(app: Application) -> None:
    await app.bot_data["api"].close()
    if "live" in app.bot_data:
        await app.bot_data["live"].close()


def build_live_service(settings) -> LiveService | None:
    sources = []
    if settings.api_football_key:
        sources.append(APIFootball(settings.api_football_key))
    if settings.myscore_enabled:
        sources.append(
            myscore.MyScore(
                feed_url=settings.myscore_feed_url or myscore.DEFAULT_FEED_URL,
                fsign=settings.myscore_fsign or myscore.DEFAULT_FSIGN,
            )
        )
    return LiveService(sources) if sources else None


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    settings = load_settings()
    app = (
        Application.builder()
        .token(settings.telegram_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.bot_data["api"] = FootballAPI(settings.football_api_key)
    app.bot_data["tz"] = ZoneInfo(settings.timezone)
    handlers.register(app)

    live = build_live_service(settings)
    if live:
        app.bot_data["live"] = live
        store = SubscriptionStore(settings.subs_file)
        app.bot_data["subs_store"] = store
        app.bot_data["subs"] = store.load()
        logging.info("Загружено подписок на матчи: %d (%s)", len(app.bot_data["subs"]), store.path)
        live_handlers.register(app, settings.live_poll_seconds)
        logging.info("Live-источники: %s", ", ".join(live.name(s) for s in live.order))
    else:
        logging.warning("Live-режим выключен: задайте API_FOOTBALL_KEY или MYSCORE_ENABLED=1")

    logging.info("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
