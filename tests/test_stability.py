"""Живучесть: что программа имеет право удалять, как переживает сбои, обрывы связи и перерывы в стриме."""
import json
import logging
import os
import random
import socket
import sqlite3
import threading
import time

import pytest

from streamclipper import chat as chat_mod
from streamclipper import config, editor, util
from streamclipper import worker as worker_mod
from streamclipper.chat import SILENCE_DEAD, ChatClient
from streamclipper.detector import WARMUP, Detector
from streamclipper.supervisor import Supervisor
from streamclipper.uploader import Telegram, UploadQueue
from streamclipper.worker import PUMP_HANG, Worker


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Отдельная папка данных программы на время теста."""
    monkeypatch.setenv("STREAMCLIPPER_HOME", str(tmp_path / "home"))
    return tmp_path


def _file(path, size=10, age=0.0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)
    if age:
        os.utime(path, (time.time() - age, time.time() - age))
    return str(path)


# --- удаление файлов -------------------------------------------------------
def test_only_own_clips_are_recognised():
    day = os.path.join("D:", "Видео", "ch", "2026-11-19")
    assert util.own_clip(os.path.join(day, "ch_2026-11-19_21-03-11_085_Смешной момент.mp4"))
    assert util.own_clip(os.path.join(day, "ch_2026-11-19_21-03-11_085_Смешной момент_9x16.mp4"))
    assert util.own_clip(os.path.join(day, "some_chan_2026-11-19_21-03-11_007.mp4"))
    assert not util.own_clip(os.path.join(day, "свадьба.mp4"))
    assert not util.own_clip(os.path.join("D:", "Видео", "отпуск 2026-11-19.mp4"))
    assert not util.own_clip(os.path.join("D:", "Видео", "ch_2026-11-19_21-03-11_085_Смешной момент.mp4"))
    assert not util.own_clip(os.path.join(day, "ch_2026-11-19_21-03-11_085_Смешной момент.txt"))


def test_description_lives_until_the_last_file_of_a_clip(tmp_path):
    day = tmp_path / "ch" / "2026-11-19"
    wide = _file(day / "ch_2026-11-19_21-03-11_085_Момент.mp4")
    tall = _file(day / "ch_2026-11-19_21-03-11_085_Момент_9x16.mp4")
    txt = _file(day / "ch_2026-11-19_21-03-11_085_Момент.txt")
    assert util.clip_twin(wide) == tall and util.clip_twin(tall) == wide
    assert util.remove_clip(tall)
    assert os.path.exists(wide) and os.path.exists(txt)          # описание ещё нужно обычному клипу
    assert util.remove_clip(wide)
    assert not os.path.exists(txt) and not day.exists()          # последним ушло описание, за ним пустая папка дня
    assert not util.remove_clip(wide)                            # файла уже нет: это не ошибка


def test_day_folder_with_other_files_is_kept(tmp_path):
    day = tmp_path / "ch" / "2026-11-19"
    clip = _file(day / "ch_2026-11-19_21-03-11_085.mp4")
    other = _file(day / "заметки.docx")
    assert util.remove_clip(clip)
    assert os.path.exists(other)


def test_trim_never_touches_foreign_videos(home):
    """Человек выбрал для клипов общую папку с видео: сверх лимита удаляются только клипы программы."""
    clips = home / "Видео"
    foreign = [_file(clips / "отпуск.mp4", 50_000, age=9000), _file(clips / "ch" / "2026-11-17" / "семья.mp4", 50_000, age=9000),
               _file(clips / "ch_2026-11-17_10-00-00_050_похоже на клип.mp4", 50_000, age=9000)]
    old = _file(clips / "ch" / "2026-11-17" / "ch_2026-11-17_10-00-00_050_старый.mp4", 2000, age=3000)
    mid = _file(clips / "ch" / "2026-11-18" / "ch_2026-11-18_10-00-00_050_средний.mp4", 2000, age=2000)
    new = _file(clips / "ch" / "2026-11-19" / "ch_2026-11-19_10-00-00_050_новый.mp4", 2000, age=1000)
    sup = Supervisor()
    sup.cfg = dict(sup.cfg, clips_dir=str(clips), max_clips_gb=5000 / 1e9, tg_enabled=False)
    sup._trim_clips()
    assert all(os.path.exists(p) for p in foreign)
    assert not os.path.exists(old) and os.path.exists(mid) and os.path.exists(new)


def test_clip_waiting_for_upload_is_not_trimmed(home):
    clips = home / "clips"
    old = _file(clips / "ch" / "2026-11-17" / "ch_2026-11-17_10-00-00_050.mp4", 4000, age=3000)
    new = _file(clips / "ch" / "2026-11-19" / "ch_2026-11-19_10-00-00_050.mp4", 4000, age=1000)
    sup = Supervisor()
    sup.cfg = dict(sup.cfg, clips_dir=str(clips), max_clips_gb=5000 / 1e9, tg_enabled=True)
    sup.tg.queue.add(old, "подпись")
    sup._trim_clips()
    assert os.path.exists(old) and not os.path.exists(new)


def test_stale_editor_leftovers_are_cleaned(home):
    sup = Supervisor()
    d = config.subdir("candidates")
    gone = [_file(d / "ch_1.vr.json", age=7200), _file(d / "ch_2.ts.json.tmp", age=7200), _file(d / "ch_3.ts", age=7200)]
    kept = [_file(d / "ch_4.ts", age=7200), _file(d / "ch_4.json", age=7200), _file(d / "ch_5.vr.json", age=60)]
    sup._clean_candidates()
    assert not any(os.path.exists(p) for p in gone)
    assert all(os.path.exists(p) for p in kept)


def test_missing_clips_disk_does_not_crash_and_clip_is_saved_elsewhere(home, monkeypatch):
    """Вынули внешний диск с клипами: программа не падает, а клип ложится в папку по умолчанию."""
    blocker = _file(home / "not_a_folder")
    fallback = home / "fallback"
    monkeypatch.setattr(config, "default_clips_dir", lambda: str(fallback))
    cfg = {"clips_dir": os.path.join(blocker, "clips")}
    sup = Supervisor()
    assert sup._check_clips_dir(cfg) is False
    folder = editor.clip_folder(cfg, "ch", "2026-11-19")
    assert folder == fallback / "ch" / "2026-11-19" and folder.is_dir()
    good = {"clips_dir": str(home / "ok")}
    assert sup._check_clips_dir(good) is True
    assert editor.clip_folder(good, "ch", "2026-11-19") == home / "ok" / "ch" / "2026-11-19"


# --- настройки -------------------------------------------------------------
def test_settings_file_never_disappears_and_has_a_backup(home):
    cfg = config.load()
    config.save(dict(cfg, channels=["first"]))
    config.save(dict(cfg, channels=["second"]))
    path = config.config_path()
    assert config.load()["channels"] == ["second"]
    with open(str(path) + ".bak", encoding="utf-8") as f:
        assert json.load(f)["channels"] == ["first"]
    with open(path, "w", encoding="utf-8") as f:
        f.write("{ битый файл")
    assert config.load()["channels"] == ["first"]                 # основной файл испорчен: берётся копия


# --- очередь Telegram ------------------------------------------------------
def test_feedback_is_read_only_from_the_current_channel(home):
    """После смены канала номера старых постов значат чужие сообщения: по ним учиться нельзя."""
    q = UploadQueue()
    for p in ("a.mp4", "b.mp4"):
        q.add(p, "")
    ids = []
    for chat in ("-1001", "-1002"):
        row = q.next()
        q.mark(row["id"], "sent")
        q.set_msg(row["id"], 100 + row["id"], chat)
        ids.append(row["id"])
    assert [r["id"] for r in q.recent_sent("-1001")] == [ids[0]]
    assert [r["id"] for r in q.recent_sent("-1002")] == [ids[1]]
    assert q.recent_sent("-1003") == []


def test_old_upload_database_is_upgraded(home):
    path = str(config.data_dir() / "uploads.db")
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE uploads(id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT, caption TEXT,
        status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0, next_try REAL DEFAULT 0, error TEXT DEFAULT '',
        created REAL, sent_at REAL, msg_id INTEGER, fb INTEGER)""")
    db.execute("INSERT INTO uploads(path, caption, status, sent_at, msg_id) VALUES('old.mp4', '', 'sent', ?, 5)", (time.time(),))
    db.commit()
    db.close()
    q = UploadQueue()
    assert q.recent_sent("-1001") == []                            # старый пост: канал неизвестен, оценки по нему не читаем
    q.add("new.mp4", "")
    assert q.counts() == {"sent": 1, "pending": 1}


