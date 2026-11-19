"""Интернет не успевает за стримами: как программа это замечает и что делает. И уведомление «стрим закончился»."""
import json
from types import SimpleNamespace

import pytest

from streamclipper import netmeter
from streamclipper.netmeter import BUSY, HEADROOM, SLOW, NetMeter, attach, lower, rank
from streamclipper.supervisor import OFFLINE_CONFIRM, Supervisor, WorkerHandle
from streamclipper.worker import CAP_FORGET, Worker, pick_quality

# качества как у большого канала Twitch: (высота, кадров/с, имя, бит/с)
LADDER = [(160, 30, "160p", 290_000), (360, 30, "360p", 700_000), (480, 30, "480p", 1_450_000),
          (720, 60, "720p60", 3_400_000), (1080, 60, "1080p60", 8_000_000)]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STREAMCLIPPER_HOME", str(tmp_path / "home"))
    return tmp_path


# --- замер ------------------------------------------------------------------
def test_meter_tells_fast_internet_from_slow():
    m = NetMeter()
    now = 1_000_000.0
    for i in range(45):                                  # 90 секунд эфира, каждый кусочек качается за 0.3 с
        m.add(0.3, 2.0, 2_000_000, now + i * 2)
    load, busy, speed = m.read(now + 90)
    assert load == pytest.approx(0.15) and busy == pytest.approx(13.5)
    assert speed == pytest.approx(2_000_000 * 8 / 0.3)
    assert not m.slow(now + 90)

    m.reset()
    for i in range(30):                                  # интернета не хватает: кусочек на 2 с качается 3 с
        m.add(3.0, 2.0, 2_000_000, now + i * 3)
    load, busy, speed = m.read(now + 90)
    assert load == pytest.approx(1.5) and busy == pytest.approx(90.0)
    assert speed == pytest.approx(2_000_000 * 8 / 3.0)
    assert m.slow(now + 90)


def test_meter_needs_enough_evidence_and_forgets_old_trouble():
    m = NetMeter()
    now = 1_000_000.0
    for i in range(5):                                   # всего 15 секунд медленно: рано судить
        m.add(3.0, 2.0, 1_000_000, now + i * 3)
    assert m.read(now + 15)[0] >= SLOW and m.read(now + 15)[1] < BUSY
    assert not m.slow(now + 15)
    assert m.read(now + 15 + 200) == (0.0, 0.0, 0.0)     # прошло время (реклама, перерыв): старые замеры не считаются
    assert NetMeter().read() == (0.0, 0.0, 0.0)


class _Writer:
    """Как у streamlink: fetch(кусочек) возвращает ответ со скачанным видео или None, если не скачалось."""

    def __init__(self, took=0.0, ok=True, clock=None):
        self.took, self.ok, self.clock, self.calls = took, ok, clock, 0

    def fetch(self, segment):
        self.calls += 1
        if self.clock is not None:
            self.clock[0] += self.took
        if not self.ok:
            return None
        return SimpleNamespace(headers={"Content-Length": str(segment.size)})


def _seg(size=2_000_000, duration=2.0, ad=False):
    return SimpleNamespace(size=size, duration=duration, ad=ad)


@pytest.fixture
def clock(monkeypatch):
    t = [5000.0]
    slept = []

    def sleep(sec):
        slept.append(sec)
        t[0] += sec

    monkeypatch.setattr(netmeter.time, "monotonic", lambda: t[0])
    monkeypatch.setattr(netmeter.time, "sleep", sleep)
    t.append(slept)
    return t


def test_attach_times_every_piece_and_keeps_the_result(clock):
    wr = _Writer(took=3.0, clock=clock)
    src = SimpleNamespace(writer=wr)
    m = NetMeter()
    assert attach(src, m) is True
    res = src.writer.fetch(_seg())
    assert res.headers["Content-Length"] == "2000000" and wr.calls == 1      # запись получает то же, что и раньше
    load, busy, speed = m.read()
    assert load == pytest.approx(1.5) and busy == pytest.approx(3.0)
    assert speed == pytest.approx(2_000_000 * 8 / 3.0)


