"""Чат поверх клипа и вертикальный формат: разбор сообщений Twitch, отбор сообщений, раскладка кадра."""
import random

from streamclipper import chatrender, emotes, vrender
from streamclipper.chat import ChatClient, parse_emotes, parse_tags


def test_tags_and_emotes():
    t = parse_tags(r"@badge-info=;color=#FF0000;display-name=Вася\sПупкин;emotes=25:0-4,12-16/1902:6-10;id=abc;room-id=123")
    assert t["display-name"] == "Вася Пупкин" and t["color"] == "#FF0000" and t["room-id"] == "123"
    assert parse_emotes(t["emotes"]) == [[0, 4, "25"], [6, 10, "1902"], [12, 16, "25"]]
    assert parse_emotes("") == [] and parse_emotes("мусор") == []


def test_chat_client_messages_and_moderation():
    got, gone = [], []
    c = ChatClient("x", lambda *a: got.append(a), lambda u, i: gone.append((u, i)))
    c._on_line(None, "@color=#1E90FF;display-name=Foo;emotes=25:0-4;id=m1;room-id=77 "
                     ":foo!foo@foo.tmi.twitch.tv PRIVMSG #x :Kappa привет", 1.0)
    c._on_line(None, ":bar!bar@bar.tmi.twitch.tv PRIVMSG #x :без тегов", 2.0)
    c._on_line(None, "@ban-duration=600 :tmi.twitch.tv CLEARCHAT #x :foo", 3.0)
    c._on_line(None, "@login=bar;target-msg-id=m2 :tmi.twitch.tv CLEARMSG #x :текст", 4.0)
    assert got[0] == (1.0, "foo", "Kappa привет", {"name": "Foo", "color": "#1E90FF", "emotes": [[0, 4, "25"]], "id": "m1"})
    assert got[1][1:3] == ("bar", "без тегов") and got[1][3]["name"] == "bar"
    assert gone == [("foo", ""), ("bar", "m2")]
    assert c.room_id == "77" and c.connected


def _chat(seconds=40, hot=(20, 30)):
    rng = random.Random(1)
    log = []
    for s in range(seconds):
        for _ in range(35 if hot[0] <= s < hot[1] else 7):
            log.append([s + rng.random(), f"user{rng.randint(1, 300)}", "", rng.choice(["АХАХАХ", "привет", "ору", "KEKW", "что"]), []])
    return sorted(log)


def test_select_filters_and_paces():
    log = _chat()
    log += [[12.0, "Nightbot", "", "правила чата", []], [12.1, "vasya", "", "заходи на cheap-skins.com", []],
            [12.2, "vasya", "", "!drop", []], [12.3, "vasya", "", "это ЗАПРЕТНОЕ слово", []]]
    sel = chatrender.select(sorted(log), 10.0, 25.0, ["запретное"])
    texts = [m["text"] for m in sel]
    assert not any("правила" in x or "cheap" in x or x.startswith("!") or "ЗАПРЕТНОЕ" in x for x in texts)
    assert -chatrender.PRE <= sel[0]["t"] < 0 and sel[-1]["t"] <= 25.0           # чат не пустой с первой секунды
    assert all(b["t"] - a["t"] >= 0.119 for a, b in zip(sel, sel[1:]))           # идут по порядку и не слипаются
    calm = sum(1 for m in sel if 0 <= m["t"] < 8) / 8
    hot = sum(1 for m in sel if 11 <= m["t"] < 19) / 8
    assert 1.5 <= calm <= 3.6 and calm < hot <= chatrender.MAX_RATE + 0.2        # живой чат заметно быстрее, но читаем
    assert chatrender.select([], 0, 10) == [] and chatrender.select([["плохо"]], 0, 10) == []


def test_tokenize_emotes():
    toks = chatrender.tokenize("Kappa ну KEKW да 😂", [[0, 4, "25"]], {"KEKW": "https://x/kekw"}, emotes.twitch_url)
    assert toks[0] == ("emote", emotes.twitch_url("25"), "Kappa")
    assert toks[2] == ("emote", "https://x/kekw", "KEKW")
    assert [t[1] for t in toks if t[0] == "text"] == ["ну", "да", "😂"]
    # позиция смайла не совпала со словом: остаётся текстом, клип не ломается
    assert chatrender.tokenize("привет Kappa", [[3, 9, "25"]], {}, emotes.twitch_url) == [("text", "привет"), ("text", "Kappa")]


def test_nick_color_readable():
    import colorsys
    for name, color in (("vasya", "#0000FF"), ("petya", ""), ("x", "мусор"), ("y", "#000000")):
        r, g, b = chatrender.nick_color(name, color)
        assert colorsys.rgb_to_hls(r / 255, g / 255, b / 255)[1] >= 0.6
    assert chatrender.nick_color("a", "") == chatrender.nick_color("a", "")       # цвет зрителя не скачет


def test_emote_lists_parsing():
    seven = [{"name": "KEKW", "data": {"host": {"url": "//cdn.7tv.app/emote/1"}}},
             {"name": "RainTime", "data": {"flags": 256, "host": {"url": "//cdn.7tv.app/emote/2"}}}, {"oops": 1}]
    assert emotes.parse_7tv(seven) == {"KEKW": "https://cdn.7tv.app/emote/1/2x.webp"}
    assert emotes.parse_bttv([{"id": "ab", "code": "catJAM"}, {}]) == {"catJAM": "https://cdn.betterttv.net/emote/ab/2x"}
    ffz = {"default_sets": [3], "sets": {"3": {"emoticons": [{"name": "ZrehplaR", "urls": {"1": "//cdn.ffz/1", "2": "//cdn.ffz/2"}}]},
                                         "9": {"emoticons": [{"name": "Other", "urls": {"1": "https://cdn.ffz/9"}}]}}}
    assert emotes.parse_ffz(ffz) == {"ZrehplaR": "https://cdn.ffz/2", "Other": "https://cdn.ffz/9"}
    assert emotes.parse_ffz(ffz, {"3"}) == {"ZrehplaR": "https://cdn.ffz/2"}
    assert emotes.parse_7tv(None) == {} and emotes.parse_ffz({}) == {}


def test_vertical_geometry():
    g = vrender.geometry("blur", 1920, 1080, chat=True)
    x, y, w, h = g["chat"]
    assert g["mode"] == "blur" and g["fg_h"] == 608
    assert y >= g["vy"] + g["fg_h"] and y + h <= vrender.H - 400        # чат под видео, низ свободен под подписи TikTok
    assert x + w <= vrender.W - 100                                      # справа место под кнопки TikTok
    assert vrender.geometry("blur", 1920, 1080, chat=False)["vy"] == (1920 - 608) // 2
    assert vrender.geometry("crop", 1920, 1080, chat=True)["mode"] == "crop"
    assert vrender.geometry("blur", 1080, 1920, chat=True)["mode"] == "fit"      # стрим с телефона уже вертикальный
    for geo, chat in ((g, True), (vrender.geometry("crop", 1280, 720, True), True), (vrender.geometry("blur", 1280, 720, False), False)):
        graph = vrender.filter_graph(geo, 60.0, chat)
        assert graph.endswith("format=yuv420p[v]") and ("[1:v]overlay" in graph) == chat
    assert "fps=60" in vrender.filter_graph(g, 120.0, False) and "fps=60" not in vrender.filter_graph(g, 60.0, False)
