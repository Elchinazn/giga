"""Команды и кнопки Telegram-бота."""

import asyncio
import functools
import logging
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from . import analysis, formatting
from .config import COMPETITIONS
from .football_api import FootballAPI, FootballAPIError

log = logging.getLogger(__name__)

HELP_TEXT = """<b>⚽ Футбольный аналитик</b>

Выберите лигу кнопкой ниже или используйте команды:

/leagues — список лиг
/table PL — турнирная таблица
/matches PL — ближайшие матчи с кнопками анализа
/results PL — последние результаты
/scorers PL — лучшие бомбардиры
/team PL Arsenal — статистика и форма команды
/analyze 12345 — анализ и прогноз матча по его ID

<b>Коды лиг:</b> {codes}"""


def _api(context: ContextTypes.DEFAULT_TYPE) -> FootballAPI:
    return context.bot_data["api"]


def _tz(context: ContextTypes.DEFAULT_TYPE) -> ZoneInfo:
    return context.bot_data["tz"]


async def _reply(update: Update, text: str, keyboard: InlineKeyboardMarkup | None = None) -> None:
    message = update.effective_message
    await message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=keyboard, disable_web_page_preview=True
    )


def handles_api_errors(func):
    """Показывает пользователю понятное сообщение вместо падения обработчика."""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.callback_query:
            await update.callback_query.answer()
        try:
            return await func(update, context)
        except FootballAPIError as exc:
            await _reply(update, f"⚠️ {exc}")
        except Exception:
            log.exception("Ошибка в обработчике %s", func.__name__)
            await _reply(update, "⚠️ Что-то пошло не так. Попробуйте позже.")

    return wrapper


def leagues_keyboard() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(name, callback_data=f"lg:{code}") for code, name in COMPETITIONS.items()]
    return InlineKeyboardMarkup([buttons[i : i + 2] for i in range(0, len(buttons), 2)])


def league_menu_keyboard(code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Таблица", callback_data=f"tbl:{code}"),
                InlineKeyboardButton("📅 Матчи", callback_data=f"mt:{code}"),
            ],
            [
                InlineKeyboardButton("📋 Результаты", callback_data=f"res:{code}"),
                InlineKeyboardButton("⚽ Бомбардиры", callback_data=f"sc:{code}"),
            ],
        ]
    )


def _league_code(raw: str | None) -> str | None:
    code = (raw or "").upper()
    return code if code in COMPETITIONS else None


async def _require_league(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    code = _league_code(context.args[0] if context.args else None)
    if code is None:
        await _reply(update, "Укажите код лиги, например: <code>PL</code>. Выберите лигу:", leagues_keyboard())
    return code


# --- Отправка данных (общая для команд и кнопок) ---


async def send_table(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    standings = await _api(context).standings(code)
    await _reply(update, formatting.format_standings(standings, COMPETITIONS[code]))


async def send_scorers(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    scorers = await _api(context).scorers(code)
    await _reply(update, formatting.format_scorers(scorers, COMPETITIONS[code]))


async def send_results(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    matches = await _api(context).recent_results(code)
    await _reply(update, formatting.format_results(matches, COMPETITIONS[code], _tz(context)))


async def send_upcoming(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    matches = (await _api(context).upcoming_matches(code))[:15]
    if not matches:
        await _reply(update, "В ближайшие дни матчей нет.")
        return
    lines = [f"<b>📅 Ближайшие матчи — {COMPETITIONS[code]}</b>\n"]
    lines += [formatting.format_match_line(m, _tz(context)) for m in matches]
    lines.append("\nНажмите на матч, чтобы получить анализ и прогноз 👇")
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"📊 {formatting.team_name(m['homeTeam'])} — {formatting.team_name(m['awayTeam'])}",
                    callback_data=f"an:{m['id']}",
                )
            ]
            for m in matches
        ]
    )
    await _reply(update, "\n".join(lines), keyboard)


async def _optional(coro, default):
    """Второстепенные данные (личные встречи, форма) не должны ломать весь анализ."""
    try:
        return await coro
    except FootballAPIError as exc:
        log.info("Необязательные данные недоступны: %s", exc)
        return default


async def send_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE, match_id: int) -> None:
    api = _api(context)
    await update.effective_chat.send_action("typing")
    match = await api.match(match_id)
    home_id, away_id = match["homeTeam"]["id"], match["awayTeam"]["id"]
    code = match.get("competition", {}).get("code")

    standings, h2h_matches, home_matches, away_matches = await asyncio.gather(
        _optional(api.standings(code), {}) if code else asyncio.sleep(0, {}),
        _optional(api.head2head(match_id), []),
        _optional(api.team_matches(home_id), []),
        _optional(api.team_matches(away_id), []),
    )

    home_form = analysis.team_form(home_matches, home_id)
    away_form = analysis.team_form(away_matches, away_id)
    h2h = analysis.head_to_head(
        [m for m in h2h_matches if m.get("id") != match_id], home_id, away_id
    )
    total = analysis.find_table(standings, "TOTAL", home_id)
    prediction = analysis.predict(standings, home_id, away_id, home_form, away_form)

    text = formatting.format_analysis(
        match,
        _tz(context),
        analysis.find_row(total, home_id),
        analysis.find_row(total, away_id),
        home_form,
        away_form,
        h2h,
        prediction,
    )
    await _reply(update, text)


