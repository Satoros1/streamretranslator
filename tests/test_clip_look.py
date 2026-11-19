"""Вид клипа «как в TikTok-нарезках»: крупный заголовок над видео, видео крупно, ИИ видит кадры."""
import base64
import json
import os
import shutil
import subprocess
import sys
import types

import pytest

from streamclipper import config, describe, editor, titlerender, vrender

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нужен ffmpeg")


def _width_for(px):
    """Ширина строки без Qt: каждая буква занимает 0.55 размера шрифта."""
    return lambda s: len(s) * px * 0.55


# ---- заголовок: текст и строки ----

def test_headline_text_is_cleaned():
    assert titlerender.clean('  «Угон   инвалидной\nколяски в гта 6».  ') == "Угон инвалидной коляски в гта 6"
    assert titlerender.clean('"стример проиграл всё за 10 секунд..."') == "стример проиграл всё за 10 секунд"
    assert titlerender.clean("стример сказал «привет»") == "стример сказал «привет»"        # кавычки вокруг слова остаются
    assert titlerender.clean("«раз» и «два»") == "«раз» и «два»"
    assert titlerender.clean("что это было?!") == "что это было?!"
    assert titlerender.clean(None) == "" and titlerender.clean('""') == "" and titlerender.clean(' ". ') == ""
    long = titlerender.clean("слово " * 40)
    assert len(long) <= titlerender.MAX_CHARS + 1 and long.endswith("о…")


def test_headline_lines_are_balanced():
    words = "угон инвалидной коляски в гта 6".split()
    w = _width_for(70)
    assert titlerender.wrap(words, w, 10_000) == ["угон инвалидной коляски в гта 6"]
    lines = titlerender.balance(words, w, 26 * 70 * 0.55)        # в строку влезает 26 букв
    assert lines == ["угон инвалидной", "коляски в гта 6"]       # а не длинная строка и «6» под ней
    assert " ".join(lines) == " ".join(words)


def test_headline_fits_the_frame():
    px, lines = titlerender.layout("угон инвалидной коляски в гта 6", _width_for)
    assert px == titlerender.SIZES[0] and len(lines) == 2        # короткий заголовок: крупно, в две строки
    px2, lines2 = titlerender.layout("стример полчаса искал ключи от машины, а они всё это время лежали у него в кармане", _width_for)
    assert px2 < px and len(lines2) <= titlerender.MAX_LINES     # длинный: буквы мельче
    for size, got in ((px, lines), (px2, lines2)):
        limit = titlerender.MAX_W - 2 * round(size * 0.5)
        assert all(_width_for(size)(ln) <= limit for ln in got)
    # текст, который не влезает никак (буквы шире обычного): три строки, в конце многоточие, за края не вылезает
    fat = lambda size: (lambda line: len(line) * size * 0.9)     # noqa: E731
    px3, lines3 = titlerender.layout("слово " * 30, fat)
    limit = titlerender.MAX_W - 2 * round(px3 * 0.5)
    assert px3 == titlerender.SIZES[-1] and len(lines3) == titlerender.MAX_LINES
    assert lines3[-1].endswith("о…") and not any(ln.endswith("…") for ln in lines3[:-1])
    assert all(fat(px3)(ln) <= limit for ln in lines3)
    # одно слово длиннее строки: обрезается, а не вылезает за плашку; многоточие одно
    px4, lines4 = titlerender.layout("а" * 80 + " конец", _width_for)
    limit = titlerender.MAX_W - 2 * round(px4 * 0.5)
    assert lines4[0].endswith("а…") and not lines4[0].endswith("……") and lines4[-1] == "конец"
    px5, lines5 = titlerender.layout("а" * 40 + " и потом ещё несколько самых обычных слов", _width_for)
    assert lines5[0].endswith("а…") and " ".join(lines5[1:]) == "и потом ещё несколько самых обычных слов"
    assert all(_width_for(px4)(ln) <= limit for ln in lines4)


