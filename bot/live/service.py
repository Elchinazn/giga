"""Выбор источника live-данных: основной + запасной."""

import logging

from .models import LiveMatch, LiveSourceError

log = logging.getLogger(__name__)


class LiveService:
    def __init__(self, sources: list):
        if not sources:
            raise ValueError("Нужен хотя бы один источник live-данных")
        self.sources = {s.source: s for s in sources}
        self.order = [s.source for s in sources]  # первый — основной

    def name(self, source: str) -> str:
        return self.sources[source].name

    async def live_matches(self, source: str | None = None) -> tuple[str, list[LiveMatch]]:
        """Live-матчи из указанного источника или из первого рабочего по порядку."""
        candidates = [source] if source else self.order
        errors = []
        for candidate in candidates:
            try:
                return candidate, await self.sources[candidate].live_matches()
            except LiveSourceError as exc:
                log.warning("Источник %s недоступен: %s", candidate, exc)
                errors.append(str(exc))
        raise LiveSourceError("\n".join(errors))

    async def match(self, source: str, match_id: str) -> LiveMatch:
        return await self.sources[source].match(match_id)

    async def matches(self, source: str, ids: list[str]) -> list[LiveMatch]:
        return await self.sources[source].matches(ids)

    async def close(self) -> None:
        for s in self.sources.values():
            await s.close()
