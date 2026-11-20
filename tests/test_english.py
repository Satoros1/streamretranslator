"""Английские стримы: чат, язык канала, речь и описание клипа."""
import json
import random
import shutil
import subprocess
import sys
import time
import types

import pytest

from streamclipper import config, describe
from streamclipper.detector import Detector, Event
from streamclipper.learning import learnable
from streamclipper.lexicon import LangGuess, classify, message_lang, normalize

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нужен ffmpeg")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STREAMCLIPPER_HOME", str(tmp_path / "home"))
    return tmp_path


# ---- чат ----

def test_english_laughter():
    for s in ["LMAOOOO", "lmao", "lmfao", "LMFAOOO", "hahahaha", "HAHAHA", "hehehe", "I'm dead 💀", "im dead",
              "imdead", "im crying", "I'm CRYING", "😭😭😭", "DEAD", "crying", "ICANT", "i cant", "KEKW", "KEKL",
              "OMEGALUL", "LULW", "lol", "LOOOL", "LOLW", "xD", "+2", "this is hilarious", "pepeLaugh", "ROFL",
              "bro is done", "HELP 😭", "💀💀💀"]:
        assert classify(s)[0], s


def test_english_hype_and_clip_requests():
    for s in ["NO WAY", "no shot", "HOLY SHIT", "LETS GOOO", "bro what", "NAHHH", "nah", "WHAT", "what?",
              "chat is this real", "W", "W W W", "L", "massive W", "insane", "YOOOO", "NOOOO", "BRUH", "EZ",
              "EZ Clap", "Pog", "PogU", "WTF", "what the hell", "sheesh", "PEAK", "CINEMA", "huh", "Clueless"]:
        assert classify(s)[1], s
    for s in ["clip it", "CLIP THAT", "clipped", "LSF", "this is going to LSF", "moment of the year", "tiktok"]:
        assert classify(s)[2], s


def test_ordinary_english_chat_is_not_a_reaction():
    for s in ["what game is this", "how long is the stream", "hello chat", "is he playing tonight", "freeze",
              "Lopez", "the boss is dead now", "I can't see the map", "good morning", "where is the shop",
              "!play", "Hello everyone", "when is the next stream", "he should go left", "what rank is he",
              "who is that", "hmm", "okay", "help me with the settings please", "whole", "hold on"]:
        assert classify(s) == (False, False, False), s


def test_english_laughter_is_counted_as_one_phrase():
    assert normalize("HAHAHAHA") == normalize("hahaha") == "HAHAHA"
    assert normalize("LMAOOOOO") == normalize("lmao") == "LMAO"
    assert normalize("АХАХАХАХ") == normalize("хахах") == "АХАХАХ"      # русский смех остаётся русским


def test_learned_words_skip_common_english():
    for w in ["the", "gonna", "chat", "really", "something", "dead", "lmao", "kekw"]:
        assert not learnable(w), w
    for w in ["mizkif", "ratjam", "xqcl", "штанишки"]:
        assert learnable(w), w


def _feed(det, rng, t0, seconds, rate, laugh_share=0.05):
    calm = ["hi chat", "what game is this", "how long has he been live", "this map is so big", "yes",
            "when is the tournament", "what song is this?", "hello from Texas", "he should go left"]
    fun = ["LMAOOOO", "hahahaha", "KEKW", "I'm dead 💀", "OMEGALUL", "what is he saying LMAO", "😭😭😭", "clip it"]
    events = []
    for s in range(seconds):
        t = t0 + s
        n = max(0, int(rng.gauss(rate, rate ** 0.5)))
        for i in range(n):
            text = rng.choice(fun) if rng.random() < laugh_share else rng.choice(calm)
            det.add_message(t + i / (n + 1), f"u{rng.randint(1, 999)}", text)
        ev = det.tick(t + 1.0)
        if ev:
            events.append(ev)
    return events


def test_english_chat_spike_gives_one_clip():
    rng = random.Random(3)
    det = Detector(3)
    t0 = 3_000_000.0
    assert _feed(det, rng, t0, 300, 12) == []                    # обычный английский чат: клипов нет
    ev = _feed(det, rng, t0 + 300, 20, 45, laugh_share=0.8)      # 20 секунд смеха
    ev += _feed(det, rng, t0 + 320, 60, 12)
    assert len(ev) == 1, ev
    assert ev[0].kind == "laugh" and ev[0].score >= 60
    assert set(ev[0].top) & {"LMAO", "HAHAHA", "KEKW", "OMEGALUL"}


