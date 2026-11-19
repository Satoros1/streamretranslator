"""Клипы не идут сплошным потоком: предел в час, первым выходит самый сильный момент, слабые пропускаются."""
import json
import os
import random
import time

import pytest

from streamclipper import bible, config, detector
from streamclipper.detector import EARLY, Detector, Event, strength
from streamclipper.pacer import HOLD, STALE, Pacer, every
from streamclipper.supervisor import Supervisor, WorkerHandle

T0 = 1_000_000.0


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


# --- сама очередь -------------------------------------------------------------
def test_quiet_hour_nobody_waits_and_nothing_is_lost():
    p = Pacer(12)
    assert p.gap == 300
    for i in range(6):                                   # момент раз в десять минут: реже, чем предел
        now = T0 + i * 600
        assert p.offer(f"j{i}", 1.2, 80, now) == []
        assert p.due(now) == ([(f"j{i}", 80)], [])       # вышел сразу
    assert p.waiting() == 0


def test_busy_time_the_strongest_goes_first():
    p = Pacer(12)
    p.offer("first", 1.1, 70, T0)
    assert p.due(T0) == ([("first", 70)], [])            # очередь была пуста: первый выходит сразу
    p.offer("mid", 1.5, 80, T0 + 30)
    p.offer("best", 2.4, 85, T0 + 60)
    p.offer("weak", 1.2, 99, T0 + 90)                    # оценка высокая, но от планки своего канала он слабее всех
    assert p.due(T0 + 299) == ([], [])                   # пять минут с прошлого клипа ещё не прошли
    assert p.eta("best", T0 + 100) == T0 + 300 and p.eta("mid", T0 + 100) == T0 + 600
    assert p.due(T0 + 300) == ([("best", 85)], [])
    assert p.due(T0 + 301) == ([], [])
    assert p.due(T0 + 600) == ([("mid", 80)], [])
    assert p.due(T0 + 900) == ([("weak", 99)], [])       # ждал дольше всех, но тоже вышел: в очереди ему хватило места
    assert p.waiting() == 0


def test_too_many_moments_the_weakest_is_passed_over():
    p = Pacer(12)
    p.offer("out", 1.0, 50, T0)
    p.due(T0)
    assert [p.offer(f"j{r}", r, 60, T0 + 10) for r in (1.4, 1.8, 1.6)] == [[], [], []]
    assert p.waiting() == HOLD == 3
    assert p.offer("strong", 2.0, 60, T0 + 20) == [("j1.4", 60)]         # слабейший из ждущих уступает место
    assert p.offer("weaker", 1.1, 60, T0 + 30) == [("weaker", 60)]       # новый слабее всех: пропускается он сам
    assert p.offer("same", 1.6, 60, T0 + 40) == [("j1.6", 60)]           # равны по силе: уходит ждавший дольше
    assert [j for j, _s in p.flush()] == ["strong", "j1.8", "same"] and p.waiting() == 0


def test_no_limit_means_no_queue():
    p = Pacer(0)
    assert p.gap == 0
    p.offer("a", 1.0, 10, T0)
    p.offer("b", 3.0, 20, T0)
    assert p.due(T0) == ([("b", 20), ("a", 10)], [])
    for bad in (None, "", "много", -5):
        p.set_rate(bad)
        assert p.gap == 0
    p.set_rate(4)
    assert p.gap == 900 and every(p.gap) == "15 мин" and every(300) == "5 мин" and every(30) == "30 с"


def test_limit_removed_while_moments_wait():
    p = Pacer(12)
    p.offer("a", 1.0, 10, T0)
    p.due(T0)
    p.offer("b", 1.0, 20, T0 + 5)
    p.offer("c", 2.0, 30, T0 + 6)
    assert p.due(T0 + 7) == ([], [])
    p.set_rate(0)                                        # человек снял предел в настройках
    assert p.due(T0 + 8) == ([("c", 30), ("b", 20)], [])


