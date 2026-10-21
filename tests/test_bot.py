"""Проверка клиента API и обработчиков без реальной сети и Telegram."""

from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from bot import handlers
from bot.football_api import BASE_URL, FootballAPI, FootballAPIError
from tests.test_analysis import make_match, standings_with_home_away


def api_with(handler) -> tuple[FootballAPI, list]:
    calls = []

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return handler(request)

    client = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(transport))
    return FootballAPI("key", client=client), calls


async def test_responses_are_cached():
    api, calls = api_with(lambda r: httpx.Response(200, json={"scorers": [{"goals": 1}]}))
    await api.scorers("PL")
    await api.scorers("PL")
    assert calls == ["/v4/competitions/PL/scorers"]


@pytest.mark.parametrize("status", [403, 404, 429, 500])
async def test_http_errors_become_readable(status):
    api, _ = api_with(lambda r: httpx.Response(status))
    with pytest.raises(FootballAPIError):
        await api.standings("PL")


class FakeMessage:
    def __init__(self):
        self.sent = []

    async def reply_text(self, text, **kwargs):
        self.sent.append((text, kwargs.get("reply_markup")))


class FakeChat:
    async def send_action(self, action):
        pass


class FakeAPI:
    def __init__(self, fail_h2h=False):
        self.fail_h2h = fail_h2h

    async def match(self, match_id):
        m = make_match(1, 2, None, None, match_id=match_id)
        m.update(status="TIMED", competition={"code": "PL", "name": "Premier League"})
        return m

    async def standings(self, code):
        return standings_with_home_away()

    async def head2head(self, match_id):
        if self.fail_h2h:
            raise FootballAPIError("limit")
        return [make_match(1, 2, 2, 2), make_match(2, 1, 0, 1)]

    async def team_matches(self, team_id):
        return [make_match(team_id, 9, 2, 0, date=f"2026-10-0{i}T15:00:00Z") for i in range(1, 6)]

    async def upcoming_matches(self, code):
        m = make_match(1, 2, None, None, match_id=42)
        m["status"] = "TIMED"
        return [m]


def fake_context(api, args=None):
    return SimpleNamespace(bot_data={"api": api, "tz": ZoneInfo("UTC")}, args=args or [])


def fake_update():
    message = FakeMessage()
    return SimpleNamespace(effective_message=message, effective_chat=FakeChat(), callback_query=None), message


async def test_analysis_message_contains_all_sections():
    update, message = fake_update()
    await handlers.analyze_cmd(update, fake_context(FakeAPI(), ["777"]))
    text = message.sent[0][0]
    assert "T1 — T2" in text
    assert "Личные встречи (последние 2)" in text
    assert "T1 1 — ничьи 1 — 0 T2" in text
    assert "П1:" in text and "Обе забьют" in text


async def test_analysis_survives_missing_h2h():
    update, message = fake_update()
    await handlers.analyze_cmd(update, fake_context(FakeAPI(fail_h2h=True), ["777"]))
    assert "Личные встречи:</b> нет данных" in message.sent[0][0]


async def test_matches_command_has_analyze_buttons():
    update, message = fake_update()
    await handlers.matches_cmd(update, fake_context(FakeAPI(), ["pl"]))
    text, keyboard = message.sent[0]
    assert "T1 — T2" in text
    assert keyboard.inline_keyboard[0][0].callback_data == "an:42"


async def test_unknown_league_shows_league_picker():
    update, message = fake_update()
    await handlers.table_cmd(update, fake_context(FakeAPI(), ["XYZ"]))
    assert "код лиги" in message.sent[0][0]


def test_find_team_prefers_exact_match():
    teams = [
        {"name": "Manchester United FC", "shortName": "Man United", "tla": "MUN"},
        {"name": "Manchester City FC", "shortName": "Man City", "tla": "MCI"},
    ]
    assert handlers.find_team(teams, "mci")["tla"] == "MCI"
    assert handlers.find_team(teams, "city")["tla"] == "MCI"
    assert handlers.find_team(teams, "Real") is None
