"""Live-режим: список матчей, live-анализ и уведомления о событиях."""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import Forbidden
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from ..handlers import _reply
from . import formatting, models
from .analysis import live_prediction
from .formatting import escape
from .models import LiveMatch, LiveSourceError
from .service import LiveService

log = logging.getLogger(__name__)

MAX_LIST = 30
MAX_SUBS_PER_CHAT = 10
MAX_MISSES = 10  # сколько опросов подряд матч может отсутствовать в ответе, прежде чем подписка снимается

SOURCE_ALIASES = {"ms": "ms", "myscore": "ms", "майскор": "ms", "af": "af", "api": "af", "apifootball": "af"}


def _service(context: ContextTypes.DEFAULT_TYPE) -> LiveService:
    return context.bot_data["live"]


def _subs(context: ContextTypes.DEFAULT_TYPE) -> dict:
    # (source, match_id) -> {"chats": set[int], "snapshot": LiveMatch, "misses": int}
    return context.bot_data.setdefault("subs", {})


def _save_subs(context: ContextTypes.DEFAULT_TYPE) -> None:
    store = context.bot_data.get("subs_store")
    if store is None:
        return
    try:
        store.save(_subs(context))
    except OSError:
        log.exception("Не удалось сохранить подписки в %s", store.path)


async def _safe(update: Update, coro) -> None:
    try:
        await coro
    except LiveSourceError as exc:
        await _reply(update, f"⚠️ {exc}")
    except Exception:
        log.exception("Ошибка в live-обработчике")
        await _reply(update, "⚠️ Что-то пошло не так. Попробуйте позже.")


def match_keyboard(match: LiveMatch, subscribed: bool) -> InlineKeyboardMarkup:
    ref = f"{match.source}:{match.id}"
    follow = (
        InlineKeyboardButton("🔕 Не следить", callback_data=f"lunsub:{ref}")
        if subscribed
        else InlineKeyboardButton("🔔 Следить", callback_data=f"lsub:{ref}")
    )
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Обновить", callback_data=f"lv:{ref}"), follow]])


async def send_live_list(
    update: Update, context: ContextTypes.DEFAULT_TYPE, source: str | None, query: str = ""
) -> None:
    service = _service(context)
    source, matches = await service.live_matches(source)
    if query:
        q = query.lower()
        matches = [m for m in matches if q in f"{m.league} {m.home} {m.away}".lower()]
    total = len(matches)
    matches = sorted(matches, key=lambda m: m.league)[:MAX_LIST]

    text = formatting.format_live_list(matches, service.name(source))
    if total > MAX_LIST:
        text += f"\n\nПоказаны {MAX_LIST} из {total}. Уточните поиск: <code>/live Англия</code> или <code>/live Arsenal</code>"

    rows = [
        [InlineKeyboardButton(f"{m.home} {formatting.score(m)} {m.away}", callback_data=f"lv:{m.source}:{m.id}")]
        for m in matches
    ]
    other = [s for s in service.order if s != source]
    if other:
        rows.append(
            [InlineKeyboardButton(f"Показать с {service.name(s)}", callback_data=f"live:{s}") for s in other]
        )
    await _reply(update, text, InlineKeyboardMarkup(rows) if rows else None)


async def send_live_match(update: Update, context: ContextTypes.DEFAULT_TYPE, source: str, match_id: str) -> None:
    service = _service(context)
    await update.effective_chat.send_action("typing")
    match = await service.match(source, match_id)
    subscribed = update.effective_chat.id in _subs(context).get((source, match_id), {}).get("chats", set())
    text = formatting.format_live_match(match, live_prediction(match), service.name(source))
    await _reply(update, text, match_keyboard(match, subscribed))


async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE, source: str, match_id: str) -> None:
    chat_id = update.effective_chat.id
    subs = _subs(context)
    if sum(chat_id in s["chats"] for s in subs.values()) >= MAX_SUBS_PER_CHAT:
        await _reply(update, f"Можно следить максимум за {MAX_SUBS_PER_CHAT} матчами. Отпишитесь от лишних: /subs")
        return

    key = (source, match_id)
    if key not in subs:
        found = await _service(context).matches(source, [match_id])
        if not found:
            raise LiveSourceError("Матч не найден.")
        if found[0].status == models.FINISHED:
            await _reply(update, "Матч уже закончился.")
            return
        subs[key] = {"chats": set(), "snapshot": found[0], "misses": 0}
    subs[key]["chats"].add(chat_id)
    _save_subs(context)
    m = subs[key]["snapshot"]
    await _reply(
        update,
        f"🔔 Слежу за матчем <b>{escape(m.home)} — {escape(m.away)}</b>.\n"
        "Пришлю голы, удаления, перерыв и итог. Список подписок: /subs",
    )


