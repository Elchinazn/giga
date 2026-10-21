"""Превращение данных API и результатов анализа в HTML-сообщения Telegram."""

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from .analysis import H2H, Form, Prediction

FORM_ICONS = {"W": "🟢", "D": "🟡", "L": "🔴"}


def team_name(team: dict) -> str:
    return team.get("shortName") or team.get("name") or "?"


def local_time(utc_date: str, tz: ZoneInfo) -> str:
    dt = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))
    return dt.astimezone(tz).strftime("%d.%m %H:%M")


def percent(p: float) -> str:
    return f"{p * 100:.0f}%"


def format_standings(standings: dict, title: str) -> str:
    blocks = [b for b in standings.get("standings", []) if b.get("type") == "TOTAL"]
    if not blocks:
        return "Таблица пока недоступна."

    parts = [f"<b>{escape(title)}</b>"]
    for block in blocks:
        lines = [" #  Команда         И   РМ   О"]
        for row in block["table"]:
            name = team_name(row["team"])[:14]
            lines.append(
                f"{row['position']:>2}  {name:<14} {row['playedGames']:>2} "
                f"{row['goalDifference']:>+4} {row['points']:>3}"
            )
        if block.get("group"):
            parts.append(f"\n<b>{escape(block['group'].replace('_', ' ').title())}</b>")
        parts.append("<pre>" + escape("\n".join(lines)) + "</pre>")
    return "\n".join(parts)


def format_scorers(scorers: list[dict], title: str) -> str:
    if not scorers:
        return "Статистика бомбардиров пока недоступна."
    lines = [f"<b>⚽ Бомбардиры — {escape(title)}</b>\n"]
    for i, s in enumerate(scorers, 1):
        assists = s.get("assists") or 0
        penalties = s.get("penalties") or 0
        extra = f", пен. {penalties}" if penalties else ""
        lines.append(
            f"{i}. <b>{escape(s['player']['name'])}</b> ({escape(team_name(s['team']))}) — "
            f"{s.get('goals') or 0} г., {assists} пас.{extra}"
        )
    return "\n".join(lines)


def format_match_line(match: dict, tz: ZoneInfo) -> str:
    home, away = team_name(match["homeTeam"]), team_name(match["awayTeam"])
    full_time = match.get("score", {}).get("fullTime", {})
    if match["status"] == "FINISHED" and full_time.get("home") is not None:
        return f"{escape(home)} <b>{full_time['home']}:{full_time['away']}</b> {escape(away)}"
    return f"{local_time(match['utcDate'], tz)}  {escape(home)} — {escape(away)}"


def format_results(matches: list[dict], title: str, tz: ZoneInfo) -> str:
    if not matches:
        return "За последние дни сыгранных матчей нет."
    lines = [f"<b>📋 Результаты — {escape(title)}</b>\n"]
    for match in sorted(matches, key=lambda m: m["utcDate"], reverse=True)[:20]:
        lines.append(f"{local_time(match['utcDate'], tz)[:5]}  {format_match_line(match, tz)}")
    return "\n".join(lines)


def format_form(form: Form) -> str:
    if not form.games:
        return "нет данных"
    icons = "".join(FORM_ICONS[r] for r in form.results)
    return f"{icons}  ({form.points} из {form.games * 3} очк., мячи {form.goals_for}:{form.goals_against})"


def format_team(team: dict, row: dict | None, form: Form, last_matches: list[dict], tz: ZoneInfo) -> str:
    lines = [f"<b>🛡 {escape(team.get('name', '?'))}</b>"]
    if team.get("venue"):
        lines.append(f"🏟 {escape(team['venue'])}")
    if row:
        games = row["playedGames"] or 1
        lines += [
            "",
            f"Место: <b>{row['position']}</b>, очки: <b>{row['points']}</b> за {row['playedGames']} игр",
            f"В/Н/П: {row['won']}/{row['draw']}/{row['lost']}",
            f"Мячи: {row['goalsFor']}:{row['goalsAgainst']} "
            f"({row['goalsFor'] / games:.2f} забито и {row['goalsAgainst'] / games:.2f} пропущено за игру)",
        ]
    lines += ["", f"<b>Форма (последние {form.games}):</b> {format_form(form)}"]
    for match in reversed(last_matches):
        lines.append(f"• {format_match_line(match, tz)}")
    return "\n".join(lines)


def format_analysis(
    match: dict,
    tz: ZoneInfo,
    home_row: dict | None,
    away_row: dict | None,
    home_form: Form,
    away_form: Form,
    h2h: H2H,
    prediction: Prediction | None,
) -> str:
    home, away = team_name(match["homeTeam"]), team_name(match["awayTeam"])
    competition = match.get("competition", {}).get("name", "")
    lines = [
        f"<b>📊 {escape(home)} — {escape(away)}</b>",
        f"{escape(competition)}, {local_time(match['utcDate'], tz)}",
        "",
    ]

    if home_row and away_row:
        lines += [
            "<b>Турнирное положение</b>",
            f"{escape(home)}: {home_row['position']} место, {home_row['points']} очк., "
            f"мячи {home_row['goalsFor']}:{home_row['goalsAgainst']}",
            f"{escape(away)}: {away_row['position']} место, {away_row['points']} очк., "
            f"мячи {away_row['goalsFor']}:{away_row['goalsAgainst']}",
            "",
        ]

    lines += [
        "<b>Форма (последние матчи)</b>",
        f"{escape(home)}: {format_form(home_form)}",
        f"{escape(away)}: {format_form(away_form)}",
        "",
    ]

    if h2h.games:
        lines += [
            f"<b>Личные встречи (последние {h2h.games})</b>",
            f"{escape(home)} {h2h.home_wins} — ничьи {h2h.draws} — {h2h.away_wins} {escape(away)}",
            f"Голов в среднем: {h2h.total_goals / h2h.games:.1f}, "
            f"ОЗ: {h2h.both_scored}/{h2h.games}, ТБ 2.5: {h2h.over_2_5}/{h2h.games}",
            "",
        ]
    else:
        lines += ["<b>Личные встречи:</b> нет данных", ""]

    if prediction:
        scores = ", ".join(f"{s} ({percent(p)})" for s, p in prediction.likely_scores)
        lines += [
            "<b>🔮 Прогноз модели</b>",
            f"Ожидаемые голы: {prediction.expected_home:.2f} — {prediction.expected_away:.2f}",
            f"П1: <b>{percent(prediction.home_win)}</b>  "
            f"X: <b>{percent(prediction.draw)}</b>  "
            f"П2: <b>{percent(prediction.away_win)}</b>",
            f"Тотал больше 2.5: {percent(prediction.over_2_5)}",
            f"Обе забьют: {percent(prediction.both_score)}",
            f"Вероятные счета: {scores}",
            "",
            "<i>Прогноз — статистическая оценка, а не гарантия результата.</i>",
        ]
    else:
        lines.append("<i>Для прогноза недостаточно данных турнирной таблицы.</i>")

    return "\n".join(lines)
