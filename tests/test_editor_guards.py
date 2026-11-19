"""Монтаж: обрубки и клипы без самого момента не сохраняются."""
import json
import os
import shutil
import subprocess

import pytest

from streamclipper import config, editor

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нужен ffmpeg")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STREAMCLIPPER_HOME", str(tmp_path / "home"))
    cfg = dict(config.load(), clips_dir=str(tmp_path / "clips"), whisper=False, clip_format="original")
    config.save(cfg)
    return tmp_path


def _job(home, seconds, **over):
    """Заготовка, как её оставляет запись: видео на seconds секунд и описание момента."""
    raw = config.subdir("candidates") / "ch_20261119_210000_aaaaaa.ts"
    made = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                           "testsrc2=size=320x180:rate=30", "-f", "lavfi", "-i", "sine=frequency=440", "-t", str(seconds),
                           "-c:v", "libx264", "-preset", "ultrafast", "-g", "30", "-c:a", "aac", "-f", "mpegts", str(raw)])
    if made.returncode != 0:
        pytest.skip("в этом ffmpeg нет кодировщика для тестового видео")
    job = {"id": "20261119_210000_aaaaaa", "channel": "ch", "raw": str(raw), "raw_start": 1_795_000_000.0,
           "raw_dur": float(seconds), "title": "Стрим", "category": "GTA", "kind": "laugh", "not_before": 0.0,
           "win_start": 0.0, "win_end": float(seconds), "chatlog": [], "room_id": "", "onset": 30.0, "peak": 34.0,
           "end": 40.0, "score": 70, "base_score": 70, "peak_z": 12, "share": 0.6, "messages": 40, "top": [], "chat": []}
    job.update(over)
    path = str(raw)[:-3] + ".json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(job, f)
    return path


def _clips(home):
    return [f for _d, _s, fs in os.walk(home / "clips") for f in fs if f.endswith(".mp4")]


def test_normal_moment_becomes_a_clip(home):
    res = editor.process(_job(home, 50))
    assert res["ok"] and not res.get("skipped") and os.path.exists(res["clip"])
    assert 25 <= res["dur"] <= 61
    assert len(_clips(home)) == 1


def test_stub_of_a_clip_is_not_saved(home):
    """Запись прерывалась: от момента осталось 11 секунд видео. Раньше из этого выходил клип-обрубок."""
    res = editor.process(_job(home, 11, onset=4.0, peak=6.0, end=9.0))
    assert res["ok"] and "слишком короткий" in res["skipped"]
    assert _clips(home) == []


def test_clip_without_the_moment_itself_is_not_saved(home):
    """Запись отставала от эфира: видео кончилось раньше, чем дошло до момента. Одна подводка никому не нужна."""
    res = editor.process(_job(home, 28, raw_dur=60.0, win_end=60.0, onset=38.0, peak=42.0, end=50.0))
    assert res["ok"] and res["skipped"] == "сам момент не попал в запись"
    assert _clips(home) == []


def test_manual_clip_is_kept_even_when_short(home):
    res = editor.process(_job(home, 10, kind="manual", onset=0.0, peak=2.0, end=10.0, score=0))
    assert res["ok"] and not res.get("skipped") and os.path.exists(res["clip"])


def test_shortest_clip_follows_the_setting():
    assert editor.min_length({"clip_min": 25}, manual=False) == 15.0
    assert editor.min_length({"clip_min": 60}, manual=False) == 36.0
    assert editor.min_length({"clip_min": 10}, manual=False) == 12.0
    assert editor.min_length({"clip_min": 60}, manual=True) == 8.0