async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE, source: str, match_id: str) -> None:
    subs = _subs(context)
    entry = subs.get((source, match_id))
    if entry:
        entry["chats"].discard(update.effective_chat.id)
        if not entry["chats"]:
            del subs[(source, match_id)]
        _save_subs(context)
    await _reply(update, "🔕 Больше не слежу за этим матчем.")


# --- Команды и кнопки ---


async def live_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = list(context.args or [])
    source = None
    if args and args[0].lower() in SOURCE_ALIASES:
        source = SOURCE_ALIASES[args.pop(0).lower()]
        if source not in _service(context).sources:
            await _reply(update, "Этот источник не настроен. См. README: API_FOOTBALL_KEY / MYSCORE_ENABLED.")
            return
    await _safe(update, send_live_list(update, context, source, " ".join(args)))


async def subs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    mine = [(key, s["snapshot"]) for key, s in _subs(context).items() if chat_id in s["chats"]]
    if not mine:
        await _reply(update, "Вы ни за чем не следите. Откройте /live и нажмите «🔔 Следить» у матча.")
        return
    lines = ["<b>🔔 Ваши подписки</b>"]
    rows = []
    for (source, match_id), m in mine:
        lines.append(f"{escape(m.home)} <b>{formatting.score(m)}</b> {escape(m.away)}")
        rows.append([InlineKeyboardButton(f"🔕 {m.home} — {m.away}", callback_data=f"lunsub:{source}:{match_id}")])
    await _reply(update, "\n".join(lines), InlineKeyboardMarkup(rows))


async def on_live_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action, _, rest = query.data.partition(":")
    source, _, match_id = rest.partition(":")
    if source not in _service(context).sources:
        return
    if action == "live":
        await _safe(update, send_live_list(update, context, source))
    elif action == "lv":
        await _safe(update, send_live_match(update, context, source, match_id))
    elif action == "lsub":
        await _safe(update, subscribe(update, context, source, match_id))
    elif action == "lunsub":
        await _safe(update, unsubscribe(update, context, source, match_id))


# --- Фоновый опрос подписок ---


async def poll_subscriptions(context: ContextTypes.DEFAULT_TYPE) -> None:
    subs = _subs(context)
    if not subs:
        return
    service = _service(context)

    by_source: dict[str, list[str]] = {}
    for source, match_id in subs:
        by_source.setdefault(source, []).append(match_id)

    for source, ids in by_source.items():
        try:
            fresh = {m.id: m for m in await service.matches(source, ids)}
        except LiveSourceError as exc:
            log.warning("Не удалось обновить подписки %s: %s", source, exc)
            continue

        for match_id in ids:
            key = (source, match_id)
            entry = subs.get(key)
            if entry is None:
                continue
            new = fresh.get(match_id)
            if new is None:
                entry["misses"] += 1
                if entry["misses"] >= MAX_MISSES:
                    del subs[key]
                continue
            entry["misses"] = 0

            for text in formatting.format_notification(entry["snapshot"], new):
                for chat_id in list(entry["chats"]):
                    try:
                        await context.bot.send_message(chat_id, text, parse_mode="HTML")
                    except Forbidden:
                        # Пользователь заблокировал бота — больше ему не пишем
                        entry["chats"].discard(chat_id)
                    except Exception:
                        log.exception("Не удалось отправить уведомление в чат %s", chat_id)
            entry["snapshot"] = new
            if new.status == models.FINISHED or not entry["chats"]:
                del subs[key]

    _save_subs(context)


def register(app: Application, poll_seconds: int) -> None:
    app.add_handler(CommandHandler("live", live_cmd))
    app.add_handler(CommandHandler("subs", subs_cmd))
    app.add_handler(CallbackQueryHandler(on_live_button, pattern=r"^(live|lv|lsub|lunsub):"))
    app.job_queue.run_repeating(poll_subscriptions, interval=poll_seconds, first=poll_seconds)