def test_moment_that_did_not_become_a_clip_gives_its_place_back():
    p = Pacer(12)
    p.offer("a", 1.0, 10, T0)
    assert p.due(T0)[0] == [("a", 10)]
    p.offer("b", 1.0, 20, T0 + 20)
    assert p.due(T0 + 21) == ([], [])
    p.refund()                                           # «a» оказался обрубком и клипом не стал
    assert p.due(T0 + 22) == ([("b", 20)], [])
    p.refund()
    p.refund()                                           # лишний возврат ничего не ломает
    assert p.due(T0 + 23) == ([], [])


def test_moment_overtaken_for_too_long_is_dropped():
    p = Pacer(12)
    p.offer("a", 3.0, 10, T0)
    p.due(T0)
    p.offer("slow", 1.0, 40, T0 + 1)
    out = []
    for k in range(1, 8):                                # полчаса каждый раз находится момент сильнее
        p.offer(f"s{k}", 2.0 + k, 50, T0 + k * 300 - 10)
        got, old = p.due(T0 + k * 300)
        out += [j for j, _s in got]
        if old:
            assert old == [("slow", 40)] and (k * 300 - 1) > STALE
            break
    else:
        raise AssertionError("слабый момент ждал вечно")
    assert "slow" not in out
    rare = Pacer(1)                                      # один клип в час: ждать приходится дольше, это не «слишком долго»
    rare.offer("a", 1.0, 10, T0)
    rare.due(T0)
    rare.offer("b", 1.0, 10, T0 + 60)
    assert rare.due(T0 + 3000) == ([], []) and rare.due(T0 + 3600) == ([("b", 10)], [])


def test_clock_moved_back_does_not_freeze_the_queue():
    p = Pacer(12)
    p.offer("a", 1.0, 10, T0)
    p.due(T0)
    p.offer("b", 1.0, 10, T0 - 7200)                     # часы перевели на два часа назад
    assert p.due(T0 - 7200) == ([("b", 10)], [])


def test_an_evening_stays_under_the_limit_and_keeps_the_strong():
    """Горячий вечер: шесть стримов дают момент в среднем раз в две минуты. Предел 12 в час."""
    rng = random.Random(5)
    p = Pacer(12)
    t, n, out, lost, ranks = T0, 0, [], [], {}
    for sec in range(3 * 3600):
        t = T0 + sec
        if rng.random() < 1 / 120:
            n += 1
            ranks[f"j{n}"] = round(1.0 + rng.expovariate(2.0), 3)
            lost += [j for j, _s in p.offer(f"j{n}", ranks[f"j{n}"], 50, t)]
        got, old = p.due(t)
        out += [(t, j) for j, _s in got]
        lost += [j for j, _s in old]
    assert len(out) + len(lost) + p.waiting() == n and n > 60
    for h in range(3):
        assert sum(T0 + h * 3600 <= when < T0 + (h + 1) * 3600 for when, _j in out) <= 12
    assert all(b[0] - a[0] >= 300 for a, b in zip(out, out[1:]))
    mean = lambda js: sum(ranks[j] for j in js) / len(js)                # noqa: E731
    assert len(lost) > 20 and mean([j for _t, j in out]) > 1.25 * mean(lost)


# --- сила момента -------------------------------------------------------------
def _ev(edge=1.5, laugh=1.0, score=80):
    return Event(onset=0, peak=5, end=15, score=score, peak_z=12.0, share=0.7, kind="laugh", messages=100,
                 features={"laugh": laugh}, edge=edge)


def test_strength_is_measured_from_the_channels_own_bar():
    assert strength(_ev(edge=2.0)) == 2.0
    assert strength(_ev(edge=2.0)) > strength(_ev(edge=1.2, score=100))      # оценка тут не решает
    assert strength(_ev(laugh=1.0)) > strength(_ev(laugh=0.0)) >= 0.7 * strength(_ev(laugh=1.0))    # смех ценится выше
    assert strength(_ev(), learned=95) > strength(_ev(), learned=80) > strength(_ev(), learned=40)  # вкус по оценкам
    assert strength(_ev(score=1), learned=100) == strength(_ev(score=1)) * 2  # но вкус не решает в одиночку
    assert strength(_ev(score=100), learned=1) == strength(_ev(score=100)) * 0.5