def test_attach_skips_ads_and_counts_only_slow_failures(clock):
    m = NetMeter()
    src = SimpleNamespace(writer=_Writer(took=4.0, clock=clock))
    attach(src, m)
    src.writer.fetch(_seg(ad=True))                      # реклама Twitch: её запись выбрасывает, скорость по ней не судим
    src.writer.fetch(_seg(duration=0))
    assert m.read() == (0.0, 0.0, 0.0)

    quick = SimpleNamespace(writer=_Writer(took=0.2, ok=False, clock=clock))
    attach(quick, m)
    assert quick.writer.fetch(_seg()) is None            # Twitch сразу ответил ошибкой: интернет тут ни при чём
    assert m.read() == (0.0, 0.0, 0.0)

    stuck = SimpleNamespace(writer=_Writer(took=30.0, ok=False, clock=clock))
    attach(stuck, m)
    assert stuck.writer.fetch(_seg()) is None            # скачивание оборвалось по времени: интернета совсем не хватает
    load, busy, speed = m.read()
    assert load == pytest.approx(15.0) and busy == pytest.approx(30.0) and speed == 0.0


def test_attach_never_breaks_recording():
    assert attach(SimpleNamespace(), NetMeter()) is False            # streamlink поменялся: замера просто нет
    assert attach(SimpleNamespace(writer=SimpleNamespace(fetch=None)), NetMeter()) is False

    class Broken:
        def add(self, *a):
            raise RuntimeError("замер сломался")

    src = SimpleNamespace(writer=_Writer())
    assert attach(src, Broken()) is True
    assert src.writer.fetch(_seg()) is not None          # запись идёт дальше, даже если замер упал


def test_speed_cap_for_checks_makes_pieces_slow(clock):
    wr = _Writer(took=0.1, clock=clock)
    src = SimpleNamespace(writer=wr)
    m = NetMeter()
    attach(src, m, cap_kbps=2500)
    src.writer.fetch(_seg(size=2_000_000))               # 16 Мбит при 2.5 Мбит/с = 6.4 секунды
    assert clock[-1] == [pytest.approx(6.3)]
    load, busy, speed = m.read()
    assert load == pytest.approx(3.2) and speed == pytest.approx(2_500_000)
    src.writer.fetch(_seg(size=20_000))                  # маленький кусочек и так качается быстрее предела
    assert len(clock[-1]) == 1


# --- выбор качества ----------------------------------------------------------
def test_rank_reads_bitrates_from_the_playlist():
    def pl(uri, bw):
        return SimpleNamespace(uri=uri, stream_info=SimpleNamespace(bandwidth=bw))

    mv = SimpleNamespace(playlists=[pl("u1080", 8_000_000), pl("u720", 3_400_000), pl("u480", 1_450_000),
                                    pl("uaudio", 160_000)])
    streams = {"audio_only": SimpleNamespace(url="uaudio", multivariant=mv),
               "480p": SimpleNamespace(url="u480", multivariant=mv),
               "720p60": SimpleNamespace(url="u720", multivariant=mv),
               "1080p60": SimpleNamespace(url="u1080", multivariant=mv),
               "worst": SimpleNamespace(url="u480", multivariant=mv),
               "best": SimpleNamespace(url="u1080", multivariant=mv)}
    assert rank(streams) == [(480, 30, "480p", 1_450_000), (720, 60, "720p60", 3_400_000),
                             (1080, 60, "1080p60", 8_000_000)]
    # плейлист без скоростей или незнакомое устройство streamlink: качества всё равно известны
    plain = {"360p": object(), "720p": object(), "best": object()}
    assert rank(plain) == [(360, 30, "360p", 0), (720, 30, "720p", 0)]
    assert rank({}) == []


