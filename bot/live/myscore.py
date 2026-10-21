"""Получение live-данных с MyScore (myscore.ru).

У MyScore нет публичного API. Сайт сам загружает данные из внутреннего потока
("feed") в формате записей `КЛЮЧ÷ЗНАЧЕНИЕ¬...¬~`, и этот модуль читает тот же поток.
Формат не документирован и может измениться. Тогда нужно обновить
MYSCORE_FEED_URL / MYSCORE_FSIGN (см. README) или поправить разбор полей ниже.
"""

import time
from datetime import datetime, timezone

import httpx

from . import models
from .models import LiveMatch, LiveSourceError, StatLine

SOURCE = "ms"
NAME = "MyScore"
DEFAULT_FEED_URL = "https://d.myscore.ru/x/feed"
DEFAULT_FSIGN = "SW9D1eZo"
SITE_URL = "https://www.myscore.ru"

# AB — общий статус матча
STATUS_SCHEDULED, STATUS_LIVE, STATUS_FINISHED = "1", "2", "3"
# AC — подробная стадия
STAGE_FIRST_HALF, STAGE_SECOND_HALF, STAGE_HALFTIME = "12", "13", "38"
STAGE_EXTRA_TIME, STAGE_PENALTIES = "6", "7"

# Подписи статистики на MyScore -> ключ для модели
STAT_KEYS = {
    "владение мячом": models.POSSESSION,
    "ожидаемые голы (xg)": models.XG,
    "удары": models.SHOTS,
    "всего ударов": models.SHOTS,
    "удары в створ": models.SHOTS_ON_TARGET,
    "угловые": models.CORNERS,
}


def parse_feed(text: str) -> list[dict[str, str]]:
    """Разбивает поток на записи: '~' разделяет записи, '¬' — поля, '÷' — ключ и значение."""
    records = []
    for chunk in text.split("~"):
        record = {}
        for field in chunk.split("¬"):
            key, sep, value = field.partition("÷")
            if sep:
                record[key] = value
        if record:
            records.append(record)
    return records


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def _minute(record: dict[str, str], now: float) -> tuple[int | None, str]:
    """Минута матча. В потоке её нет, поэтому считаем примерно от времени начала."""
    stage = record.get("AC")
    if stage == STAGE_HALFTIME:
        return 45, "Перерыв"
    if stage == STAGE_PENALTIES:
        return 120, "Пенальти"
    start = _int(record.get("AD"))
    if start is None:
        return None, ""
    elapsed = int((now - start) / 60)
    if stage == STAGE_FIRST_HALF:
        minute = min(max(elapsed, 1), 45)
        label = f"~{minute}'" if elapsed <= 45 else "45+'"
        return minute, label
    if stage == STAGE_SECOND_HALF:
        minute = min(max(elapsed - 15, 46), 90)  # ~15 минут перерыва
        label = f"~{minute}'" if elapsed - 15 <= 90 else "90+'"
        return minute, label
    if stage == STAGE_EXTRA_TIME:
        return 90, "Доп. время"
    return None, ""


def _status(record: dict[str, str]) -> str:
    ab, ac = record.get("AB"), record.get("AC")
    if ab == STATUS_LIVE:
        return models.HALFTIME if ac == STAGE_HALFTIME else models.LIVE
    if ab == STATUS_FINISHED:
        return models.FINISHED
    if ab == STATUS_SCHEDULED:
        return models.SCHEDULED
    return models.OTHER


def parse_matches(text: str, now: float | None = None) -> list[LiveMatch]:
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    matches, league = [], ""
    for record in parse_feed(text):
        if "ZA" in record:  # заголовок турнира, относится к следующим матчам
            league = record["ZA"]
            continue
        if "AA" not in record:
            continue
        minute, label = _minute(record, now) if record.get("AB") == STATUS_LIVE else (None, "")
        matches.append(
            LiveMatch(
                source=SOURCE,
                id=record["AA"],
                league=league,
                home=record.get("AE") or record.get("CX") or "?",
                away=record.get("AF") or "?",
                home_score=_int(record.get("AG")),
                away_score=_int(record.get("AH")),
                status=_status(record),
                minute=minute,
                minute_label=label,
                home_red=_int(record.get("AJ")) or 0,
                away_red=_int(record.get("AK")) or 0,
                url=f"{SITE_URL}/match/{record['AA']}/#/match-summary",
            )
        )
    return matches


def parse_stats(text: str) -> list[StatLine]:
    """Статистика матча; берём только первый раздел ("Матч"), без разбивки по таймам."""
    stats, sections = [], 0
    for record in parse_feed(text):
        if "SE" in record:
            sections += 1
            if sections > 1:
                break
        if "SG" in record:
            label = record["SG"]
            stats.append(
                StatLine(label, record.get("SH", "0"), record.get("SI", "0"), STAT_KEYS.get(label.lower()))
            )
    return stats


class MyScore:
    source = SOURCE
    name = NAME

    def __init__(
        self,
        feed_url: str = DEFAULT_FEED_URL,
        fsign: str = DEFAULT_FSIGN,
        cache_ttl: int = 30,
        client: httpx.AsyncClient | None = None,
    ):
        self._feed_url = feed_url.rstrip("/")
        self._client = client or httpx.AsyncClient(
            headers={
                "x-fsign": fsign,
                "Referer": SITE_URL + "/",
                "User-Agent": "Mozilla/5.0 (compatible; football-stats-bot)",
            },
            timeout=15,
        )
        self._cache_ttl = cache_ttl
        self._cache: dict[str, tuple[float, str]] = {}

    async def close(self) -> None:
        await self._client.aclose()

    async def _feed(self, name: str) -> str:
        cached = self._cache.get(name)
        if cached and time.monotonic() - cached[0] < self._cache_ttl:
            return cached[1]
        try:
            response = await self._client.get(f"{self._feed_url}/{name}")
        except httpx.HTTPError as exc:
            raise LiveSourceError("MyScore недоступен.") from exc
        if response.status_code >= 400:
            raise LiveSourceError(
                f"MyScore ответил ошибкой {response.status_code}. "
                "Возможно, сайт изменил формат — обновите MYSCORE_FEED_URL и MYSCORE_FSIGN."
            )
        response.encoding = "utf-8"
        self._cache[name] = (time.monotonic(), response.text)
        return response.text

    async def today_matches(self) -> list[LiveMatch]:
        # f_1_0_3_ru_1: футбол, сегодня, часовой пояс, русский язык
        return parse_matches(await self._feed("f_1_0_3_ru_1"))

    async def live_matches(self) -> list[LiveMatch]:
        return [m for m in await self.today_matches() if m.in_play]

    async def matches(self, ids: list[str]) -> list[LiveMatch]:
        wanted = set(ids)
        return [m for m in await self.today_matches() if m.id in wanted]

    async def match(self, match_id: str) -> LiveMatch:
        found = await self.matches([match_id])
        if not found:
            raise LiveSourceError("Матч не найден в сегодняшней ленте MyScore.")
        match = found[0]
        try:
            match.stats = parse_stats(await self._feed(f"df_st_1_{match_id}"))
        except LiveSourceError:
            pass  # статистики может не быть (например, у низших лиг)
        return match
