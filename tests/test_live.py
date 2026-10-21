from types import SimpleNamespace

import httpx
import pytest

from bot.live import api_football, formatting, handlers, models, myscore
from bot.live.analysis import live_prediction
from bot.live.models import LiveMatch, LiveSourceError, StatLine
from bot.live.service import LiveService

KICKOFF = 1_760_000_000

MYSCORE_FEED = (
    "SA÷1¬~ZA÷АНГЛИЯ: Премьер-лига¬ZEE÷x¬~"
    f"AA÷AbCd1234¬AD÷{KICKOFF}¬AB÷2¬AC÷13¬AE÷Арсенал¬AF÷Челси¬AG÷2¬AH÷1¬AK÷1¬~"
    f"AA÷Zz998877¬AD÷{KICKOFF}¬AB÷2¬AC÷38¬AE÷Ливерпуль¬AF÷Эвертон¬AG÷0¬AH÷0¬~"
    "ZA÷ИСПАНИЯ: Ла Лига¬~"
    f"AA÷Fin00001¬AD÷{KICKOFF}¬AB÷3¬AC÷3¬AE÷Реал¬AF÷Хетафе¬AG÷3¬AH÷0¬~"
    f"AA÷Sch00001¬AD÷{KICKOFF + 9000}¬AB÷1¬AC÷1¬AE÷Барселона¬AF÷Севилья¬~"
    "A1÷end¬~"
)

MYSCORE_STATS = (
    "SE÷Матч¬~SF÷Основное¬~"
    "SD÷1¬SG÷Ожидаемые голы (xG)¬SH÷1.85¬SI÷0.40¬~"
    "SD÷2¬SG÷Владение мячом¬SH÷61%¬SI÷39%¬~"
    "SD÷3¬SG÷Удары в створ¬SH÷6¬SI÷2¬~"
    "SE÷1-й тайм¬~SD÷1¬SG÷Ожидаемые голы (xG)¬SH÷0.9¬SI÷0.1¬~"
)


def test_myscore_feed_parsing():
    now = KICKOFF + 75 * 60  # 75 минут от начала: ~60-я минута второго тайма
    matches = {m.id: m for m in myscore.parse_matches(MYSCORE_FEED, now=now)}
    assert set(matches) == {"AbCd1234", "Zz998877", "Fin00001", "Sch00001"}

    ars = matches["AbCd1234"]
    assert (ars.league, ars.home, ars.away) == ("АНГЛИЯ: Премьер-лига", "Арсенал", "Челси")
    assert (ars.home_score, ars.away_score, ars.away_red) == (2, 1, 1)
    assert ars.status == models.LIVE and ars.minute == 60 and ars.minute_label == "~60'"
    assert ars.url.endswith("/match/AbCd1234/#/match-summary")

    assert matches["Zz998877"].status == models.HALFTIME
    assert matches["Fin00001"].league == "ИСПАНИЯ: Ла Лига"
    assert matches["Fin00001"].status == models.FINISHED
    assert matches["Sch00001"].status == models.SCHEDULED and matches["Sch00001"].home_score is None


def test_myscore_stats_only_whole_match_section():
    stats = myscore.parse_stats(MYSCORE_STATS)
    assert [s.label for s in stats] == ["Ожидаемые голы (xG)", "Владение мячом", "Удары в створ"]
    match = LiveMatch("ms", "1", "", "A", "B", 0, 0, models.LIVE, stats=stats)
    assert match.stat(models.XG) == (1.85, 0.40)
    assert match.stat(models.POSSESSION) == (61, 39)


async def test_myscore_client_sends_signature_and_fetches_stats():
    seen = []

    def handler(request):
        seen.append((request.url.path, request.headers.get("x-fsign")))
        body = MYSCORE_STATS if "df_st_1_" in request.url.path else MYSCORE_FEED
        return httpx.Response(200, text=body)

    client = myscore.MyScore(feed_url="https://feed.test/x/feed", fsign="SIGN")
    client._client = httpx.AsyncClient(headers={"x-fsign": "SIGN"}, transport=httpx.MockTransport(handler))
    live = await client.live_matches()
    assert {m.id for m in live} == {"AbCd1234", "Zz998877"}
    match = await client.match("AbCd1234")
    assert match.stat(models.SHOTS_ON_TARGET) == (6, 2)
    assert seen == [("/x/feed/f_1_0_3_ru_1", "SIGN"), ("/x/feed/df_st_1_AbCd1234", "SIGN")]


async def test_myscore_error_is_readable():
    client = myscore.MyScore()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(LiveSourceError, match="MYSCORE_FSIGN"):
        await client.live_matches()