def test_lower_picks_the_best_quality_the_internet_can_carry():
    assert lower(LADDER, "1080p60", 5_000_000)[2] == "720p60"        # 3.4 Мбит/с влезает в 80% от 5
    assert lower(LADDER, "1080p60", 4_000_000)[2] == "480p"          # 3.4 уже не влезает в 3.2
    assert lower(LADDER, "best", 2_500_000)[2] == "480p"             # «best» = самое высокое
    assert lower(LADDER, "720p60", 2_500_000)[2] == "480p"
    assert lower(LADDER, "1080p60", 100_000)[2] == "160p"            # не влезает ничего: самое лёгкое
    assert lower(LADDER, "160p", 100_000) is None                    # ниже некуда
    assert lower([], "1080p60", 1_000_000) is None
    assert 0.5 < HEADROOM < 1.0


def test_lower_goes_one_step_down_when_bitrates_are_unknown():
    blind = [r[:3] + (0,) for r in LADDER]
    assert lower(blind, "1080p60", 2_500_000)[2] == "720p60"
    assert lower(blind, "720p60", 2_500_000)[2] == "480p"
    assert lower(LADDER, "1080p60", 0.0)[2] == "720p60"              # скорость измерить не удалось


def test_pick_quality_respects_the_ceiling_set_by_slow_internet():
    names = ["audio_only", "160p", "360p", "480p", "720p60", "1080p60", "worst", "best"]
    assert pick_quality(names, "best") == "best"
    assert pick_quality(names, "best", (480, 30)) == "480p"
    assert pick_quality(names, "best", (720, 60)) == "720p60"
    assert pick_quality(names, "720", (480, 30)) == "480p"           # и настройка, и потолок: берётся меньшее
    assert pick_quality(names, "360", (720, 60)) == "360p"
    assert pick_quality(["720p", "720p60", "best"], "best", (720, 30)) == "720p"


# --- процесс записи ----------------------------------------------------------
def _events(sent):
    return [(m["kind"], m.get("old"), m.get("new")) for m in sent if m.get("t") == "event"]


def test_worker_lowers_quality_and_says_so(home):
    w = Worker("ch")
    sent = []
    w.emit = lambda **m: sent.append(m)
    w.variants, w.quality = list(LADDER), "1080p60"
    assert w._lower_quality(1.4, 2_500_000) is True
    assert w.auto_cap == (480, 30) and w._reopen is True
    assert _events(sent) == [("slow_net", "1080p60", "480p")]


def test_worker_at_the_lowest_quality_complains_rarely(home):
    w = Worker("ch")
    sent = []
    w.emit = lambda **m: sent.append(m)
    w.variants, w.quality = list(LADDER), "160p"
    assert w._lower_quality(2.0, 100_000) is False
    assert w._lower_quality(2.0, 100_000) is False       # проверка идёт каждые 5 секунд, а сказать надо один раз
    assert w.auto_cap is None and w._reopen is False
    assert _events(sent) == [("slow_net", "160p", "")]


class _Src:
    closed = False

    def close(self):
        self.closed = True


def test_quality_change_reopens_the_stream_at_once_and_is_not_a_stream_end(home, monkeypatch):
    w = Worker("ch")
    sent, opened, waits = [], [], []
    w.emit = lambda **m: sent.append(m)
    monkeypatch.setattr(w.stop, "wait", lambda sec=None: waits.append(sec))

    def open_source():
        opened.append(w.auto_cap)
        w.variants = list(LADDER)
        w.quality = "1080p60" if w.auto_cap is None else "480p"
        return _Src()

    def pump(src):
        if len(opened) == 1:
            assert w._lower_quality(1.4, 2_500_000)
        else:
            w.stop.set()

    w._open_source, w._pump = open_source, pump
    w._record_loop()
    assert opened == [None, (480, 30)]
    assert [(m["kind"], m.get("quality") or m.get("new")) for m in sent if m.get("t") == "event"] == [
        ("online", "1080p60"), ("slow_net", "480p"), ("online", "480p")]
    assert waits == []                                   # между качествами запись не ждёт


