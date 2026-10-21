"""Клиент API-Football (api-sports.io) v3 для live-матчей."""

import time
from typing import Any

import httpx

from . import models
from .models import Event, LiveMatch, LiveSourceError, StatLine

BASE_URL = "https://v3.football.api-sports.io"
SOURCE = "af"
NAME = "API-Football"

STATUSES = {
    "1H": models.LIVE,
    "2H": models.LIVE,
    "ET": models.LIVE,
    "BT": models.LIVE,
    "P": models.LIVE,
    "LIVE": models.LIVE,
    "HT": models.HALFTIME,
    "FT": models.FINISHED,
    "AET": models.FINISHED,
    "PEN": models.FINISHED,
    "TBD": models.SCHEDULED,
    "NS": models.SCHEDULED,
}

# Название в API -> (подпись, ключ для модели)
STAT_TYPES = {
    "Ball Possession": ("Владение мячом", models.POSSESSION),
    "expected_goals": ("Ожидаемые голы (xG)", models.XG),
    "Total Shots": ("Удары", models.SHOTS),
    "Shots on Goal": ("Удары в створ", models.SHOTS_ON_TARGET),
    "Shots insidebox": ("Удары из штрафной", None),
    "Corner Kicks": ("Угловые", models.CORNERS),
    "Goalkeeper Saves": ("Сейвы", None),
    "Fouls": ("Фолы", None),
    "Offsides": ("Офсайды", None),
    "Yellow Cards": ("Жёлтые карточки", None),
    "Red Cards": ("Красные карточки", None),
    "Passes %": ("Точность передач", None),
}


class APIFootball:
    source = SOURCE
    name = NAME

    def __init__(self, api_key: str, cache_ttl: int = 30, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(
            base_url=BASE_URL, headers={"x-apisports-key": api_key}, timeout=15
        )
        self._cache_ttl = cache_ttl
        self._cache: dict[str, tuple[float, list]] = {}

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any]) -> list[dict]:
        key = path + repr(sorted(params.items()))
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < self._cache_ttl:
            return cached[1]
        try:
            response = await self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise LiveSourceError("API-Football недоступен.") from exc
        if response.status_code >= 400:
            raise LiveSourceError(f"API-Football: ошибка {response.status_code}")

        data = response.json()
        # Ошибки (лимит запросов, неверный ключ) приходят с кодом 200 в поле errors
        if data.get("errors"):
            errors = data["errors"]
            message = "; ".join(errors.values()) if isinstance(errors, dict) else str(errors)
            raise LiveSourceError(f"API-Football: {message}")

        result = data.get("response", [])
        self._cache[key] = (time.monotonic(), result)
        return result

    async def live_matches(self) -> list[LiveMatch]:
        return [parse_fixture(f) for f in await self._get("/fixtures", {"live": "all"})]

    async def matches(self, ids: list[str]) -> list[LiveMatch]:
        """Матчи с событиями и статистикой; до 20 ID за один запрос."""
        result = []
        for i in range(0, len(ids), 20):
            fixtures = await self._get("/fixtures", {"ids": "-".join(ids[i : i + 20])})
            result += [parse_fixture(f) for f in fixtures]
        return result

    async def match(self, match_id: str) -> LiveMatch:
        found = await self.matches([match_id])
        if not found:
            raise LiveSourceError("Матч не найден.")
        return found[0]


def _event_kind(event: dict) -> str | None:
    kind, detail = event.get("type", ""), event.get("detail") or ""
    if kind == "Goal":
        return {"Own Goal": "own_goal", "Penalty": "penalty", "Missed Penalty": "missed_penalty"}.get(
            detail, "goal"
        )
    if kind == "Card":
        return "red" if detail in ("Red Card", "Second Yellow card") else "yellow"
    if kind == "subst":
        return "sub"
    if kind == "Var":
        return "var"
    return None


def parse_fixture(data: dict) -> LiveMatch:
    fixture, teams, goals = data["fixture"], data["teams"], data.get("goals", {})
    status = fixture.get("status", {})
    elapsed, extra = status.get("elapsed"), status.get("extra")
    home_id = teams["home"]["id"]

    events = []
    for e in data.get("events") or []:
        kind = _event_kind(e)
        if kind is None:
            continue
        minute = str(e["time"]["elapsed"]) + (f"+{e['time']['extra']}" if e["time"].get("extra") else "")
        events.append(
            Event(
                minute=minute,
                kind=kind,
                side="home" if e["team"]["id"] == home_id else "away",
                player=(e.get("player") or {}).get("name") or "",
                detail=(e.get("assist") or {}).get("name") or "",
            )
        )

    stats = []
    by_team = {s["team"]["id"]: {x["type"]: x["value"] for x in s["statistics"]} for s in data.get("statistics") or []}
    home_stats = by_team.get(home_id, {})
    away_stats = next((v for k, v in by_team.items() if k != home_id), {})
    for api_type, (label, key) in STAT_TYPES.items():
        h, a = home_stats.get(api_type), away_stats.get(api_type)
        if h is None and a is None:
            continue
        stats.append(StatLine(label, str(h if h is not None else 0), str(a if a is not None else 0), key))

    reds = [e for e in events if e.kind == "red"]
    short = status.get("short", "")
    return LiveMatch(
        source=SOURCE,
        id=str(fixture["id"]),
        league=f"{data['league'].get('country', '')}: {data['league']['name']}".lstrip(": "),
        home=teams["home"]["name"],
        away=teams["away"]["name"],
        home_score=goals.get("home"),
        away_score=goals.get("away"),
        status=STATUSES.get(short, models.OTHER),
        minute=elapsed,
        minute_label=("Перерыв" if short == "HT" else f"{elapsed}{f'+{extra}' if extra else ''}'") if elapsed else "",
        home_red=sum(e.side == "home" for e in reds),
        away_red=sum(e.side == "away" for e in reds),
        events=events,
        stats=stats,
    )