API_FOOTBALL_FIXTURE = {
    "fixture": {"id": 555, "status": {"short": "2H", "elapsed": 67, "extra": None}},
    "league": {"name": "Premier League", "country": "England"},
    "teams": {"home": {"id": 42, "name": "Arsenal"}, "away": {"id": 49, "name": "Chelsea"}},
    "goals": {"home": 2, "away": 1},
    "events": [
        {"time": {"elapsed": 12, "extra": None}, "team": {"id": 42}, "player": {"name": "Saka"},
         "assist": {"name": "Odegaard"}, "type": "Goal", "detail": "Normal Goal"},
        {"time": {"elapsed": 30, "extra": None}, "team": {"id": 49}, "player": {"name": "Palmer"},
         "assist": {}, "type": "Goal", "detail": "Penalty"},
        {"time": {"elapsed": 45, "extra": 2}, "team": {"id": 49}, "player": {"name": "Caicedo"},
         "assist": {}, "type": "Card", "detail": "Second Yellow card"},
        {"time": {"elapsed": 60, "extra": None}, "team": {"id": 42}, "player": {"name": "Havertz"},
         "assist": {}, "type": "Goal", "detail": "Normal Goal"},
    ],
    "statistics": [
        {"team": {"id": 42}, "statistics": [
            {"type": "Ball Possession", "value": "64%"}, {"type": "Shots on Goal", "value": 7},
            {"type": "expected_goals", "value": "2.10"}]},
        {"team": {"id": 49}, "statistics": [
            {"type": "Ball Possession", "value": "36%"}, {"type": "Shots on Goal", "value": None},
            {"type": "expected_goals", "value": "0.55"}]},
    ],
}


def test_api_football_fixture_parsing():
    m = api_football.parse_fixture(API_FOOTBALL_FIXTURE)
    assert (m.id, m.league, m.home, m.away) == ("555", "England: Premier League", "Arsenal", "Chelsea")
    assert (m.home_score, m.away_score, m.minute, m.minute_label) == (2, 1, 67, "67'")
    assert m.status == models.LIVE and (m.home_red, m.away_red) == (0, 1)
    assert [e.kind for e in m.events] == ["goal", "penalty", "red", "goal"]
    assert m.events[2].minute == "45+2" and m.events[0].detail == "Odegaard"
    assert m.stat(models.XG) == (2.10, 0.55)
    assert m.stat(models.SHOTS_ON_TARGET) == (7, 0)


async def test_api_football_errors_in_body():
    body = {"errors": {"requests": "You have reached the request limit for the day"}, "response": []}
    client = api_football.APIFootball("k", client=httpx.AsyncClient(
        base_url=api_football.BASE_URL, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))))
    with pytest.raises(LiveSourceError, match="request limit"):
        await client.live_matches()


async def test_api_football_batches_ids():
    calls = []

    def handler(request):
        calls.append(request.url.params["ids"])
        return httpx.Response(200, json={"errors": [], "response": []})

    client = api_football.APIFootball("k", client=httpx.AsyncClient(
        base_url=api_football.BASE_URL, transport=httpx.MockTransport(handler)))
    await client.matches([str(i) for i in range(25)])
    assert len(calls) == 2 and calls[0].count("-") == 19


# --- Live-прогноз ---


def live(home_score, away_score, minute, status=models.LIVE, stats=None, home_red=0, away_red=0):
    return LiveMatch("af", "1", "L", "H", "A", home_score, away_score, status, minute=minute,
                     home_red=home_red, away_red=away_red, stats=stats or [])


def total(p):
    return p.home_win + p.draw + p.away_win


def test_live_prediction_sums_to_one_and_respects_score():
    p = live_prediction(live(1, 0, 80))
    assert total(p) == pytest.approx(1.0)
    assert p.home_win > 0.75
    assert p.likely_finals[0][0] == "1:0"


def test_late_draw_stays_draw():
    p = live_prediction(live(0, 0, 89))
    assert p.draw > 0.8
    assert p.no_more_goals > 0.8


def test_early_lead_is_less_certain_than_late_lead():
    assert live_prediction(live(1, 0, 10)).home_win < live_prediction(live(1, 0, 85)).home_win


def test_stats_shift_next_goal():
    dominant = [StatLine("xG", "0.2", "2.4", models.XG)]
    base = live_prediction(live(0, 0, 60))
    shifted = live_prediction(live(0, 0, 60, stats=dominant))
    assert shifted.next_goal_away > base.next_goal_away
    assert shifted.away_win > base.away_win


def test_red_card_helps_opponent():
    base = live_prediction(live(0, 0, 30))
    red = live_prediction(live(0, 0, 30, home_red=1))
    assert red.away_win > base.away_win and red.home_win < base.home_win


def test_no_prediction_when_not_in_play():
    assert live_prediction(live(2, 0, 90, status=models.FINISHED)) is None
    assert live_prediction(live(None, None, None, status=models.SCHEDULED)) is None


def test_halftime_uses_second_half_only():
    p = live_prediction(live(0, 0, None, status=models.HALFTIME))
    assert 0 < p.no_more_goals < 0.5