def test_worker_reports_stream_end_once_and_forgets_the_ceiling_after_a_long_break(home, monkeypatch):
    w = Worker("ch")
    sent = []
    w.emit = lambda **m: sent.append(m)
    w.auto_cap = (480, 30)
    clock = [1_000_000.0]
    monkeypatch.setattr("streamclipper.worker.time.time", lambda: clock[0])
    caps = []

    def wait(sec=None):
        caps.append(w.auto_cap)
        clock[0] += 60
        if len(caps) >= CAP_FORGET // 60 + 3:
            w.stop.set()

    monkeypatch.setattr(w.stop, "wait", wait)
    w._open_source = lambda: None                        # Twitch отвечает: эфира нет
    w._record_loop()
    assert _events(sent) == [("offline", None, None)]    # сказано один раз, даже если запись только что перезапускалась
    assert caps[0] == (480, 30) and caps[5] == (480, 30) # короткий перерыв: качество остаётся сниженным
    assert caps[-1] is None                              # эфира нет давно: в следующий раз пробуем качество из настроек


# --- окно и Telegram ---------------------------------------------------------
class _Proc:
    def __init__(self, *msgs):
        self.stdout = [json.dumps(m) + "\n" for m in msgs]

    def poll(self):
        return None


def _sup(monkeypatch):
    sup = Supervisor()
    alerts = []
    monkeypatch.setattr(sup.tg, "alert", lambda text, key=None, every=1800: alerts.append((key, text)))
    h = sup.workers["ch"] = WorkerHandle("ch")
    h.proc = _Proc()
    return sup, h, alerts


def _said(sup):
    return [sup.events.get_nowait()["text"] for _ in range(sup.events.qsize())]


def _feed(sup, h, *msgs):
    sup._read_worker(h, _Proc(*msgs))


def test_short_break_in_the_stream_is_not_announced(home, monkeypatch):
    sup, h, alerts = _sup(monkeypatch)
    _feed(sup, h, {"t": "event", "kind": "online", "quality": "1080p60"})
    assert _said(sup) == ["ch: стрим в эфире, качество 1080p60"]
    _feed(sup, h, {"t": "event", "kind": "offline"})
    t0 = h.off_at
    assert t0 > 0
    _feed(sup, h, {"t": "event", "kind": "offline"})     # запись повторяет это каждую минуту, отсчёт не сбрасывается
    assert h.off_at == t0
    sup._check(h, t0 + OFFLINE_CONFIRM - 5)
    assert _said(sup) == [] and alerts == []
    _feed(sup, h, {"t": "event", "kind": "online", "quality": "1080p60"})     # эфир вернулся
    h.last_msg = t0 + OFFLINE_CONFIRM + 500
    sup._check(h, t0 + OFFLINE_CONFIRM + 500)
    assert _said(sup) == [] and alerts == [] and h.online


def test_real_stream_end_is_announced_once_after_the_wait(home, monkeypatch):
    sup, h, alerts = _sup(monkeypatch)
    _feed(sup, h, {"t": "event", "kind": "offline"})     # программа только запустилась, эфира и не было: молчим
    assert h.off_at == 0.0
    _feed(sup, h, {"t": "event", "kind": "online", "quality": "720p60"}, {"t": "event", "kind": "offline"})
    _said(sup)
    t0 = h.off_at
    h.last_msg = t0 + OFFLINE_CONFIRM
    sup._check(h, t0 + OFFLINE_CONFIRM + 1)
    sup._check(h, t0 + OFFLINE_CONFIRM + 2)
    assert _said(sup) == ["ch: стрим закончился"]
    assert [k for k, _t in alerts] == ["off-ch"] and not h.online
    _feed(sup, h, {"t": "event", "kind": "online", "quality": "720p60"})      # следующий эфир объявляется как новый
    assert _said(sup) == ["ch: стрим в эфире, качество 720p60"]
    assert 60 <= OFFLINE_CONFIRM <= 180


def test_slow_internet_is_said_in_plain_words(home, monkeypatch):
    sup, h, alerts = _sup(monkeypatch)
    _feed(sup, h, {"t": "event", "kind": "online", "quality": "1080p60"},
          {"t": "event", "kind": "slow_net", "old": "1080p60", "new": "480p"},
          {"t": "event", "kind": "online", "quality": "480p"})                # тот же эфир в новом качестве
    said = _said(sup)
    assert said == ["ch: стрим в эфире, качество 1080p60",
                    "ch: интернет не успевает за качеством 1080p60, дальше записываю в 480p"]
    assert [k for k, _t in alerts] == ["slow-net"] and "Интернет не успевает" in alerts[0][1]
    _feed(sup, h, {"t": "event", "kind": "slow_net", "old": "160p", "new": ""})
    assert "даже за самым низким качеством" in _said(sup)[0]