def test_headline_picture(tmp_path):
    pytest.importorskip("PySide6")
    vrender._qt_app()
    out = str(tmp_path / "title.png")
    size = titlerender.render("угон инвалидной коляски в гта 6", out)
    from PySide6.QtGui import QImage
    img = QImage(out)
    assert size == (img.width(), img.height()) and 300 < size[0] <= titlerender.MAX_W and 150 < size[1] < 330
    assert size[0] % 2 == 0 and size[1] % 2 == 0
    assert img.pixelColor(2, 2).alpha() == 0                     # углы плашки скруглены: за ними видно видео
    mid = img.pixelColor(size[0] // 2, 6)
    assert mid.alpha() > 200 and mid.lightness() < 40            # плашка тёмная
    white = sum(1 for x in range(0, size[0], 3) for y in range(0, size[1], 3) if img.pixelColor(x, y).lightness() > 230)
    assert white > 300                                           # и на ней белые буквы
    assert titlerender.render("  ", str(tmp_path / "none.png")) is None and not os.path.exists(tmp_path / "none.png")


# ---- кадр ----

def test_big_layout_like_tiktok_cuts():
    g = vrender.geometry("big", 1920, 1080, chat=True, title=True)
    assert g["mode"] == "blur" and g["fg_h"] == vrender.BIG_H    # видео во всю ширину, 4:3: крупнее, чем целиком (608)
    side, y = g["title"]
    assert side == "bottom" and y == g["vy"] - vrender.TITLE_GAP  # заголовок стоит прямо над видео
    assert g["vy"] >= 3 * round(70 * 1.22) + 2 * round(70 * 0.34) + 120      # три строки заголовка влезают, верх свободен
    x, cy, w, h = g["chat"]
    assert cy >= g["vy"] + g["fg_h"] and cy + h <= vrender.H - 250           # чат под видео, низ под подписи TikTok
    assert g["vy"] + g["fg_h"] // 2 < vrender.H // 2             # видео в верхней половине кадра, как в примерах
    plain = vrender.geometry("big", 1920, 1080, chat=False, title=False)
    assert plain["title"] is None and plain["fg_h"] == vrender.BIG_H
    assert vrender.geometry("big", 1440, 1080, chat=True, title=True)["fg_h"] == 810      # 4:3 остаётся как есть
    assert vrender.geometry("big", 1080, 1080, chat=False, title=True)["fg_h"] == 1080    # квадратное не обрезаем
    assert vrender.geometry("blur", 1920, 1080, chat=True, title=True)["fg_h"] == 608
    assert vrender.geometry("crop", 1920, 1080, chat=True, title=True)["title"] == ("top", 170)
    assert vrender.geometry("big", 1080, 1920, chat=True, title=True)["mode"] == "fit"    # вертикальный стрим


def test_title_is_the_right_ffmpeg_input():
    geo = vrender.geometry("big", 1920, 1080, chat=True, title=True)
    both = vrender.filter_graph(geo, 60.0, True, (900, 200))
    assert "[base][2:v]overlay=90:" in both and "[titled][1:v]overlay" in both           # чат вход 1, заголовок вход 2
    assert f"overlay=90:{geo['title'][1] - 200}[titled]" in both                         # низ плашки над видео
    solo = vrender.geometry("big", 1920, 1080, chat=False, title=True)
    only = vrender.filter_graph(solo, 60.0, False, (900, 200))
    assert "[base][1:v]overlay" in only and "[2:v]" not in only and only.endswith("[titled]format=yuv420p[v]")
    none = vrender.filter_graph(geo, 60.0, True, None)
    assert "titled" not in none and "[base][1:v]overlay" in none
    assert f"crop={vrender.W}:{vrender.BIG_H}" in both           # видео приближено: края по бокам за кадром
    top = vrender.filter_graph(vrender.geometry("crop", 1920, 1080, False, True), 30.0, False, (800, 120))
    assert "overlay=140:170[titled]" in top


# ---- что написать над видео ----

def test_what_goes_above_the_video():
    auto, manual = {"kind": "laugh"}, {"kind": "manual"}
    cfg = {"tt_title": True, "tt_title_text": ""}
    assert describe.headline(auto, " угон коляски в гта 6 ", True, cfg) == "угон коляски в гта 6"
    assert describe.headline(auto, "Чат разрывает от смеха", False, cfg) == "Чат разрывает от смеха"
    assert describe.headline(manual, "Клип вручную", False, cfg) == ""           # кнопкой и без ИИ: писать нечего
    assert describe.headline(manual, "погоня на катере", True, cfg) == "погоня на катере"
    assert describe.headline(auto, "что угодно", True, dict(cfg, tt_title_text=" GTA 6 ")) == "GTA 6"
    assert describe.headline(auto, "что угодно", True, dict(cfg, tt_title=False, tt_title_text="GTA 6")) == ""


# ---- ИИ видит кадры ----

def test_ai_message_with_frames():
    assert describe.ai_content("текст", []) == "текст"
    content = describe.ai_content("текст", [(12.4, b"\xff\xd8one"), (31.0, b"\xff\xd8two")])
    assert [c["type"] for c in content] == ["text", "image", "text", "image", "text"]
    assert content[0]["text"] == "Кадр на 12-й секунде:" and content[-1]["text"] == "текст"
    src = content[1]["source"]
    assert src["type"] == "base64" and src["media_type"] == "image/jpeg" and base64.b64decode(src["data"]) == b"\xff\xd8one"
    json.dumps(content)                                          # уходит в запрос как есть


def _video(path, seconds=40, size="640x360"):
    made = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                           f"testsrc2=size={size}:rate=30", "-f", "lavfi", "-i", "sine=frequency=440", "-t", str(seconds),
                           "-c:v", "libx264", "-preset", "ultrafast", "-g", "30", "-c:a", "aac", "-f", "mpegts", str(path)])
    if made.returncode != 0:
        pytest.skip("в этом ffmpeg нет кодировщика для тестового видео")
    return str(path)