# ---- язык канала ----

def test_message_language():
    assert message_lang("what is he doing") == "en"
    assert message_lang("is this the new map?") == "en"
    assert message_lang("привет всем") == "ru"
    assert message_lang("ору с него KEKW") == "ru"
    # смайлы и словечки латиницей пишет и русский чат: по ним язык не понять
    for s in ["KEKW", "lol", "ok", "bro", "LMAO", "!play", "W", ""]:
        assert message_lang(s) == "", s


def _chat(lang, n, rng):
    ru = ["привет", "какая игра", "ору", "стример топ", "го катку", "а что за музыка?", "KEKW", "LUL", "ok", "lol",
          "ахахах", "он что творит", "gg", "Pog"]
    en = ["what game is this", "hi chat", "he is so bad", "KEKW", "LUL", "LMAO", "W", "is this the new map",
          "I think he won", "Pog", "привет из Москвы", "lol"]
    words = ru if lang == "ru" else en
    return [rng.choice(words) for _ in range(n)]


def test_language_is_guessed_from_chat():
    rng = random.Random(5)
    for lang in ("ru", "en"):
        g = LangGuess()
        for text in _chat(lang, 30, rng):
            g.add(text)
        assert g.lang == ""                              # сообщений мало: не торопится
        for text in _chat(lang, 600, rng):
            g.add(text)
        assert g.lang == lang
    # русский стример позвал англоязычных гостей: чат смешался, но русского всё ещё много
    g = LangGuess()
    for text in _chat("ru", 400, rng) + _chat("en", 150, rng):
        g.add(text)
    assert g.lang == "ru"


def test_worker_language_comes_from_chat_unless_set(home):
    from streamclipper.worker import Worker
    w = Worker("ch")
    assert w.lang() == ""
    for i in range(200):
        w._on_chat(time.time(), f"u{i}", "what is he doing LMAO")
    assert w.lang() == "en"
    w.cfg = dict(w.cfg, stream_lang="ru")                # в настройках сказано «все на русском»
    assert w.lang() == "ru"


def test_moment_carries_the_language(home, monkeypatch):
    from streamclipper import segments
    from streamclipper.worker import Worker
    w = Worker("ch")
    sent = []
    w.emit = lambda **m: sent.append(m)
    for i in range(200):
        w._on_chat(time.time(), f"u{i}", "is this the new map")
    now = time.time()
    segs = []
    for i in range(30):
        p = w.buf_dir / f"piece{i}.ts"
        p.write_bytes(b"x" * 1000)
        segs.append(segments.Segment(str(p), 1, now - 120 + i * 4, now - 116 + i * 4, 1000))
    monkeypatch.setattr("streamclipper.worker.segments.scan", lambda folder: segs)
    ev = Event(onset=now - 40, peak=now - 35, end=now - 25, score=80, peak_z=12.0, share=0.7, kind="laugh",
               messages=120, features={"z": 12.0, "share": 0.7, "laugh": 1.0, "dur": 15.0, "msgs": 120, "uniq": 0.8})
    w._make_candidate(ev)
    msg = next(m for m in sent if m["t"] == "candidate")
    with open(msg["job"], encoding="utf-8") as f:
        assert json.load(f)["lang"] == "en"


def test_language_settings_have_safe_defaults(home):
    cfg = config.load()
    assert cfg["stream_lang"] == "auto" and cfg["descr_lang"] == "ru"


# ---- описание клипа ----

def test_title_and_caption_language():
    job = {"channel": "xqc", "kind": "laugh", "lang": "en", "peak_z": 11.6, "top": ["LMAO", "KEKW"], "title": "GTA"}
    assert describe.out_lang(job, {"descr_lang": "ru"}) == "ru"           # по умолчанию по-русски
    assert describe.out_lang(job, {"descr_lang": "stream"}) == "en"
    assert describe.out_lang(dict(job, lang=""), {"descr_lang": "stream"}) == "ru"
    assert describe.heuristic(job, [], "ru")[0] == "Чат разрывает от смеха"
    assert describe.heuristic(job, [], "en")[0] == "Chat can't stop laughing"
    assert describe.heuristic(dict(job, kind="hype"), [], "en")[0] == "Chat is shocked"
    text = describe.caption(job, "Chat can't stop laughing", "", "", 81, 37.6, "20.11 18:00", "en")
    assert "chat ×12" in text and "38 s" in text
    assert "чат ×12" in describe.caption(job, "Чат разрывает от смеха", "", "", 81, 37.6, "20.11 18:00")


