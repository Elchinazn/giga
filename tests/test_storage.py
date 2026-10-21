import json

from telegram.error import Forbidden

from bot.live import api_football, handlers, models
from bot.live.storage import SubscriptionStore
from tests.test_live import API_FOOTBALL_FIXTURE, FakeSource, setup


def fixture_match():
    return api_football.parse_fixture(API_FOOTBALL_FIXTURE)


def test_roundtrip_keeps_everything(tmp_path):
    store = SubscriptionStore(str(tmp_path / "subs.json"))
    subs = {("af", "555"): {"chats": {1, 2}, "snapshot": fixture_match(), "misses": 3}}
    store.save(subs)

    loaded = store.load()
    assert loaded.keys() == subs.keys()
    entry = loaded[("af", "555")]
    assert entry["chats"] == {1, 2} and entry["misses"] == 3
    assert entry["snapshot"] == subs[("af", "555")]["snapshot"]  # события и статистика тоже
    assert entry["snapshot"].stat(models.XG) == (2.10, 0.55)


def test_missing_file_means_no_subscriptions(tmp_path):
    assert SubscriptionStore(str(tmp_path / "nope.json")).load() == {}


def test_broken_file_is_set_aside(tmp_path):
    path = tmp_path / "subs.json"
    path.write_text("{not json", encoding="utf-8")
    assert SubscriptionStore(str(path)).load() == {}
    assert not path.exists()
    assert (tmp_path / "subs.json.broken").read_text(encoding="utf-8") == "{not json"


def test_unknown_fields_are_ignored(tmp_path):
    path = tmp_path / "subs.json"
    store = SubscriptionStore(str(path))
    store.save({("ms", "x1"): {"chats": {5}, "snapshot": fixture_match(), "misses": 0}})
    data = json.loads(path.read_text(encoding="utf-8"))
    data[0]["snapshot"]["field_from_future"] = 1
    path.write_text(json.dumps(data), encoding="utf-8")
    assert store.load()[("ms", "x1")]["snapshot"].home == "Arsenal"


def test_save_leaves_no_temp_files(tmp_path):
    store = SubscriptionStore(str(tmp_path / "subs.json"))
    store.save({})
    store.save({})
    assert [p.name for p in tmp_path.iterdir()] == ["subs.json"]


async def test_subscriptions_survive_restart(tmp_path):
    path = str(tmp_path / "subs.json")
    source = FakeSource("af", "API-Football", [fixture_match()])

    # Первый запуск: подписываемся
    update, context, _ = setup(source)
    context.bot_data["subs_store"] = SubscriptionStore(path)
    await handlers.subscribe(update, context, "af", "555")

    # "Перезапуск": новый контекст, подписки читаются из файла
    _, restarted, _ = setup(source)
    store = SubscriptionStore(path)
    restarted.bot_data.update(subs_store=store, subs=store.load())
    assert 100 in restarted.bot_data["subs"][("af", "555")]["chats"]

    # Пока бот был выключен, гости забили — уведомление приходит при первой проверке
    goal = fixture_match()
    goal.away_score = 2
    source._matches = [goal]
    await handlers.poll_subscriptions(restarted)
    assert "ГОЛ" in restarted.bot.sent[0][1]
    assert store.load()[("af", "555")]["snapshot"].away_score == 2

    # Отписка тоже сохраняется
    await handlers.unsubscribe(update, restarted, "af", "555")
    assert store.load() == {}


async def test_blocked_user_is_unsubscribed(tmp_path):
    source = FakeSource("af", "API-Football", [fixture_match()])
    update, context, _ = setup(source)
    context.bot_data["subs_store"] = SubscriptionStore(str(tmp_path / "subs.json"))
    await handlers.subscribe(update, context, "af", "555")

    async def blocked(*args, **kwargs):
        raise Forbidden("bot was blocked by the user")

    context.bot.send_message = blocked
    goal = fixture_match()
    goal.away_score = 2
    source._matches = [goal]
    await handlers.poll_subscriptions(context)
    assert context.bot_data["subs"] == {}
    assert context.bot_data["subs_store"].load() == {}
