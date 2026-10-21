import os
from dataclasses import dataclass

# Лиги, доступные на бесплатном тарифе football-data.org
COMPETITIONS: dict[str, str] = {
    "PL": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Премьер-лига",
    "PD": "🇪🇸 Ла Лига",
    "BL1": "🇩🇪 Бундеслига",
    "SA": "🇮🇹 Серия A",
    "FL1": "🇫🇷 Лига 1",
    "DED": "🇳🇱 Эредивизи",
    "PPL": "🇵🇹 Примейра",
    "ELC": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Чемпионшип",
    "BSA": "🇧🇷 Серия A Бразилии",
    "CL": "🏆 Лига чемпионов",
    "EC": "🇪🇺 Чемпионат Европы",
    "WC": "🌍 Чемпионат мира",
}


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    football_api_key: str
    api_football_key: str | None
    myscore_enabled: bool
    myscore_feed_url: str | None
    myscore_fsign: str | None
    live_poll_seconds: int
    timezone: str


def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_settings() -> Settings:
    _load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    api_key = os.environ.get("FOOTBALL_DATA_API_KEY")
    missing = [
        name
        for name, value in (("TELEGRAM_BOT_TOKEN", token), ("FOOTBALL_DATA_API_KEY", api_key))
        if not value
    ]
    if missing:
        raise RuntimeError(f"Не заданы переменные окружения: {', '.join(missing)}")
    return Settings(
        telegram_token=token,
        football_api_key=api_key,
        api_football_key=os.environ.get("API_FOOTBALL_KEY") or None,
        myscore_enabled=os.environ.get("MYSCORE_ENABLED", "1").lower() not in ("0", "false", "no"),
        myscore_feed_url=os.environ.get("MYSCORE_FEED_URL") or None,
        myscore_fsign=os.environ.get("MYSCORE_FSIGN") or None,
        live_poll_seconds=int(os.environ.get("LIVE_POLL_SECONDS", "120")),
        timezone=os.environ.get("BOT_TIMEZONE", "Europe/Moscow"),
    )