def test_detector_reports_how_far_above_the_bar_a_moment_is():
    det = Detector(3)
    h = {"onset": 0.0, "peak_z": det.z_react * EARLY * 2, "peak_rate": 5.0}
    assert det._edge(h) == pytest.approx(2.0)            # планки ещё нет: считаем от порога первых минут
    for m in range(40):
        det.minute_peaks.append((m, 3.0))
    assert det._edge(dict(h, onset=40 * 60.0, peak_rate=7.5)) == pytest.approx(2.5)
    det.set_sensitivity(5)                               # «брать почти всё»: планки нет вовсе
    assert det._edge(dict(h, onset=40 * 60.0, peak_z=det.z_react * 3)) == pytest.approx(3.0)


def test_real_moments_carry_their_strength():
    from test_detector import BIGS, stream               # тот же живой чат, что в проверках отбора
    det = Detector(3)
    got = []
    tick = det.tick

    def spy(now):
        ev = tick(now)
        if ev:
            got.append(ev)
        return ev

    det.tick = spy
    stream(det, 50, bigs=BIGS)
    assert len(got) >= len(BIGS) and all(ev.edge > 1.0 for ev in got)
    assert all(strength(ev) > 0.75 for ev in got)


# --- в программе ---------------------------------------------------------------
def _sup(monkeypatch=None, per_hour=None):
    sup = Supervisor()
    if per_hour is not None:
        sup.pacer.set_rate(per_hour)
    sup.learner.drop = lambda cid: None
    return sup


def _said(sup):
    return [sup.events.get_nowait()["text"] for _ in range(sup.events.qsize())]


def _queued(sup):
    out = []
    while not sup.edit_q.empty():
        out.append(sup.edit_q.get(0)[0])
        sup.edit_q.done()
    return out


def test_default_limit_and_settings(home):
    assert config.DEFAULTS["clips_per_hour"] == 12
    sup = _sup()
    assert sup.pacer.gap == 300
    sup.tg.reconfigure = lambda cfg: None
    sup.apply_config(dict(sup.cfg, clips_per_hour=30, prevent_sleep=False))
    assert sup.pacer.gap == 120
    sup.apply_config(dict(sup.cfg, clips_per_hour=0))
    assert sup.pacer.gap == 0
    assert config.load()["clips_per_hour"] == 0


def test_moments_wait_for_their_turn_and_the_journal_says_so(home, monkeypatch):
    sup = _sup()
    clock = [T0]
    monkeypatch.setattr("streamclipper.supervisor.time.time", lambda: clock[0])
    a = _job("buster_20261119_210000_aaaaaa", 70)
    b = _job("recrent_20261119_210100_bbbbbb", 80)
    c = _job("t2x2_20261119_210200_cccccc", 90)
    sup._candidate("buster", {"job": a, "score": 70, "rank": 1.2, "kind": "laugh"})
    assert _said(sup) == ["buster: пойман момент (оценка 70), монтирую…"] and _queued(sup) == [a]
    clock[0] = T0 + 60
    sup._candidate("recrent", {"job": b, "score": 80, "rank": 1.4, "kind": "laugh"})
    said = _said(sup)
    until = time.strftime("%H:%M", time.localtime(T0 + 300))
    assert said[0].startswith(f"recrent: пойман момент (оценка 80), ждёт очереди примерно до {until}")
    assert "не чаще раза в 5 мин" in said[0] and "«Клипов в час, не больше»" in said[0]     # объяснение при первом ожидании
    clock[0] = T0 + 120
    sup._candidate("t2x2", {"job": c, "score": 90, "rank": 2.2, "kind": "hype"})
    assert _said(sup) == [f"t2x2: пойман момент (оценка 90), ждёт очереди примерно до {until}"]     # объяснение не повторяется
    assert _queued(sup) == [] and sup.pacer.waiting() == 2
    sup._pace(T0 + 299)
    assert _queued(sup) == []
    sup._pace(T0 + 300)
    assert _queued(sup) == [c] and _said(sup) == ["t2x2: подошла очередь момента (оценка 90), монтирую…"]
    sup._pace(T0 + 600)
    assert _queued(sup) == [b] and _said(sup) == ["recrent: подошла очередь момента (оценка 80), монтирую…"]
    assert all(os.path.exists(j) for j in (a, b, c))     # никто не пропал


