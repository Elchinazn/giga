"""Сохранение подписок на матчи в JSON-файл, чтобы они переживали перезапуск бота."""

import dataclasses
import json
import logging
import os
import tempfile

from .models import Event, LiveMatch, StatLine

log = logging.getLogger(__name__)


def _from_dict(cls, data: dict):
    # Лишние поля игнорируем, чтобы старые файлы читались после изменения моделей
    names = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


def match_from_dict(data: dict) -> LiveMatch:
    match = _from_dict(LiveMatch, data)
    match.events = [_from_dict(Event, e) for e in data.get("events", [])]
    match.stats = [_from_dict(StatLine, s) for s in data.get("stats", [])]
    return match


class SubscriptionStore:
    def __init__(self, path: str):
        self.path = path

    def load(self) -> dict:
        """Подписки в формате bot_data["subs"]: (source, match_id) -> {chats, snapshot, misses}."""
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, encoding="utf-8") as f:
                items = json.load(f)
            subs = {}
            for item in items:
                subs[(item["source"], item["id"])] = {
                    "chats": set(item["chats"]),
                    "snapshot": match_from_dict(item["snapshot"]),
                    "misses": item.get("misses", 0),
                }
            return subs
        except (OSError, ValueError, KeyError, TypeError):
            # Не теряем повреждённый файл: откладываем его в сторону и начинаем с чистого списка
            broken = self.path + ".broken"
            log.exception("Не удалось прочитать подписки из %s, файл переименован в %s", self.path, broken)
            os.replace(self.path, broken)
            return {}

    def save(self, subs: dict) -> None:
        items = [
            {
                "source": source,
                "id": match_id,
                "chats": sorted(entry["chats"]),
                "misses": entry["misses"],
                "snapshot": dataclasses.asdict(entry["snapshot"]),
            }
            for (source, match_id), entry in subs.items()
        ]
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        # Пишем во временный файл и подменяем атомарно, чтобы сбой не оставил полфайла
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".subs-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            os.unlink(tmp)
            raise
