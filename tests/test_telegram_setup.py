"""Настройка Telegram: какой канал запоминается и что делает кнопка «Отправить проверочное сообщение»."""
import asyncio

import pytest
from telethon import errors

from streamclipper.uploader import Telegram, pick_channel, summarize_test


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STREAMCLIPPER_HOME", str(tmp_path / "home"))
    return tmp_path


class _Client:
    """Telegram понарошку: помнит, что и куда отправили, и умеет отказывать."""

    def __init__(self, refuse=None, unknown=()):
        self.sent: list[tuple[object, str]] = []
        self.refuse = refuse or {}
        self.unknown = set(unknown)

    async def send_message(self, target, text):
        if target in self.refuse:
            raise self.refuse[target]
        self.sent.append((target, text))

    async def get_input_entity(self, spec):
        if spec in self.unknown:
            raise ValueError(f'No user has "{spec}" as username')
        return f"peer:{spec}"

    async def get_dialogs(self, limit=0):
        return []


def _tg(client, target="peer:channel", **cfg):
    tg = Telegram(lambda ev: None)
    tg._client, tg._target = client, target
    tg._cfg = dict({"tg_enabled": True, "tg_mode": "user", "tg_alerts": True, "tg_alerts_chat": ""}, **cfg)
    tg.status, tg.status_text = ("ok", "подключён") if target else ("no_channel", "канал не найден: нет такого")
    return tg


def test_test_message_goes_to_the_clip_channel_and_to_the_alerts_chat(home):
    """Раньше проверочное сообщение шло только в уведомления, и канал для клипов оставался непроверенным."""
    client = _Client()
    res = asyncio.run(_tg(client, tg_alerts_chat="@my_chat")._send_test())
    assert res == [("Канал для клипов", ""), ("Уведомления (@my_chat)", "")]
    assert [t for t, _text in client.sent] == ["peer:channel", "peer:@my_chat"]
    assert "сюда будут приходить клипы" in client.sent[0][1]
    assert summarize_test(res) == ("Канал для клипов: отправлено ✅\nУведомления (@my_chat): отправлено ✅", False)


def test_test_message_without_alerts_chat_goes_to_saved_messages(home):
    client = _Client()
    res = asyncio.run(_tg(client)._send_test())
    assert res == [("Канал для клипов", ""), ("Уведомления («Избранное»)", "")]
    assert [t for t, _text in client.sent] == ["peer:channel", "me"]


def test_test_message_says_why_the_channel_did_not_get_it(home):
    client = _Client(refuse={"peer:channel": errors.ChatWriteForbiddenError(request=None)})
    res = asyncio.run(_tg(client)._send_test())
    assert res[0][0] == "Канал для клипов" and "нет прав писать" in res[0][1]
    assert res[1] == ("Уведомления («Избранное»)", "")              # уведомления при этом дошли
    text, bad = summarize_test(res)
    assert bad and "Канал для клипов: не получилось ❌ нет прав писать" in text

    client = _Client(refuse={"peer:channel": errors.ChannelPrivateError(request=None)})
    assert "не состоит в этом канале" in asyncio.run(_tg(client)._send_test())[0][1]


def test_test_message_reports_a_channel_that_was_not_found(home):
    client = _Client()
    res = asyncio.run(_tg(client, target=None)._send_test())
    assert res[0] == ("Канал для клипов", "канал не найден: нет такого")
    assert [t for t, _text in client.sent] == ["me"]                # в канал не писали, в уведомления написали


def test_test_message_reports_a_wrong_alerts_chat_and_skips_disabled_alerts(home):
    client = _Client(unknown={"@typo"})
    res = asyncio.run(_tg(client, tg_alerts_chat="@typo")._send_test())
    assert res[0] == ("Канал для клипов", "") and res[1][0] == "Уведомления (@typo)" and "чат не найден" in res[1][1]

    client = _Client()
    res = asyncio.run(_tg(client, tg_alerts=False)._send_test())
    assert res == [("Канал для клипов", "")] and len(client.sent) == 1
    assert "Уведомления выключены" in summarize_test(res, alerts_on=False)[0]

    res = asyncio.run(_tg(_Client(), tg_mode="bot")._send_test())    # боту «Избранное» недоступно
    assert res[1][0] == "Уведомления" and "указать чат" in res[1][1]


def test_channel_from_the_list_is_remembered_by_its_number():
    mine = [("мемы", "-1001"), ("gta 6 reels", "-1002"), ("Тест", "-1003")]
    assert pick_channel("gta 6 reels", mine, 1) == ("-1002", "gta 6 reels")
    # название набрали руками (поле само дописало его по первой букве), а строку в списке не выбрали
    assert pick_channel(" GTA 6 Reels ", mine, 0) == ("-1002", "gta 6 reels")
    assert pick_channel("gta 6 reels", mine + [("gta 6 reels", None)], 3) == ("-1002", "gta 6 reels")
    # @имя и ссылки берутся как есть
    assert pick_channel("@my_channel", mine, 0) == ("@my_channel", "")
    assert pick_channel("https://t.me/my_channel", [], -1) == ("https://t.me/my_channel", "")
    assert pick_channel("", mine, -1) == ("", "")
    # два канала с одинаковым названием: гадать нельзя, пусть человек выберет из списка
    twins = [("клипы", "-1"), ("клипы", "-2")]
    assert pick_channel("клипы", twins, -1) == ("клипы", "")
    assert pick_channel("клипы", twins, 1) == ("-2", "клипы")
    # после перезапуска в поле стоит сохранённое название, а за ним номер канала
    assert pick_channel("gta 6 reels", [("gta 6 reels", "-1002")], 0) == ("-1002", "gta 6 reels")
