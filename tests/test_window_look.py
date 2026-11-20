"""Новый вид окна: кадры стримов, обложки клипов, лампочки состояния. Логика записи тут не проверяется."""
import os
import shutil
import subprocess
import time

import pytest

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нужен ffmpeg")


@pytest.fixture
def app(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("STREAMCLIPPER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _wait(fn, sec=15.0):
    end = time.time() + sec
    while time.time() < end:
        got = fn()
        if got:
            return got
        time.sleep(0.1)
    return None


def _video(path, seconds, size="640x360", fmt=None):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30", "-t", str(seconds),
           "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "2M"]
    subprocess.run(cmd + (["-f", fmt] if fmt else []) + [str(path)], check=True)
    return str(path)


@needs_ffmpeg
def test_live_frame_comes_from_the_ring_buffer(app):
    from streamclipper import config
    from streamclipper.ui.frames import Frames
    buf = config.subdir("buffer") / "buster"
    buf.mkdir()
    for i in (1, 2):
        _video(buf / f"r00001_1_00000{i}.ts", 4, "1280x720", "mpegts")
        time.sleep(0.05)
    fr = Frames()
    try:
        got = _wait(lambda: fr.stream("buster"))
        assert got and os.path.getsize(got[0]) > 500 and got[0].endswith("live_buster.jpg")
        assert fr.stream("nobody") is None                         # нет записи: нет кадра, окно покажет букву
    finally:
        fr.close()


@needs_ffmpeg
def test_clip_cover_is_made_once(app, tmp_path):
    from streamclipper.ui.frames import Frames
    clip = _video(tmp_path / "clip_9x16.mp4", 3, "360x640")         # короче 12 секунд: кадр берётся из начала
    fr = Frames()
    try:
        got = _wait(lambda: fr.clip("c1", clip))
        assert got and os.path.exists(got[0])
        assert fr.clip("c1", clip) == got                          # второй раз ffmpeg не запускается
    finally:
        fr.close()


def test_frames_without_ffmpeg_do_not_break(app, tmp_path, monkeypatch):
    from streamclipper import util
    from streamclipper.ui import frames as F
    monkeypatch.setattr(util, "find_tool", lambda name: str(tmp_path / "no-ffmpeg"))
    (tmp_path / "c.mp4").write_bytes(b"0" * 1000)
    fr = F.Frames()
    try:
        fr.clip("c", str(tmp_path / "c.mp4"))
        assert _wait(lambda: "clip:c" in fr.failed, 5)
        assert fr.clip("c", str(tmp_path / "c.mp4")) is None
    finally:
        fr.close()


def test_stream_card_lamps(app):
    from streamclipper import gui
    from streamclipper.supervisor import Supervisor, WorkerHandle
    from streamclipper.ui import theme as T
    sup = Supervisor()
    for name, st in (("hot", {"state": "live", "hot": True, "chat": True, "rate": 30, "level": 0.9}),
                     ("calm", {"state": "live", "chat": True, "rate": 5, "level": 0.2}),
                     ("slow", {"state": "live", "hot": True, "slow": True, "level": 0.5}),
                     ("off", {"state": "offline"})):
        sup.workers[name] = WorkerHandle(name)
        sup.workers[name].status = st
    win = gui.MainWindow(sup)
    try:
        win.refresh_streams()
        c = win.cards
        assert c["hot"].chip.text() == "движ в чате" and c["hot"].chip.objectName() == "statusHot"
        assert c["hot"].objectName() == "cardHot" and c["hot"].dot.color == T.ACCENT and c["hot"].level.color == T.ACCENT
        assert c["calm"].chip.text() == "идёт запись" and c["calm"].dot.color == T.LIVE and c["calm"].objectName() == "card"
        assert c["slow"].chip.text() == "интернет не успевает" and c["slow"].chip.objectName() == "statusBad"
        assert c["off"].level.level == 0 and not c["off"].clip_btn.isEnabled()
        assert "клипов 0" in c["off"].meta.text()
    finally:
        win.really_quit = True
        win.close()
        sup.shutdown()


def test_log_marks_errors_without_emoji(app):
    from streamclipper import gui
    from streamclipper.supervisor import Supervisor
    sup = Supervisor()
    win = gui.MainWindow(sup)
    try:
        win.log_line({"time": "15:00:00", "text": "<b>канал</b>: ошибка", "level": "error"})
        text = win.log.toPlainText()
        assert "15:00:00" in text and "<b>канал</b>: ошибка" in text and "⚠" not in text
    finally:
        win.really_quit = True
        win.close()
        sup.shutdown()
