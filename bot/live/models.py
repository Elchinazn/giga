"""Общие модели live-данных — одинаковые для API-Football и MyScore."""

from dataclasses import dataclass, field

# Статусы матча
SCHEDULED = "scheduled"
LIVE = "live"
HALFTIME = "halftime"
FINISHED = "finished"
OTHER = "other"  # перенесён, прерван и т. п.

# Ключи статистики, по которым считается live-прогноз
POSSESSION = "possession"
SHOTS = "shots"
SHOTS_ON_TARGET = "shots_on_target"
CORNERS = "corners"
XG = "xg"


class LiveSourceError(Exception):
    """Источник live-данных недоступен или вернул ошибку — текст показывается пользователю."""


@dataclass
class Event:
    minute: str  # "67" или "90+3"
    kind: str  # goal / own_goal / penalty / missed_penalty / yellow / red / sub / var
    side: str  # home / away
    player: str = ""
    detail: str = ""


@dataclass
class StatLine:
    label: str
    home: str
    away: str
    key: str | None = None  # одна из констант выше, если статистика используется в модели


@dataclass
class LiveMatch:
    source: str  # "af" (API-Football) или "ms" (MyScore)
    id: str
    league: str
    home: str
    away: str
    home_score: int | None
    away_score: int | None
    status: str
    minute: int | None = None
    minute_label: str = ""  # как показывать минуту: "67'", "45+2'", "~70'"
    home_red: int = 0
    away_red: int = 0
    events: list[Event] = field(default_factory=list)
    stats: list[StatLine] = field(default_factory=list)
    url: str | None = None

    @property
    def in_play(self) -> bool:
        return self.status in (LIVE, HALFTIME)

    def stat(self, key: str) -> tuple[float, float] | None:
        """Числовое значение статистики для хозяев и гостей ("55%" -> 55.0)."""
        for line in self.stats:
            if line.key == key:
                try:
                    return _number(line.home), _number(line.away)
                except ValueError:
                    return None
        return None


def _number(value: str) -> float:
    return float(str(value).strip().rstrip("%").replace(",", ".") or 0)
