"""Статистический анализ матчей: форма, личные встречи и прогноз по модели Пуассона.

Модуль не делает сетевых запросов — только считает по данным API, поэтому легко тестируется.
"""

import math
from dataclasses import dataclass, field

# Сколько "виртуальных" средних матчей подмешивать к силе команды,
# чтобы в начале сезона пара удачных игр не давала экстремальных оценок
SHRINK_GAMES = 3
MAX_GOALS = 10


@dataclass
class Form:
    results: list[str] = field(default_factory=list)  # "W" / "D" / "L", от старых к новым
    goals_for: int = 0
    goals_against: int = 0

    @property
    def points(self) -> int:
        return sum({"W": 3, "D": 1, "L": 0}[r] for r in self.results)

    @property
    def games(self) -> int:
        return len(self.results)


@dataclass
class H2H:
    games: int = 0
    home_wins: int = 0  # победы команды, играющей дома в анализируемом матче
    draws: int = 0
    away_wins: int = 0
    total_goals: int = 0
    both_scored: int = 0
    over_2_5: int = 0


@dataclass
class Prediction:
    expected_home: float
    expected_away: float
    home_win: float
    draw: float
    away_win: float
    over_2_5: float
    both_score: float
    likely_scores: list[tuple[str, float]]


def match_result_for(match: dict, team_id: int) -> tuple[str, int, int] | None:
    """Результат матча с точки зрения команды: (W/D/L, забито, пропущено)."""
    full_time = match.get("score", {}).get("fullTime", {})
    home_goals, away_goals = full_time.get("home"), full_time.get("away")
    if home_goals is None or away_goals is None:
        return None
    if match["homeTeam"]["id"] == team_id:
        scored, conceded = home_goals, away_goals
    elif match["awayTeam"]["id"] == team_id:
        scored, conceded = away_goals, home_goals
    else:
        return None
    outcome = "W" if scored > conceded else "L" if scored < conceded else "D"
    return outcome, scored, conceded


def team_form(matches: list[dict], team_id: int) -> Form:
    form = Form()
    for match in sorted(matches, key=lambda m: m["utcDate"]):
        result = match_result_for(match, team_id)
        if result is None:
            continue
        outcome, scored, conceded = result
        form.results.append(outcome)
        form.goals_for += scored
        form.goals_against += conceded
    return form


def head_to_head(matches: list[dict], home_id: int, away_id: int) -> H2H:
    h2h = H2H()
    for match in matches:
        result = match_result_for(match, home_id)
        if result is None or away_id not in (match["homeTeam"]["id"], match["awayTeam"]["id"]):
            continue
        outcome, scored, conceded = result
        h2h.games += 1
        if outcome == "W":
            h2h.home_wins += 1
        elif outcome == "L":
            h2h.away_wins += 1
        else:
            h2h.draws += 1
        h2h.total_goals += scored + conceded
        h2h.both_scored += scored > 0 and conceded > 0
        h2h.over_2_5 += scored + conceded > 2
    return h2h


def find_table(standings: dict, table_type: str, team_id: int) -> list[dict] | None:
    """Таблица нужного типа (TOTAL/HOME/AWAY), в которой есть команда."""
    for block in standings.get("standings", []):
        if block.get("type") != table_type:
            continue
        if any(row["team"]["id"] == team_id for row in block["table"]):
            return block["table"]
    return None


def find_row(table: list[dict] | None, team_id: int) -> dict | None:
    if not table:
        return None
    return next((row for row in table if row["team"]["id"] == team_id), None)


def _avg_goals(table: list[dict]) -> tuple[float, float]:
    """Среднее забитых и пропущенных за матч по таблице."""
    games = sum(row["playedGames"] for row in table)
    if games == 0:
        return 0.0, 0.0
    return (
        sum(row["goalsFor"] for row in table) / games,
        sum(row["goalsAgainst"] for row in table) / games,
    )