class _FakeAnthropic:
    def __init__(self):
        self.calls = []
        outer = self

        class Messages:
            def parse(self, **kw):
                outer.calls.append(kw)
                v = {"keep": True, "start": 5.0, "end": 40.0, "title": "t", "description": "d", "score": 70}
                return types.SimpleNamespace(stop_reason="end_turn", parsed_output=kw["output_format"](**v))

        class Client:
            def __init__(self, **kw):
                self.messages = Messages()

        self.module = types.ModuleType("anthropic")
        self.module.Anthropic = Client


AI_CFG = {"ai_enabled": True, "ai_api_key": "sk-test", "ai_model": "", "ai_frames": False, "clip_min": 30,
          "clip_max": 45}


def test_ai_gets_the_stream_language(home, monkeypatch):
    fake = _FakeAnthropic()
    monkeypatch.setitem(sys.modules, "anthropic", fake.module)
    job = {"channel": "xqc", "title": "GTA 6", "category": "GTA VI", "kind": "laugh", "raw": "", "lang": "en",
           "onset": 30.0, "peak": 34.0, "end": 40.0, "chat": [[31.0, "jess", "LMAOOO"], [33.0, "tom", "I'm dead 💀"]]}
    said = [(28.0, 31.0, "bro stole a wheelchair")]
    # английский стрим, описание по-русски (так по умолчанию)
    assert describe.ai_verdict(job, said, 0.0, 60.0, (5.0, 40.0), dict(AI_CFG, descr_lang="ru"))
    call = fake.calls[-1]
    assert call["system"] == describe.SYSTEM_EN_RU
    assert "англоязычных" in call["system"] and "по-русски" in call["system"]
    assert "СТРИМЕР ГОВОРИЛ" in call["messages"][0]["content"]
    # английский стрим, описание как у стрима: и задание, и запрос по-английски
    assert describe.ai_verdict(job, said, 0.0, 60.0, (5.0, 40.0), dict(AI_CFG, descr_lang="stream"))
    call = fake.calls[-1]
    text = call["messages"][0]["content"]
    assert call["system"] == describe.SYSTEM_EN and "in English" in call["system"]
    assert "THE STREAMER SAID" in text and "bro stole a wheelchair" in text and "Preliminary pick: 5-40" in text
    assert "СТРИМЕР" not in text
    # русский стрим: всё как раньше
    describe.ai_verdict(dict(job, lang="ru"), said, 0.0, 60.0, (5.0, 40.0), dict(AI_CFG, descr_lang="stream"))
    assert fake.calls[-1]["system"] == describe.SYSTEM


@needs_ffmpeg
def test_speech_is_recognized_in_the_stream_language(home, monkeypatch, tmp_path):
    raw = tmp_path / "tone.wav"
    made = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                           "sine=frequency=440:duration=4", str(raw)])
    assert made.returncode == 0
    asked = []

    class Model:
        def __init__(self, *a, **kw):
            pass

        def transcribe(self, audio, **kw):
            asked.append(kw["language"])
            seg = types.SimpleNamespace(start=0.5, end=2.0, text=" no way he did that ", no_speech_prob=0.1)
            junk = types.SimpleNamespace(start=2.0, end=3.0, text="Thanks for watching!", no_speech_prob=0.1)
            return iter([seg, junk]), types.SimpleNamespace(language="en", language_probability=0.97)

    fw = types.ModuleType("faster_whisper")
    fw.WhisperModel = Model
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)
    cfg = {"whisper": True, "whisper_model": "base"}
    out = describe.transcribe(str(raw), 0.0, 4.0, cfg, "en")
    assert asked == ["en"] and out == [(0.5, 2.0, "no way he did that")]      # выдумка Whisper выброшена
    assert describe.transcribe(str(raw), 0.0, 4.0, cfg) and asked[-1] == "ru"  # по умолчанию как раньше
    heard = {}
    describe.transcribe(str(raw), 0.0, 4.0, cfg, "", heard)                     # язык неизвестен: Whisper решает сам
    assert asked[-1] is None and heard == {"lang": "en"}
