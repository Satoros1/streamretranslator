"""Очередь монтажа: что монтируется первым и что происходит, когда моментов больше, чем успевает компьютер."""
import json
import os
import queue
import time

import pytest

from streamclipper import config
from streamclipper.supervisor import EDIT_BACKLOG, EditQueue, Supervisor, WorkerHandle, job_rank


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STREAMCLIPPER_HOME", str(tmp_path / "home"))
    return tmp_path


def _job(name, score=0, kind="laugh"):
    """Заготовка клипа как её оставляет процесс записи: файл с видео и файл с описанием."""
    d = config.subdir("candidates")
    with open(d / (name + ".ts"), "wb") as f:
        f.write(b"x" * 100)
    with open(d / (name + ".json"), "w", encoding="utf-8") as f:
        json.dump({"id": name.split("_", 1)[1], "score": score, "kind": kind}, f)
    return str(d / (name + ".json"))


def test_manual_clips_go_first_then_the_strongest_moments():
    q = EditQueue()
    for job, score, manual in (("weak", 20, False), ("strong", 90, False), ("asked", 0, True), ("mid", 55, False),
                               ("mid2", 55, False)):
        assert q.put(job, score, manual) == []
    order = []
    while not q.empty():
        order.append(q.get(0)[0])
        q.done()
    assert order == ["asked", "strong", "mid", "mid2", "weak"]      # при равной оценке раньше тот, что пойман раньше
    with pytest.raises(queue.Empty):
        q.get(0.01)


def test_full_queue_drops_the_weakest_moment_and_never_a_manual_clip():
    q = EditQueue(limit=3)
    assert q.put("a", 40) == [] and q.put("b", 70) == [] and q.put("asked", 0, manual=True) == []
    assert q.put("c", 60) == [("a", 40)]                 # самый слабый из ждущих уступает место
    assert q.put("d", 10) == [("d", 10)]                 # новый момент слабее всех: пропускается он сам
    assert q.put("asked2", 0, manual=True) == [("c", 60)]
    assert q.put("e", 70) == [("b", 70)]                 # оценки равны: уходит тот, что ждал дольше
    assert q.put("asked3", 0, manual=True) == [("e", 70)]
    assert q.put("asked4", 0, manual=True) == []         # одни ручные клипы: их не выбрасываем, даже сверх лимита
    assert q.qsize() == 4
    assert q.put("f", 99) == [("f", 99)]


def test_queue_counts_the_clip_being_edited_as_work():
    q = EditQueue()
    assert not q.busy() and not q.active()
    q.put("a", 50)
    assert q.busy() and not q.active()
    assert q.get(0) == ("a", 50, False)
    assert q.empty() and q.busy() and q.active()         # в очереди пусто, но клип ещё монтируется
    q.done()
    assert not q.busy() and not q.active()


def test_overflow_removes_files_of_the_dropped_moment_and_says_so(home):
    sup = Supervisor()
    sup.edit_q = EditQueue(limit=2)
    dropped_from_list = []
    sup.learner.drop = dropped_from_list.append
    weak = _job("some_chan_20261119_210000_aaaaaa", 15)
    good = _job("some_chan_20261119_210100_bbbbbb", 80)
    asked = _job("ch_20261119_210200_cccccc", 0, "manual")
    sup._enqueue(weak, 15)
    sup._enqueue(good, 80)
    sup._enqueue(asked, 0, manual=True)
    assert not os.path.exists(weak) and not os.path.exists(weak[:-5] + ".ts")
    assert os.path.exists(good) and os.path.exists(asked)
    assert dropped_from_list == ["20261119_210000_aaaaaa"]
    said = [sup.events.get_nowait()["text"] for _ in range(sup.events.qsize())]
    assert any("самый слабый момент пропущен (some_chan, оценка 15)" in t for t in said)
    assert sup.edit_q.get(0)[0] == asked


def test_leftovers_from_last_run_keep_their_priority(home, monkeypatch):
    sup = Supervisor()
    monkeypatch.setattr(sup.tg, "start", lambda cfg: None)
    monkeypatch.setattr("streamclipper.supervisor.threading.Thread", lambda **kw: type("T", (), {"start": lambda self: None})())
    a = _job("ch_20261119_200000_aaaaaa", 30)
    b = _job("ch_20261119_200100_bbbbbb", 75)
    c = _job("ch_20261119_200200_cccccc", 0, "manual")
    assert job_rank(b) == (75, False) and job_rank(c) == (0, True)
    assert job_rank(str(home / "нет такого.json")) == (0, False)
    sup.cfg = dict(sup.cfg, channels=[])
    sup.start()
    assert [sup.edit_q.get(0)[0] for _ in range(3)] == [c, b, a]


def test_failed_edit_is_retried_with_the_same_priority(home):
    sup = Supervisor()
    job = _job("ch_20261119_200000_aaaaaa", 64)
    sup._edit_done(job, {"ok": False, "error": "сбой"}, 64, False)
    assert sup.edit_q.get(0) == (job, 64, False)
    sup.edit_q.done()
    sup._edit_done(job, {"ok": False, "error": "сбой"}, 64, False)       # вторая неудача: заготовка убирается
    assert sup.edit_q.empty() and not os.path.exists(job)


def test_stop_recording_keeps_workers_down(home, monkeypatch):
    """Проверочный запуск: запись остановлена, домонтируется пойманное. Сторож не должен поднимать запись обратно."""
    sup = Supervisor()
    spawned = []
    monkeypatch.setattr(sup, "_spawn", lambda h: spawned.append(h.name))
    h = sup.workers["ch"] = WorkerHandle("ch")
    sup._check(h, time.time())
    assert spawned == ["ch"]
    sup.stop_recording()
    sup._check(h, time.time() + 100)
    assert spawned == ["ch"] and h.status["state"] == "stopped"


def test_backlog_limit_is_sane():
    assert 6 <= EDIT_BACKLOG <= 30
