"""Оценки клипов в Telegram-канале: реакции включаются кнопкой, считаются голоса всех подписчиков."""
import asyncio
from types import SimpleNamespace

import pytest
from telethon import errors
from telethon.tl import functions, types

from streamclipper.uploader import Telegram


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STREAMCLIPPER_HOME", str(tmp_path / "home"))
    return tmp_path


class _Client:
    """Канал понарошку: помнит, какие реакции в нём разрешены и что стоит под постами."""

    def __init__(self, allowed=None, refuse=None, posts=None):
        self.allowed, self.refuse, self.posts = allowed, refuse, posts or {}
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        if isinstance(request, functions.channels.GetFullChannelRequest):
            return SimpleNamespace(full_chat=SimpleNamespace(available_reactions=self.allowed))
        if isinstance(request, functions.messages.SetChatAvailableReactionsRequest):
            if self.refuse:
                raise self.refuse
            self.allowed = request.available_reactions
            return None
        raise AssertionError(f"неожиданный запрос {request}")

    async def get_messages(self, target, ids):
        return [self.posts.get(i) for i in ids]


def _tg(client, target="peer:channel"):
    tg = Telegram(lambda ev: None)
    tg._client, tg._target, tg._chat_key = client, target, "chan"
    tg._cfg = {"tg_enabled": True, "tg_mode": "user"}
    return tg


def _post(**counts):
    """Пост с реакциями: _post(**{"👍": 3, "👎": 1})."""
    return SimpleNamespace(reactions=SimpleNamespace(results=[
        SimpleNamespace(reaction=types.ReactionEmoji(emoticon=e), count=n) for e, n in counts.items()]))


def test_program_sees_whether_reactions_are_allowed_in_the_channel(home):
    assert asyncio.run(_tg(_Client(allowed=None))._reactions_state()) == "off"
    assert asyncio.run(_tg(_Client(allowed=types.ChatReactionsNone()))._reactions_state()) == "off"
    assert asyncio.run(_tg(_Client(allowed=types.ChatReactionsSome(reactions=[])))._reactions_state()) == "off"
    assert asyncio.run(_tg(_Client(allowed=types.ChatReactionsAll()))._reactions_state()) == "on"
    some = types.ChatReactionsSome(reactions=[types.ReactionEmoji(emoticon="🔥")])
    assert asyncio.run(_tg(_Client(allowed=some))._reactions_state()) == "on"
    assert asyncio.run(_tg(_Client(), target=None)._reactions_state()) == ""      # канал не выбран: неизвестно
    assert asyncio.run(_tg(object())._reactions_state()) == ""                    # Telegram не ответил: не падаем


def test_button_turns_on_thumbs_up_and_down(home):
    client = _Client(allowed=types.ChatReactionsNone())
    tg = _tg(client)
    assert asyncio.run(tg._enable_reactions()) == ""
    assert tg.votes == "on"
    assert [r.emoticon for r in client.allowed.reactions] == ["👍", "👎"]
    asked = [r for r in client.requests if isinstance(r, functions.messages.SetChatAvailableReactionsRequest)]
    assert len(asked) == 1 and asked[0].peer == "peer:channel"


def test_button_explains_when_the_account_may_not_change_the_channel(home):
    tg = _tg(_Client(allowed=types.ChatReactionsNone(), refuse=errors.ChatAdminRequiredError(request=None)))
    why = asyncio.run(tg._enable_reactions())
    assert "нет прав" in why and "Реакции" in why and tg.votes == ""
    tg = _tg(_Client(allowed=types.ChatReactionsAll(), refuse=errors.ChatNotModifiedError(request=None)))
    assert asyncio.run(tg._enable_reactions()) == "" and tg.votes == "on"       # «уже включено» не ошибка
    assert "не подключён" in asyncio.run(_tg(None)._enable_reactions())


def test_votes_of_all_subscribers_are_counted(home):
    """Не только владелец: под клипом голосуют все, побеждает большинство. Удалённый пост = клип плохой."""
    tg = _tg(_Client(posts={
        11: _post(**{"👍": 3, "👎": 1}),                 # трое за, один против: хороший
        12: _post(**{"👎": 2, "🔥": 1}),                 # большинство против: плохой
        13: _post(**{"👍": 1, "👎": 1}),                 # поровну: пока без оценки
        14: _post(**{"🤔": 5}),                          # посторонние смайлы не считаются
        15: SimpleNamespace(reactions=None),             # никто не голосовал
    }))                                                  # пост 16 удалили
    for n in range(11, 17):
        tg.queue.add(f"clip{n}.mp4", "")
        row = tg.queue.next()
        tg.queue.mark(row["id"], "sent")
        tg.queue.set_msg(row["id"], n, "chan")
    seen = []
    tg.on_event = seen.append
    asyncio.run(tg._read_feedback(tg._client))
    marks = {ev["path"]: ev["good"] for ev in seen if ev["t"] == "reaction"}
    assert marks == {"clip11.mp4": True, "clip12.mp4": False, "clip16.mp4": False}
    seen.clear()
    asyncio.run(tg._read_feedback(tg._client))           # те же оценки второй раз не присылаются
    assert seen == []