def test_weakest_waiting_moment_is_dropped_with_its_files(home, monkeypatch):
    sup = _sup()
    dropped = []
    sup.learner.drop = dropped.append
    clock = [T0]
    monkeypatch.setattr("streamclipper.supervisor.time.time", lambda: clock[0])
    first = _job("ch_20261119_210000_000000", 60)
    sup._candidate("ch", {"job": first, "score": 60, "rank": 1.0})
    jobs = {}
    for k, rank in enumerate((1.3, 1.9, 1.6), 1):
        clock[0] = T0 + k * 10
        jobs[rank] = _job(f"ch{k}_20261119_21010{k}_aaaaa{k}", 50 + k)
        sup._candidate(f"ch{k}", {"job": jobs[rank], "score": 50 + k, "rank": rank})
    _said(sup), _queued(sup)
    clock[0] = T0 + 50
    strong = _job("big_chan_20261119_210200_bbbbbb", 77)
    sup._candidate("big_chan", {"job": strong, "score": 77, "rank": 2.5})
    said = _said(sup)
    assert said[0] == "ch1: момент пропущен (оценка 51): клипов больше, чем задано в час, а в очереди есть сильнее"
    assert said[1].startswith("big_chan: пойман момент (оценка 77), ждёт очереди")
    assert not os.path.exists(jobs[1.3]) and not os.path.exists(jobs[1.3][:-5] + ".ts")
    assert dropped == ["20261119_210101_aaaaa1"]         # и из списка клипов на оценку он убран
    clock[0] = T0 + 60
    weak = _job("ch_20261119_210300_cccccc", 95)
    sup._candidate("ch", {"job": weak, "score": 95, "rank": 1.1})
    assert _said(sup) == ["ch: пойман момент (оценка 95), но пропущен: клипов больше, чем задано в час, "
                          "а в очереди есть сильнее"]
    assert not os.path.exists(weak) and sup.pacer.waiting() == 3 and _queued(sup) == []


def test_clip_now_button_never_waits(home, monkeypatch):
    sup = _sup()
    clock = [T0]
    monkeypatch.setattr("streamclipper.supervisor.time.time", lambda: clock[0])
    a = _job("ch_20261119_210000_aaaaaa", 70)
    sup._candidate("ch", {"job": a, "score": 70, "rank": 1.2})
    for k in range(5):                                   # пять клипов кнопкой подряд: все монтируются сразу
        m = _job(f"ch_20261119_21010{k}_mmmmm{k}", 0, "manual")
        sup._candidate("ch", {"job": m, "score": 0, "kind": "manual"})
    assert len(_queued(sup)) == 6 and sup.pacer.waiting() == 0
    assert _said(sup)[-1] == "ch: пойман момент, монтирую…"
    clock[0] = T0 + 5
    b = _job("ch_20261119_210200_bbbbbb", 70)
    sup._candidate("ch", {"job": b, "score": 70, "rank": 1.2})
    assert sup.pacer.waiting() == 1                      # а на автоматические клипы кнопка не влияет: предел тот же


def test_without_limit_everything_is_edited_at_once(home):
    sup = _sup(per_hour=0)
    jobs = [_job(f"ch_20261119_21000{k}_aaaaa{k}", 60) for k in range(5)]
    for j in jobs:
        sup._candidate("ch", {"job": j, "score": 60, "rank": 1.0})
    assert sorted(_queued(sup)) == sorted(jobs) and all("монтирую" in t for t in _said(sup))


def test_old_recorder_without_strength_is_ranked_by_score(home, monkeypatch):
    sup = _sup()
    monkeypatch.setattr("streamclipper.supervisor.time.time", lambda: T0)
    for k, score in enumerate((50, 40, 90, 60)):
        sup._candidate("ch", {"job": _job(f"ch_20261119_21000{k}_aaaaa{k}", score), "score": score})
    _queued(sup)
    assert [s for _j, s in sup.pacer.flush()] == [90, 60, 40]


