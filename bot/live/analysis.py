"""Live-прогноз: вероятности итогового исхода с учётом счёта, минуты, статистики и удалений.

Оставшиеся голы каждой команды моделируются распределением Пуассона. Интенсивность
берётся из средних показателей лиг и смещается в сторону команды, которая
создаёт больше моментов (xG или удары) в этом матче.
"""

from dataclasses import dataclass

from ..analysis import MAX_GOALS, _poisson
from . import models
from .models import LiveMatch

# Средние голы за матч хозяев и гостей в топ-лигах
PRIOR_HOME_GOALS = 1.5
PRIOR_AWAY_GOALS = 1.2
MATCH_MINUTES = 93  # 90 + среднее компенсированное время
RED_CARD_OWN = 0.75  # команда в меньшинстве забивает реже...
RED_CARD_OPPONENT = 1.25  # ...а соперник чаще
MAX_STATS_WEIGHT = 0.6  # насколько сильно к концу матча доверяем статистике игры


@dataclass
class LivePrediction:
    remaining_home: float  # ожидаемые голы до конца матча
    remaining_away: float
    home_win: float
    draw: float
    away_win: float
    next_goal_home: float
    next_goal_away: float
    no_more_goals: float
    over_2_5: float
    likely_finals: list[tuple[str, float]]


def pressure_share(match: LiveMatch) -> float | None:
    """Доля моментов хозяев по статистике матча (0..1), с псевдосчётом для малых выборок."""
    xg = match.stat(models.XG)
    if xg and sum(xg) > 0:
        h, a = xg
        return (h + 0.3) / (h + a + 0.6)
    on_target, shots = match.stat(models.SHOTS_ON_TARGET), match.stat(models.SHOTS)
    if on_target or shots:
        on_target, shots = on_target or (0, 0), shots or (0, 0)
        h = on_target[0] + 0.3 * shots[0]
        a = on_target[1] + 0.3 * shots[1]
        return (h + 1) / (h + a + 2)
    return None


def remaining_minutes(match: LiveMatch) -> float:
    minute = match.minute or 0
    if match.status == models.HALFTIME:
        minute = 45
    return max(MATCH_MINUTES - minute, 1)


def live_prediction(match: LiveMatch) -> LivePrediction | None:
    if not match.in_play or match.home_score is None or match.away_score is None:
        return None

    minutes_left = remaining_minutes(match)
    total_rate = (PRIOR_HOME_GOALS + PRIOR_AWAY_GOALS) / MATCH_MINUTES
    home_share = PRIOR_HOME_GOALS / (PRIOR_HOME_GOALS + PRIOR_AWAY_GOALS)

    observed = pressure_share(match)
    if observed is not None:
        weight = MAX_STATS_WEIGHT * min((match.minute or 45) / 90, 1)
        home_share = (1 - weight) * home_share + weight * observed

    lam_home = total_rate * minutes_left * home_share
    lam_away = total_rate * minutes_left * (1 - home_share)
    lam_home *= RED_CARD_OWN**match.home_red * RED_CARD_OPPONENT**match.away_red
    lam_away *= RED_CARD_OWN**match.away_red * RED_CARD_OPPONENT**match.home_red

    home_probs = [_poisson(i, lam_home) for i in range(MAX_GOALS + 1)]
    away_probs = [_poisson(j, lam_away) for j in range(MAX_GOALS + 1)]
    home_win = draw = away_win = over = 0.0
    finals = []
    for i, p_home in enumerate(home_probs):
        for j, p_away in enumerate(away_probs):
            p = p_home * p_away
            final_home, final_away = match.home_score + i, match.away_score + j
            if final_home > final_away:
                home_win += p
            elif final_home < final_away:
                away_win += p
            else:
                draw += p
            if final_home + final_away > 2:
                over += p
            finals.append((f"{final_home}:{final_away}", p))

    total = home_win + draw + away_win
    no_more = home_probs[0] * away_probs[0]
    lam_sum = lam_home + lam_away
    finals.sort(key=lambda s: s[1], reverse=True)

    return LivePrediction(
        remaining_home=lam_home,
        remaining_away=lam_away,
        home_win=home_win / total,
        draw=draw / total,
        away_win=away_win / total,
        next_goal_home=(1 - no_more) * lam_home / lam_sum,
        next_goal_away=(1 - no_more) * lam_away / lam_sum,
        no_more_goals=no_more,
        over_2_5=over / total,
        likely_finals=finals[:3],
    )