def test_counts_cache_for_the_window(home):
    q = UploadQueue()
    q.add("a.mp4", "")
    assert q.counts(60) == {"pending": 1}
    q.add("b.mp4", "")
    assert q.counts(60) == {"pending": 1}                          # окну хватает ответа секундной давности
    assert q.counts() == {"pending": 2}


def test_alerts_are_not_hoarded_while_telegram_is_off(home):
    tg = Telegram(lambda ev: None)
    tg._cfg = {"tg_enabled": False, "tg_alerts": True}
    tg.alert("стрим закончился", "off-ch", 600)
    assert not tg._alerts and not tg._alert_seen
    tg._cfg = {"tg_enabled": True, "tg_alerts": True}
    tg.alert("стрим закончился", "off-ch", 600)
    tg.alert("стрим закончился", "off-ch", 600)
    assert [text for _born, text in tg._alerts] == ["стрим закончился"]


# --- чат -------------------------------------------------------------------
class _Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def time(self):
        return self.now


class _Sock:
    """Соединение, в котором сервер молчит. answer=True: на наш PING он отвечает, значит связь жива."""

    def __init__(self, clock, answer, client=None, until=0.0):
        self.clock, self.answer, self.client, self.until = clock, answer, client, until
        self.sent: list[bytes] = []
        self.reply = b""

    def settimeout(self, _t):
        pass

    def sendall(self, data):
        self.sent.append(data)
        if self.answer and data.startswith(b"PING"):
            self.reply = b":tmi.twitch.tv PONG tmi.twitch.tv :sc\r\n"

    def recv(self, _n):
        if self.client is not None and self.clock.now >= self.until:
            self.client._quit.set()
        if self.reply:
            data, self.reply = self.reply, b""
            return data
        self.clock.now += 15
        raise socket.timeout()


