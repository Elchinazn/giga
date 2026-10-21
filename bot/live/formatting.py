"""Сообщения Telegram для live-режима."""

import html

from ..formatting import percent
from . import models
from .analysis import LivePrediction
from .models import LiveMatch

EVENT_ICONS = {
    "goal": "⚽",
    "penalty": "⚽ (пен.)",
    "own_goal": "⚽ (автогол)",
    "missed_penalty": "❌ незабитый пенальти",
    "yellow": "🟨",
    "red": "🟥",
    "sub": "🔄",
    "var": "📺 VAR",
}
def escape(text: str) -> str:
    return html.escape(text, quote=False)


KEY_EVENTS = {"goal", "penalty", "own_goal", "missed_penalty", "red", "var"}


def score(match: LiveMatch) -> str:
    if match.home_score is None:
        return "—"
    return f"{match.home_score}:{match.away_score}"


def status_label(match: LiveMatch) -> str:
    if match.status == models.FINISHED:
        return "Завершён"
    if match.status == models.SCHEDULED:
        return "Не начался"
    return match.minute_label or "LIVE"


def red_cards(count: int) -> str:
    return " " + "🟥" * count if count else ""


def format_live_list(matches: list[LiveMatch], source_name: str) -> str:
    if not matches:
        return f"Сейчас нет матчей в прямом эфире.\n\n<i>Источник: {source_name}</i>"
    lines = [f"<b>📡 Матчи в прямом эфире: {len(matches)}</b>"]
    league = None
    for m in sorted(matches, key=lambda m: m.league):
        if m.league != league:
            league = m.league
            lines.append(f"\n<b>{escape(league)}</b>")
        lines.append(
            f"<code>{escape(status_label(m)):>8}</code> {escape(m.home)}{red_cards(m.home_red)} "
            f"<b>{score(m)}</b> {escape(m.away)}{red_cards(m.away_red)}"
        )
    lines.append(f"\n<i>Источник: {source_name}. Нажмите на матч для live-анализа 👇</i>")
    return "\n".join(lines)


def format_event(event: models.Event, match: LiveMatch) -> str:
    team = match.home if event.side == "home" else match.away
    icon = EVENT_ICONS.get(event.kind, "•")
    player = f" {escape(event.player)}" if event.player else ""
    extra = ""
    if event.detail and event.kind in ("goal", "penalty"):
        extra = f" (пас: {escape(event.detail)})"
    elif event.detail and event.kind == "sub":
        extra = f" ↔ {escape(event.detail)}"
    return f"{event.minute}' {icon}{player}{extra} — {escape(team)}"


def format_live_match(match: LiveMatch, prediction: LivePrediction | None, source_name: str) -> str:
    lines = [
        f"<b>📡 {escape(status_label(match))}</b> · {escape(match.league)}",
        f"<b>{escape(match.home)}{red_cards(match.home_red)}  {score(match)}  "
        f"{escape(match.away)}{red_cards(match.away_red)}</b>",
    ]

    key_events = [e for e in match.events if e.kind in KEY_EVENTS]
    if key_events:
        lines += ["", "<b>События</b>"]
        lines += [format_event(e, match) for e in key_events]

    if match.stats:
        rows = [f"{s.home:>5} – {s.away:<5} {s.label}" for s in match.stats]
        lines += ["", "<b>Статистика</b>", "<pre>" + escape("\n".join(rows)) + "</pre>"]

    if prediction:
        finals = ", ".join(f"{s} ({percent(p)})" for s, p in prediction.likely_finals)
        lines += [
            "",
            "<b>🔮 Live-прогноз на итог</b>",
            f"П1: <b>{percent(prediction.home_win)}</b>  "
            f"X: <b>{percent(prediction.draw)}</b>  "
            f"П2: <b>{percent(prediction.away_win)}</b>",
            f"Следующий гол: {escape(match.home)} {percent(prediction.next_goal_home)} · "
            f"{escape(match.away)} {percent(prediction.next_goal_away)} · "
            f"голов больше не будет {percent(prediction.no_more_goals)}",
            f"Тотал матча больше 2.5: {percent(prediction.over_2_5)}",
            f"Вероятный итог: {finals}",
        ]
        if not match.stats:
            lines.append("<i>Статистики нет — прогноз учитывает только счёт, время и удаления.</i>")

    footer = f"Источник: {source_name}"
    if match.url:
        footer += f' · <a href="{html.escape(match.url)}">открыть на MyScore</a>'
    lines += ["", f"<i>{footer}</i>"]
    return "\n".join(lines)


def format_notification(old: LiveMatch, new: LiveMatch) -> list[str]:
    """Сообщения о том, что изменилось между двумя снимками матча."""
    header = f"{escape(new.home)} <b>{score(new)}</b> {escape(new.away)}"
    minute = f" {escape(new.minute_label)}" if new.minute_label else ""
    messages = []

    for side, old_goals, new_goals, team in (
        ("home", old.home_score or 0, new.home_score or 0, new.home),
        ("away", old.away_score or 0, new.away_score or 0, new.away),
    ):
        if new_goals > old_goals:
            scorers = [
                e for e in new.events if e.side == side and e.kind in ("goal", "penalty", "own_goal")
            ][-(new_goals - old_goals) :]
            who = ", ".join(f"{e.minute}' {escape(e.player)}" for e in scorers if e.player)
            messages.append(f"⚽ <b>ГОЛ!</b>{minute} {escape(team)}\n{header}" + (f"\n{who}" if who else ""))
        elif new_goals < old_goals:
            messages.append(f"📺 Гол отменён{minute}\n{header}")

    for old_red, new_red, team in ((old.home_red, new.home_red, new.home), (old.away_red, new.away_red, new.away)):
        if new_red > old_red:
            messages.append(f"🟥 <b>Удаление</b>{minute} — {escape(team)}\n{header}")

    if old.status == models.LIVE and new.status == models.HALFTIME:
        messages.append(f"⏸ Перерыв\n{header}")
    if old.status == models.HALFTIME and new.status == models.LIVE:
        messages.append(f"▶️ Начался второй тайм\n{header}")
    if old.status == models.SCHEDULED and new.in_play:
        messages.append(f"🏁 Матч начался\n{header}")
    if old.status != models.FINISHED and new.status == models.FINISHED:
        messages.append(f"🏁 <b>Матч окончен</b>\n{header}")
    return messages
