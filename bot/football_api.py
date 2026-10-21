"""Асинхронный клиент football-data.org v4 с кэшем в памяти."""

import time
from datetime import date, timedelta
from typing import Any

import httpx

BASE_URL = "https://api.football-data.org/v4"


class FootballAPIError(Exception):
    pass


class FootballAPI:
    def __init__(self, api_key: str, cache_ttl: int = 300, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"X-Auth-Token": api_key},
            timeout=15,
        )
        self._cache_ttl = cache_ttl
        self._cache: dict[str, tuple[float, Any]] = {}

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < self._cache_ttl:
            return cached[1]

        try:
            response = await self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise FootballAPIError("Не удалось связаться с сервером статистики.") from exc

        if response.status_code == 429:
            raise FootballAPIError("Превышен лимит запросов к API (10 в минуту). Попробуйте через минуту.")
        if response.status_code == 403:
            raise FootballAPIError("Эта лига недоступна на вашем тарифе API.")
        if response.status_code == 404:
            raise FootballAPIError("Ничего не найдено.")
        if response.status_code >= 400:
            raise FootballAPIError(f"Ошибка API: {response.status_code}")

        data = response.json()
        self._cache[key] = (time.monotonic(), data)
        return data

    async def standings(self, competition: str) -> dict:
        return await self._get(f"/competitions/{competition}/standings")

    async def scorers(self, competition: str, limit: int = 10) -> list[dict]:
        data = await self._get(f"/competitions/{competition}/scorers", {"limit": limit})
        return data.get("scorers", [])

    async def upcoming_matches(self, competition: str, days: int = 10) -> list[dict]:
        today = date.today()
        data = await self._get(
            f"/competitions/{competition}/matches",
            {"dateFrom": today.isoformat(), "dateTo": (today + timedelta(days=days)).isoformat()},
        )
        return [m for m in data.get("matches", []) if m["status"] in ("SCHEDULED", "TIMED")]

    async def recent_results(self, competition: str, days: int = 10) -> list[dict]:
        today = date.today()
        data = await self._get(
            f"/competitions/{competition}/matches",
            {
                "dateFrom": (today - timedelta(days=days)).isoformat(),
                "dateTo": today.isoformat(),
                "status": "FINISHED",
            },
        )
        return data.get("matches", [])

    async def match(self, match_id: int) -> dict:
        return await self._get(f"/matches/{match_id}")

    async def head2head(self, match_id: int, limit: int = 10) -> list[dict]:
        data = await self._get(f"/matches/{match_id}/head2head", {"limit": limit})
        return data.get("matches", [])

    async def team_matches(self, team_id: int, limit: int = 5) -> list[dict]:
        today = date.today()
        data = await self._get(
            f"/teams/{team_id}/matches",
            {
                "status": "FINISHED",
                "dateFrom": (today - timedelta(days=180)).isoformat(),
                "dateTo": today.isoformat(),
            },
        )
        matches = sorted(data.get("matches", []), key=lambda m: m["utcDate"])
        return matches[-limit:]

    async def competition_teams(self, competition: str) -> list[dict]:
        data = await self._get(f"/competitions/{competition}/teams")
        return data.get("teams", [])