def test_silently_dead_chat_connection_is_noticed(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(chat_mod, "time", clock)
    c = ChatClient("x", lambda *a: None)
    sock = _Sock(clock, answer=False)
    t0 = clock.now
    with pytest.raises(ConnectionError):
        c._listen(sock)
    assert b"PING :sc\r\n" in sock.sent
    assert clock.now - t0 <= SILENCE_DEAD + 30                     # раньше обрыв замечали только через 5 минут


def test_quiet_but_alive_chat_stays_connected(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(chat_mod, "time", clock)
    c = ChatClient("x", lambda *a: None)
    sock = _Sock(clock, answer=True, client=c, until=clock.now + 1800)
    c._listen(sock)                                                # полчаса тишины: ни одного разрыва
    assert sock.sent.count(b"PING :sc\r\n") >= 20


# --- детектор --------------------------------------------------------------
def _feed(det, rng, t0, seconds, rate, laugh_share=0.05):
    filler = ["привет", "го катку", "какой ранг", "норм", "стример топ", "а что за игра", "да", "нет"]
    laughs = ["АХАХАХАХ", "ахахах", "KEKW", "ору", "пхпхпх", "ХАХАХА что он несёт", "LUL"]
    events = []
    for s in range(seconds):
        t = t0 + s
        n = max(0, int(rng.gauss(rate, rate ** 0.5)))
        for i in range(n):
            text = rng.choice(laughs) if rng.random() < laugh_share else rng.choice(filler)
            det.add_message(t + i / (n + 1), f"u{rng.randint(1, 999)}", text)
        ev = det.tick(t + 1.0)
        if ev:
            events.append(ev)
    return events


def test_stream_start_is_not_a_funny_moment():
    """До эфира чат почти пуст. Стрим начался, зрители набежали и шумят: это не момент для клипа."""
    def run(restart: bool):
        rng = random.Random(3)
        det = Detector(3)
        t0 = 1_000_000.0
        ev = _feed(det, rng, t0, 400, 0.4)                         # ожидание стрима
        if restart:
            det.restart(t0 + 400)
        ev += _feed(det, rng, t0 + 400, 60, 30, laugh_share=0.5)   # начало эфира: наплыв и смайлы
        ev += _feed(det, rng, t0 + 460, 300, 14)
        late = _feed(det, rng, t0 + 760, 20, 50, laugh_share=0.8)  # настоящий момент позже
        late += _feed(det, rng, t0 + 780, 60, 14)
        return ev, late

    ev, late = run(restart=False)
    assert ev, "проверка потеряла смысл: без сброса нормы начало стрима должно выглядеть всплеском"
    ev, late = run(restart=True)
    assert ev == []
    assert len(late) == 1 and late[0].kind == "laugh"


def test_restart_gives_a_fresh_warmup():
    det = Detector(3)
    det.tick(1000.0)
    det.block_until = 5000.0
    det.hype = {"onset": 1.0}
    det.hist_total.extend([1.0] * 50)
    det.restart(2000.0)
    assert det.started == 2000.0 and det.hype is None and det.block_until == 0.0
    assert not det.hist_total and not det.hist_react
    assert WARMUP > 30


# --- процесс записи --------------------------------------------------------
class _Endless:
    """Источник видео, который отдаёт данные без остановки."""

    def __init__(self):
        self.reads = 0

    def read(self, n):
        self.reads += 1
        return b"\x47" * n


def test_reader_thread_does_not_leak_when_recording_stops(home):
    """Запись остановилась (пауза, сбой), а поток чтения раньше навсегда застревал с полной очередью видео в памяти."""
    w = Worker("ch")
    w.cfg = dict(w.cfg, paused=True)                               # цикл записи выходит сразу, ничего не забирая
    before = {t for t in threading.enumerate() if t.name == "reader"}
    w._pump(_Endless())
    mine = [t for t in threading.enumerate() if t.name == "reader" and t not in before]
    deadline = time.time() + 5
    while any(t.is_alive() for t in mine) and time.time() < deadline:
        time.sleep(0.05)
    assert not any(t.is_alive() for t in mine)
    assert w.pump_beat == 0.0


class _HungFfmpeg:
    def __init__(self):
        self.killed = 0

    def poll(self):
        return None

    def kill(self):
        self.killed += 1


def test_watchdog_kills_hung_ffmpeg_then_restarts_the_process(home, monkeypatch):
    exits = []
    monkeypatch.setattr(worker_mod.os, "_exit", lambda code: exits.append(code))
    monkeypatch.setattr(logging, "shutdown", lambda: None)
    w = Worker("ch")
    now = 1_000_000.0
    w.ff_cur = ff = _HungFfmpeg()

    w._watchdog(now)                                               # запись не идёт: сторож молчит
    w.pump_beat = now - 5
    w._watchdog(now)                                               # запись дышит
    assert ff.killed == 0 and not exits

    w.pump_beat = now - PUMP_HANG - 1                              # цикл записи замолчал
    w._watchdog(now)
    assert ff.killed == 1 and not exits                            # первый шаг: снять зависший ffmpeg
    w._watchdog(now + 10)
    assert ff.killed == 1 and not exits
    w._watchdog(now + PUMP_HANG + 1)
    assert exits == [75]                                           # не помогло: процесс канала перезапускается

    exits.clear()
    w._hang_acted = 0.0
    w.pump_beat = now + 200                                        # запись ожила
    w._watchdog(now + 201)
    assert not exits and w._hang_acted == 0.0


def test_watchdog_forgives_a_sleeping_computer(home, monkeypatch):
    exits = []
    monkeypatch.setattr(worker_mod.os, "_exit", lambda code: exits.append(code))
    w = Worker("ch")
    now = 1_000_000.0
    w.ff_cur = ff = _HungFfmpeg()
    w.pump_beat = now - 3600                                       # ноутбук час спал
    w._grace_until = now + PUMP_HANG
    w._watchdog(now)
    w._watchdog(now + PUMP_HANG - 1)
    assert ff.killed == 0 and not exits


def test_chat_outage_resets_the_chat_norm(home):
    w = Worker("ch")
    now = 1_000_000.0
    w.det.started = now - 5000
    w.chat.connected = False
    w._watch_chat(now)
    w._watch_chat(now + 30)
    w.chat.connected = True
    w._watch_chat(now + 40)                                        # короткий обрыв: норма чата остаётся
    assert w.det.started == now - 5000
    w.chat.connected = False
    w._watch_chat(now + 100)
    w.chat.connected = True
    w._watch_chat(now + 400)                                       # чата не было 5 минут: учимся заново
    assert w.det.started == now + 400