# --- Уведомления ---


def test_notifications_for_goal_red_card_and_finish():
    old = api_football.parse_fixture(API_FOOTBALL_FIXTURE)
    old.home_score, old.away_red = 1, 0

    new = api_football.parse_fixture(API_FOOTBALL_FIXTURE)
    messages = formatting.format_notification(old, new)
    assert any("ГОЛ" in m and "Arsenal" in m and "60' Havertz" in m for m in messages)
    assert any("Удаление" in m and "Chelsea" in m for m in messages)

    finished = api_football.parse_fixture(API_FOOTBALL_FIXTURE)
    finished.status = models.FINISHED
    assert formatting.format_notification(new, finished) == ["🏁 <b>Матч окончен</b>\nArsenal <b>2:1</b> Chelsea"]


def test_no_notification_without_changes():
    m = api_football.parse_fixture(API_FOOTBALL_FIXTURE)
    assert formatting.format_notification(m, m) == []


def test_live_match_message_has_all_parts():
    m = api_football.parse_fixture(API_FOOTBALL_FIXTURE)
    text = formatting.format_live_match(m, live_prediction(m), "API-Football")
    for part in ("67'", "Arsenal", "2:1", "Saka", "45+2' 🟥", "Владение мячом", "Live-прогноз", "Следующий гол"):
        assert part in text


# --- Источники и обработчики ---


class FakeSource:
    def __init__(self, source, name, matches, fail=False):
        self.source, self.name, self._matches, self.fail = source, name, matches, fail

    async def live_matches(self):
        if self.fail:
            raise LiveSourceError(f"{self.name} down")
        return [m for m in self._matches if m.in_play]

    async def matches(self, ids):
        return [m for m in self._matches if m.id in ids]

    async def match(self, match_id):
        return (await self.matches([match_id]))[0]

    async def close(self):
        pass


async def test_service_falls_back_to_second_source():
    ms_match = live(1, 1, 50)
    service = LiveService([FakeSource("af", "API-Football", [], fail=True), FakeSource("ms", "MyScore", [ms_match])])
    source, matches = await service.live_matches()
    assert source == "ms" and matches == [ms_match]
    with pytest.raises(LiveSourceError):
        await service.live_matches("af")


class FakeMessage:
    def __init__(self):
        self.sent = []

    async def reply_text(self, text, **kwargs):
        self.sent.append((text, kwargs.get("reply_markup")))


class FakeChat:
    id = 100

    async def send_action(self, action):
        pass


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


def setup(source):
    message = FakeMessage()
    update = SimpleNamespace(effective_message=message, effective_chat=FakeChat(), callback_query=None)
    context = SimpleNamespace(bot_data={"live": LiveService([source])}, args=[], bot=FakeBot())
    return update, context, message


async def test_live_list_filter_and_buttons():
    matches = [api_football.parse_fixture(API_FOOTBALL_FIXTURE), live(0, 0, 10)]
    update, context, message = setup(FakeSource("af", "API-Football", matches))
    context.args = ["arsenal"]
    await handlers.live_cmd(update, context)
    text, keyboard = message.sent[0]
    assert "Матчи в прямом эфире: 1" in text and "Arsenal" in text
    assert keyboard.inline_keyboard[0][0].callback_data == "lv:af:555"


async def test_subscribe_poll_and_unsubscribe():
    match = api_football.parse_fixture(API_FOOTBALL_FIXTURE)
    source = FakeSource("af", "API-Football", [match])
    update, context, message = setup(source)

    await handlers.subscribe(update, context, "af", "555")
    assert "Слежу за матчем" in message.sent[-1][0]

    # Ничего не изменилось — уведомлений нет
    await handlers.poll_subscriptions(context)
    assert context.bot.sent == []

    # Гол гостей, затем финальный свисток
    goal = api_football.parse_fixture(API_FOOTBALL_FIXTURE)
    goal.away_score = 2
    source._matches = [goal]
    await handlers.poll_subscriptions(context)
    assert len(context.bot.sent) == 1 and "ГОЛ" in context.bot.sent[0][1] and context.bot.sent[0][0] == 100

    final = api_football.parse_fixture(API_FOOTBALL_FIXTURE)
    final.away_score, final.status = 2, models.FINISHED
    source._matches = [final]
    await handlers.poll_subscriptions(context)
    assert "Матч окончен" in context.bot.sent[-1][1]
    assert context.bot_data["subs"] == {}  # подписка снята после финала


async def test_unsubscribe_removes_chat():
    source = FakeSource("af", "API-Football", [api_football.parse_fixture(API_FOOTBALL_FIXTURE)])
    update, context, message = setup(source)
    await handlers.subscribe(update, context, "af", "555")
    await handlers.unsubscribe(update, context, "af", "555")
    assert context.bot_data["subs"] == {}