def test_stream_card_shows_slow_internet_instead_of_no_data(home, monkeypatch):
    sup, h, _alerts = _sup(monkeypatch)
    h.status = {"state": "pause", "slow": True, "quality": "1080p60"}
    row = sup.snapshot()[0]
    assert row["text"] == "интернет не успевает" and row["slow"] is True
    h.status = {"state": "live", "slow": True, "hot": True}
    assert sup.snapshot()[0]["text"] == "интернет не успевает"       # важнее, чем «движ в чате»: клип выйдет с дырами
    h.status = {"state": "pause", "slow": False}
    row = sup.snapshot()[0]
    assert row["text"] == "нет видео от Twitch, жду" and row["slow"] is False
    h.status = {"state": "offline", "slow": True}                    # эфира нет: устаревший замер не показываем
    assert sup.snapshot()[0]["text"] == "не в эфире"
    h.status = {"state": "live"}
    assert sup.snapshot()[0]["text"] == "идёт запись"


# --- настоящий streamlink ------------------------------------------------------
def _hls_server(pieces=6, piece_sec=2.0):
    """Маленький «Twitch» на этом же компьютере: два качества, плейлисты и кусочки видео."""
    import http.server
    import threading

    sizes = {"hi": 400_000, "lo": 50_000}
    files = {"/master.m3u8": ("#EXTM3U\n"
                              '#EXT-X-STREAM-INF:BANDWIDTH=1600000,RESOLUTION=1280x720,FRAME-RATE=30.000\nhi.m3u8\n'
                              '#EXT-X-STREAM-INF:BANDWIDTH=200000,RESOLUTION=640x360,FRAME-RATE=30.000\nlo.m3u8\n').encode()}
    for q in sizes:
        rows = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:2", "#EXT-X-MEDIA-SEQUENCE:0"]
        for i in range(pieces):
            rows += ["#EXTINF:%.3f," % piece_sec, "%s_%d.ts" % (q, i)]
            files["/%s_%d.ts" % (q, i)] = b"\x47" * sizes[q]
        files["/%s.m3u8" % q] = "\n".join(rows + ["#EXT-X-ENDLIST", ""]).encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = files.get(self.path.split("?")[0])
            self.send_response(200 if body is not None else 404)
            self.send_header("Content-Length", str(len(body or b"")))
            self.end_headers()
            self.wfile.write(body or b"")

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, sizes


def test_meter_works_with_the_real_streamlink(monkeypatch):
    """streamlink обновляется сам. Если в нём поменяется то, за что цепляется замер, эта проверка скажет об этом."""
    streamlink = pytest.importorskip("streamlink")
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    srv, sizes = _hls_server()
    try:
        session = streamlink.Streamlink()
        session.http.trust_env = False
        streams = session.streams("hls://http://127.0.0.1:%d/master.m3u8" % srv.server_address[1])
        assert rank(streams) == [(360, 30, "360p", 200_000), (720, 30, "720p", 1_600_000)]

        m = NetMeter()
        src = streams["720p"].open()
        try:
            assert attach(src, m) is True
            got = 0
            while True:
                chunk = src.read(65536)
                if not chunk:
                    break
                got += len(chunk)
        finally:
            src.close()
        assert got == 6 * sizes["hi"]
        with m._lock:
            items = list(m._items)
        # первый кусочек streamlink мог начать качать до подключения замера; остальные посчитаны все
        assert 5 <= len(items) <= 6
        assert all(it[2] == pytest.approx(2.0) and it[3] == sizes["hi"] for it in items)
        load, _busy, speed = m.read()
        assert 0 < load < 0.5 and speed > 1_600_000          # со своего же компьютера качается мгновенно
        assert lower(rank(streams), "720p", 500_000)[2] == "360p"
    finally:
        srv.shutdown()
        srv.server_close()