@needs_ffmpeg
def test_frames_for_the_ai(tmp_path):
    raw = _video(tmp_path / "raw.ts", 30, "1280x720")
    pics = describe.shots(raw, 4.0, 28.0)
    assert len(pics) == describe.SHOTS and [round(t, 1) for t, _ in pics] == [5.5, 8.5, 11.5, 14.5, 17.5, 20.5, 23.5, 26.5]
    assert all(jpg[:2] == b"\xff\xd8" and len(jpg) < 200_000 for _t, jpg in pics)        # небольшие JPEG
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", "-i", "pipe:0"],
                           input=pics[0][1], stdout=subprocess.PIPE).stdout.decode().strip()
    assert probe.startswith(f"{describe.SHOT_W},360")
    assert describe.shots(raw, 10.0, 11.0) == []                 # кусок короче двух секунд: кадры не нужны
    assert describe.shots(str(tmp_path / "нет.ts"), 0.0, 20.0) == []


class _FakeAnthropic:
    """Вместо настоящего ИИ: запоминает запрос и отвечает готовым вердиктом."""

    def __init__(self, answer=None, stop="end_turn"):
        self.calls, self.answer, self.stop = [], answer, stop
        outer = self

        class Messages:
            def parse(self, **kw):
                outer.calls.append(kw)
                v = outer.answer
                parsed = None if v is None else kw["output_format"](**v)
                return types.SimpleNamespace(stop_reason=outer.stop, parsed_output=parsed)

        class Client:
            def __init__(self, **kw):
                outer.client_kw = kw
                self.messages = Messages()

        self.module = types.ModuleType("anthropic")
        self.module.Anthropic = Client


@pytest.fixture
def ai(monkeypatch, tmp_path):
    monkeypatch.setenv("STREAMCLIPPER_HOME", str(tmp_path / "home"))
    fake = _FakeAnthropic({"keep": True, "start": 12.0, "end": 47.0, "title": " угон инвалидной коляски в гта 6 ",
                           "description": "Стример угнал коляску и уехал от полиции.", "score": 88})
    monkeypatch.setitem(sys.modules, "anthropic", fake.module)
    return fake


def _ai_job(raw="", **over):
    job = {"channel": "ch", "title": "GTA 6 первый взгляд", "category": "Grand Theft Auto VI", "kind": "laugh", "raw": raw,
           "onset": 30.0, "peak": 34.0, "end": 40.0, "chat": [[31.0, "vasya", "АХАХАХ"], [33.0, "petya", "коляска ору"]]}
    job.update(over)
    return job


AI_CFG = {"ai_enabled": True, "ai_api_key": "sk-test", "ai_model": "", "ai_frames": True, "clip_min": 30, "clip_max": 45}


@needs_ffmpeg
def test_ai_sees_the_screen(ai, tmp_path):
    raw = _video(tmp_path / "raw.ts")
    v = describe.ai_verdict(_ai_job(raw), [(28.0, 31.0, "смотрите, коляска")], 0.0, 40.0, (5.0, 40.0), AI_CFG)
    assert v == {"keep": True, "start": 12.0, "end": 47.0, "title": "угон инвалидной коляски в гта 6",
                 "description": "Стример угнал коляску и уехал от полиции.", "score": 88}
    call = ai.calls[0]
    content = call["messages"][0]["content"]
    assert sum(1 for c in content if c["type"] == "image") == describe.SHOTS
    text = content[-1]["text"]
    assert "смотрите, коляска" in text and "коляска ору" in text and "Предварительный выбор: 5-40" in text
    assert call["model"] == "claude-haiku-5-5" and call["system"] == describe.SYSTEM and "кадр" in describe.SYSTEM
    assert ai.client_kw["api_key"] == "sk-test"


