import logging
import os
from zoneinfo import ZoneInfo

from telegram import BotCommand
from telegram.ext import Application

from bot import handlers
from bot.config import load_settings
from bot.football_api import FootballAPI

COMMANDS = [
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
    app.bot_data["tz"] = ZoneInfo(os.environ.get("BOT_TIMEZONE", "Europe/Moscow"))
    handlers.register(app)

    logging.info("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