# --- Команды ---


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, HELP_TEXT.format(codes=", ".join(COMPETITIONS)), leagues_keyboard())


async def leagues(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, "Выберите лигу:", leagues_keyboard())


@handles_api_errors
async def table_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if code := await _require_league(update, context):
        await send_table(update, context, code)


@handles_api_errors
async def matches_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if code := await _require_league(update, context):
        await send_upcoming(update, context, code)


@handles_api_errors
async def results_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if code := await _require_league(update, context):
        await send_results(update, context, code)


@handles_api_errors
async def scorers_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if code := await _require_league(update, context):
        await send_scorers(update, context, code)


@handles_api_errors
async def analyze_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].isdigit():
        await _reply(update, "Укажите ID матча: <code>/analyze 12345</code>\nИли откройте /matches PL и нажмите на матч.")
        return
    await send_analysis(update, context, int(context.args[0]))


def find_team(teams: list[dict], query: str) -> dict | None:
    query = query.lower().strip()
    for exact in (True, False):
        for team in teams:
            names = [team.get(k) or "" for k in ("name", "shortName", "tla")]
            if any(n.lower() == query if exact else query in n.lower() for n in names):
                return team
    return None


@handles_api_errors
async def team_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    code = _league_code(context.args[0] if context.args else None)
    if code is None or len(context.args) < 2:
        await _reply(update, "Использование: <code>/team PL Arsenal</code>")
        return

    api = _api(context)
    team = find_team(await api.competition_teams(code), " ".join(context.args[1:]))
    if team is None:
        await _reply(update, f"Команда не найдена в лиге {COMPETITIONS[code]}.")
        return

    standings, last_matches = await asyncio.gather(
        _optional(api.standings(code), {}), api.team_matches(team["id"])
    )
    row = analysis.find_row(analysis.find_table(standings, "TOTAL", team["id"]), team["id"])
    form = analysis.team_form(last_matches, team["id"])
    await _reply(update, formatting.format_team(team, row, form, last_matches, _tz(context)))


# --- Кнопки ---


@handles_api_errors
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    action, _, value = update.callback_query.data.partition(":")
    if action == "an" and value.isdigit():
        await send_analysis(update, context, int(value))
        return

    code = _league_code(value)
    if code is None:
        return
    if action == "lg":
        await _reply(update, f"<b>{COMPETITIONS[code]}</b> — что показать?", league_menu_keyboard(code))
    elif action == "tbl":
        await send_table(update, context, code)
    elif action == "mt":
        await send_upcoming(update, context, code)
    elif action == "res":
        await send_results(update, context, code)
    elif action == "sc":
        await send_scorers(update, context, code)


def register(app: Application) -> None:
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("leagues", leagues))
    app.add_handler(CommandHandler("table", table_cmd))
    app.add_handler(CommandHandler("matches", matches_cmd))
    app.add_handler(CommandHandler("results", results_cmd))
    app.add_handler(CommandHandler("scorers", scorers_cmd))
    app.add_handler(CommandHandler("team", team_cmd))
    app.add_handler(CommandHandler("analyze", analyze_cmd))
    app.add_handler(CallbackQueryHandler(on_button))