def test_ai_without_frames_and_for_a_manual_clip(ai, tmp_path):
    # кадры выключены в настройках: уходит только текст
    assert describe.ai_verdict(_ai_job("нет.ts"), [], 0.0, 60.0, (5.0, 40.0), dict(AI_CFG, ai_frames=False))
    assert isinstance(ai.calls[0]["messages"][0]["content"], str)
    # клип, вырезанный кнопкой: ИИ просят только назвать его, границы не трогать
    assert describe.ai_verdict(_ai_job("", kind="manual"), [], 0.0, 60.0, (10.0, 50.0), AI_CFG)
    text = ai.calls[1]["messages"][0]["content"]
    assert "вырезал сам" in text and "от 10 до 50" in text and "Чат начал реагировать" not in text
    # ИИ выключен или нет ключа: запроса нет
    assert describe.ai_verdict(_ai_job(), [], 0, 60, (5, 40), dict(AI_CFG, ai_enabled=False)) is None
    assert describe.ai_verdict(_ai_job(), [], 0, 60, (5, 40), dict(AI_CFG, ai_api_key="")) is None
    assert len(ai.calls) == 2
    # ответ оборвался или ИИ отказался: берём обычное описание, клип не теряется
    ai.stop = "max_tokens"
    assert describe.ai_verdict(_ai_job(), [], 0, 60, (5, 40), AI_CFG) is None
    ai.stop, ai.answer = "end_turn", None
    assert describe.ai_verdict(_ai_job(), [], 0, 60, (5, 40), AI_CFG) is None


# ---- настройки ----

def test_new_look_becomes_the_default_once(tmp_path, monkeypatch):
    monkeypatch.setenv("STREAMCLIPPER_HOME", str(tmp_path / "home"))
    assert config.load()["tt_layout"] == "big" and config.load()["tt_title"] is True     # новая установка
    path = config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tt_layout": "blur", "channels": ["a"]}), encoding="utf-8")       # настройки от 0.1.1
    cfg = config.load()
    assert cfg["tt_layout"] == "big" and cfg["look"] == 1 and cfg["channels"] == ["a"]
    path.write_text(json.dumps({"tt_layout": "crop"}), encoding="utf-8")         # человек выбирал вид сам: не трогаем
    assert config.load()["tt_layout"] == "crop"
    cfg = dict(config.load(), tt_layout="blur")                  # вернул прежний вид уже в новой версии
    config.save(cfg)
    assert config.load()["tt_layout"] == "blur"                  # и он остаётся


# ---- всё вместе: настоящий клип ----

def _job_file(home, raw, **over):
    job = {"id": "20261119_210000_aaaaaa", "channel": "ch", "raw": raw, "raw_start": 1_795_000_000.0, "raw_dur": 50.0,
           "title": "Стрим", "category": "GTA", "kind": "laugh", "not_before": 0.0, "win_start": 0.0, "win_end": 50.0,
           "chatlog": [[30.0 + i * 0.4, f"user{i}", "", "АХАХАХ ору", []] for i in range(30)], "room_id": "",
           "onset": 30.0, "peak": 34.0, "end": 40.0, "score": 70, "base_score": 70, "peak_z": 12, "share": 0.6,
           "messages": 40, "top": [], "chat": []}
    job.update(over)
    path = raw[:-3] + ".json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(job, f)
    return path


@needs_ffmpeg
def test_vertical_clip_with_headline_and_chat(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("STREAMCLIPPER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    config.save(dict(config.load(), clips_dir=str(tmp_path / "clips"), whisper=False, lead_in=8,
                     tt_title_text="угон инвалидной коляски в гта 6"))
    raw = _video(config.subdir("candidates") / "ch_20261119_210000_aaaaaa.ts", 50, "1280x720")
    res = editor.process(_job_file(tmp_path, raw))
    assert res["ok"] and res["clip"].endswith("_9x16.mp4"), res
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height",
                                      "-of", "json", res["clip"]], stdout=subprocess.PIPE).stdout)
    video = next(s for s in info["streams"] if s.get("width"))
    assert (video["width"], video["height"], video["codec_name"]) == (1080, 1920, "h264")
    # кадр из середины клипа: над видео тёмная плашка с белыми буквами
    shot = str(tmp_path / "shot.png")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "6", "-i", res["clip"], "-frames:v", "1", shot])
    from PySide6.QtGui import QImage
    img = QImage(shot)
    geo = vrender.geometry("big", 1280, 720, chat=True, title=True)
    band = [img.pixelColor(x, y).lightness() for y in range(geo["vy"] - 200, geo["vy"] - 20, 4) for x in range(100, 980, 4)]
    assert sum(1 for v in band if v > 225) > 150 and sum(1 for v in band if v < 30) > 2000, (max(band), min(band))
    assert not [f for f in os.listdir(os.path.dirname(res["clip"])) if f.endswith(".png")]       # картинка заголовка убрана