def _strength(goals: int, games: int, league_avg: float) -> float:
    """Отношение к среднему по лиге, сглаженное к 1.0 при малом числе игр."""
    if league_avg <= 0:
        return 1.0
    # (games * goals_per_game / league_avg + SHRINK_GAMES * 1.0) / (games + SHRINK_GAMES)
    return (goals / league_avg + SHRINK_GAMES) / (games + SHRINK_GAMES)


def expected_goals(standings: dict, home_id: int, away_id: int) -> tuple[float, float] | None:
    """Ожидаемые голы хозяев и гостей по силе атаки/обороны (модель Пуассона Махера).

    Используются домашняя таблица для хозяев и выездная для гостей; если их нет
    (например, в кубках) — общая таблица.
    """
    home_table = find_table(standings, "HOME", home_id)
    away_table = find_table(standings, "AWAY", away_id)
    home_row, away_row = find_row(home_table, home_id), find_row(away_table, away_id)

    if home_row and away_row and home_row["playedGames"] and away_row["playedGames"]:
        # В домашней таблице goalsFor — голы хозяев, goalsAgainst — голы гостей
        avg_home, avg_away = _avg_goals(home_table)
    else:
        total = find_table(standings, "TOTAL", home_id)
        home_row, away_row = find_row(total, home_id), find_row(total, away_id)
        if not home_row or not away_row:
            return None
        per_team, _ = _avg_goals(total)
        # Небольшое преимущество своего поля, когда раздельных таблиц нет
        avg_home, avg_away = per_team * 1.1, per_team * 0.9

    if avg_home <= 0 or avg_away <= 0:
        return None

    home_attack = _strength(home_row["goalsFor"], home_row["playedGames"], avg_home)
    home_defence = _strength(home_row["goalsAgainst"], home_row["playedGames"], avg_away)
    away_attack = _strength(away_row["goalsFor"], away_row["playedGames"], avg_away)
    away_defence = _strength(away_row["goalsAgainst"], away_row["playedGames"], avg_home)

    return home_attack * away_defence * avg_home, away_attack * home_defence * avg_away


def form_multiplier(form: Form) -> float:
    """Поправка за текущую форму: от 0.9 (все поражения) до 1.1 (все победы)."""
    if not form.games:
        return 1.0
    return 0.9 + 0.2 * form.points / (3 * form.games)


def _poisson(k: int, lam: float) -> float:
    return math.exp(-lam) * lam**k / math.factorial(k)


def poisson_prediction(lam_home: float, lam_away: float) -> Prediction:
    home_probs = [_poisson(i, lam_home) for i in range(MAX_GOALS + 1)]
    away_probs = [_poisson(j, lam_away) for j in range(MAX_GOALS + 1)]

    home_win = draw = away_win = over = 0.0
    scores: list[tuple[str, float]] = []
    for i, p_home in enumerate(home_probs):
        for j, p_away in enumerate(away_probs):
            p = p_home * p_away
            if i > j:
                home_win += p
            elif i < j:
                away_win += p
            else:
                draw += p
            if i + j > 2:
                over += p
            scores.append((f"{i}:{j}", p))

    # Нормируем, чтобы хвост за пределами MAX_GOALS не искажал сумму
    total = home_win + draw + away_win
    both_score = (1 - home_probs[0]) * (1 - away_probs[0])
    scores.sort(key=lambda s: s[1], reverse=True)

    return Prediction(
        expected_home=lam_home,
        expected_away=lam_away,
        home_win=home_win / total,
        draw=draw / total,
        away_win=away_win / total,
        over_2_5=over / total,
        both_score=both_score,
        likely_scores=scores[:3],
    )


def predict(standings: dict, home_id: int, away_id: int, home_form: Form, away_form: Form) -> Prediction | None:
    goals = expected_goals(standings, home_id, away_id)
    if goals is None:
        return None
    lam_home, lam_away = goals
    lam_home *= form_multiplier(home_form)
    lam_away *= form_multiplier(away_form)
    return poisson_prediction(max(lam_home, 0.05), max(lam_away, 0.05))
