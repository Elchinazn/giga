import pytest

from bot import analysis


def make_match(home_id, away_id, home_goals, away_goals, date="2026-10-01T15:00:00Z", match_id=1):
    return {
        "id": match_id,
        "utcDate": date,
        "status": "FINISHED",
        "homeTeam": {"id": home_id, "shortName": f"T{home_id}"},
        "awayTeam": {"id": away_id, "shortName": f"T{away_id}"},
        "score": {"fullTime": {"home": home_goals, "away": away_goals}},
    }


def row(team_id, played, gf, ga, position=1, points=0):
    return {
        "position": position,
        "team": {"id": team_id, "shortName": f"T{team_id}"},
        "playedGames": played,
        "won": 0,
        "draw": 0,
        "lost": 0,
        "points": points,
        "goalsFor": gf,
        "goalsAgainst": ga,
        "goalDifference": gf - ga,
    }


def test_team_form_orders_by_date_and_counts_goals():
    matches = [
        make_match(1, 2, 0, 1, date="2026-10-03T15:00:00Z"),
        make_match(3, 1, 1, 1, date="2026-10-02T15:00:00Z"),
        make_match(1, 4, 2, 0, date="2026-10-01T15:00:00Z"),
    ]
    form = analysis.team_form(matches, 1)
    assert form.results == ["W", "D", "L"]
    assert (form.goals_for, form.goals_against) == (3, 2)
    assert form.points == 4


def test_team_form_skips_unplayed_matches():
    unplayed = make_match(1, 2, None, None)
    assert analysis.team_form([unplayed], 1).games == 0


def test_head_to_head_from_home_team_perspective():
    matches = [
        make_match(1, 2, 2, 1),  # хозяева анализируемого матча выиграли дома
        make_match(2, 1, 3, 0),  # проиграли в гостях
        make_match(2, 1, 1, 1),
        make_match(1, 5, 4, 0),  # чужой матч — игнорируется
    ]
    h2h = analysis.head_to_head(matches, home_id=1, away_id=2)
    assert (h2h.games, h2h.home_wins, h2h.draws, h2h.away_wins) == (3, 1, 1, 1)
    assert h2h.total_goals == 8
    assert h2h.both_scored == 2
    assert h2h.over_2_5 == 2


def test_poisson_probabilities_sum_to_one():
    p = analysis.poisson_prediction(1.5, 1.1)
    assert p.home_win + p.draw + p.away_win == pytest.approx(1.0)
    assert p.home_win > p.away_win
    assert 0 < p.over_2_5 < 1 and 0 < p.both_score < 1
    assert len(p.likely_scores) == 3


def test_equal_teams_are_symmetric():
    p = analysis.poisson_prediction(1.3, 1.3)
    assert p.home_win == pytest.approx(p.away_win)


def standings_with_home_away():
    home = [row(1, 10, 25, 5), row(2, 10, 8, 20)]
    away = [row(1, 10, 15, 10), row(2, 10, 6, 22)]
    total = [row(1, 20, 40, 15, position=1), row(2, 20, 14, 42, position=2)]
    return {
        "standings": [
            {"type": "TOTAL", "table": total},
            {"type": "HOME", "table": home},
            {"type": "AWAY", "table": away},
        ]
    }


def test_strong_home_team_is_favourite():
    standings = standings_with_home_away()
    p = analysis.predict(standings, 1, 2, analysis.Form(), analysis.Form())
    assert p.expected_home > p.expected_away
    assert p.home_win > 0.6

    reverse = analysis.predict(standings, 2, 1, analysis.Form(), analysis.Form())
    assert reverse.away_win > reverse.home_win


def test_falls_back_to_total_table_without_home_away():
    standings = {"standings": [{"type": "TOTAL", "table": [row(1, 5, 12, 3), row(2, 5, 3, 12)]}]}
    goals = analysis.expected_goals(standings, 1, 2)
    assert goals is not None and goals[0] > goals[1]


def test_no_prediction_for_unknown_team():
    assert analysis.predict(standings_with_home_away(), 1, 99, analysis.Form(), analysis.Form()) is None
    assert analysis.predict({}, 1, 2, analysis.Form(), analysis.Form()) is None


def test_form_multiplier_bounds():
    assert analysis.form_multiplier(analysis.Form(results=["W"] * 5)) == pytest.approx(1.1)
    assert analysis.form_multiplier(analysis.Form(results=["L"] * 5)) == pytest.approx(0.9)
    assert analysis.form_multiplier(analysis.Form()) == 1.0


def test_shrinkage_pulls_small_samples_to_average():
    # Одна игра с 5 голами при среднем 1.0 не должна давать силу атаки 5.0
    assert analysis._strength(5, 1, 1.0) == pytest.approx(2.0)
    assert analysis._strength(0, 0, 1.0) == 1.0