def test_stopping_the_recording_sends_waiting_moments_to_editing(home, monkeypatch):
    sup = _sup()
    monkeypatch.setattr("streamclipper.supervisor.time.time", lambda: T0)
    jobs = [_job(f"ch_20261119_21000{k}_aaaaa{k}", 60 + k) for k in range(3)]
    for k, j in enumerate(jobs):
        sup._candidate("ch", {"job": j, "score": 60 + k, "rank": 1.0 + k})
    assert _queued(sup) == [jobs[0]] and sup.pacer.waiting() == 2
    sup.workers["ch"] = WorkerHandle("ch")
    sup.stop_recording()
    assert _queued(sup) == [jobs[2], jobs[1]] and sup.pacer.waiting() == 0
    late = _job("ch_20261119_210100_dddddd", 50)         # запись уже остановлена, а её последний момент дошёл позже
    sup._candidate("ch", {"job": late, "score": 50, "rank": 1.0})
    assert _queued(sup) == [late]


def test_skipped_edit_frees_the_place_for_the_next_moment(home, monkeypatch):
    sup = _sup()
    clock = [T0]
    monkeypatch.setattr("streamclipper.supervisor.time.time", lambda: clock[0])
    a = _job("ch_20261119_210000_aaaaaa", 70)
    b = _job("ch_20261119_210100_bbbbbb", 60)
    sup._candidate("ch", {"job": a, "score": 70, "rank": 1.5})
    clock[0] = T0 + 30
    sup._candidate("ch", {"job": b, "score": 60, "rank": 1.2})
    assert _queued(sup) == [a]
    sup._edit_done(a, {"ok": True, "skipped": "слишком короткий кусок"}, 70, False)
    sup._pace(T0 + 31)
    assert _queued(sup) == [b]                           # клипа не вышло: следующий идёт без ожидания
    sup._edit_done(b, {"ok": True, "skipped": "сам момент не попал в запись"}, 0, True)       # клип по кнопке места не занимал
    c = _job("ch_20261119_210200_cccccc", 60)
    clock[0] = T0 + 40
    sup._candidate("ch", {"job": c, "score": 60, "rank": 1.2})
    assert _queued(sup) == [] and sup.pacer.waiting() == 1


def test_learning_page_tells_about_the_limit():
    text = " ".join(bible.rules({"sensitivity": 3, "clips_per_hour": 12}))
    assert "не больше 12 клипов в час" in text and "первым идёт самый сильный" in text
    assert "клипов в час" not in " ".join(bible.rules({"sensitivity": 3, "clips_per_hour": 0}))
    assert detector.RARE[3] == 0.92                      # сам отбор от предела не зависит


def test_recorder_sends_the_strength_with_each_moment(home, monkeypatch):
    from streamclipper import segments
    from streamclipper.worker import Worker
    w = Worker("ch")
    sent = []
    w.emit = lambda **m: sent.append(m)
    now = time.time()
    segs = []
    for i in range(30):                                  # две минуты записи без дыр
        p = w.buf_dir / f"piece{i}.ts"
        p.write_bytes(b"x" * 1000)
        segs.append(segments.Segment(str(p), 1, now - 120 + i * 4, now - 116 + i * 4, 1000))
    monkeypatch.setattr("streamclipper.worker.segments.scan", lambda folder: segs)
    ev = Event(onset=now - 40, peak=now - 35, end=now - 25, score=80, peak_z=12.0, share=0.7, kind="laugh",
               messages=120, features={"z": 12.0, "share": 0.7, "laugh": 1.0, "dur": 15.0, "msgs": 120, "uniq": 0.8},
               edge=1.8)
    w._make_candidate(ev)
    msg = next(m for m in sent if m["t"] == "candidate")
    assert msg["rank"] == 1.8 and msg["score"] == 80 and msg["kind"] == "laugh"
    with open(msg["job"], encoding="utf-8") as f:
        assert json.load(f)["rank"] == 1.8
    w._make_candidate(None)                              # клип по кнопке: силы у него нет, в очереди он не стоит
    manual = [m for m in sent if m["t"] == "candidate"][-1]
    assert manual["kind"] == "manual" and manual["rank"] == 0
