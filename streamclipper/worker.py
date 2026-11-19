"""Рабочий процесс одного стрима.

На каждый канал запускается отдельный процесс: если один зависнет или упадёт,
остальные продолжают писать, а главный процесс перезапустит упавший.
Общение с главным процессом: строки JSON в stdout (от нас) и stdin (нам).
"""
from __future__ import annotations

import json
import logging
import os
import queue
import random
import re
import shlex
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from . import config, segments, util
from .chat import ChatClient
from .detector import Detector, Event
from .learning import Learner, learnable, tokenize

log = logging.getLogger("worker")

GAP = 6.0             # нет данных столько секунд = разрыв (реклама/обрыв), клип через него не режем
PRE_ROLL = 45         # сколько секунд до реакции чата отдаём монтажёру на выбор
POST_ROLL = 8
MAX_RAW = 170


class Worker:
    def __init__(self, channel: str):
        self.channel = channel
        self.cfg = config.load()
        self.buf_dir = config.subdir("buffer") / channel
        self.buf_dir.mkdir(parents=True, exist_ok=True)
        self.cand_dir = config.subdir("candidates")
        self.det = Detector(self.cfg["sensitivity"])
        self.learner = Learner()
        self._apply_learning()
        self.det_lock = threading.Lock()
        self.stop = threading.Event()
        self.cmds: queue.Queue = queue.Queue()
        self.out_lock = threading.Lock()
        self.state = "starting"
        self.detail = ""
        self.title = ""
        self.category = ""
        self.quality = ""
        self.ad_until = 0.0
        self.buf_bytes = 0
        self.clips = 0
        self.last_clip_end = 0.0
        existing = segments.scan(self.buf_dir)
        self.run_no = (existing[-1].run if existing else 0) + 1
        self.chat = ChatClient(channel, self._on_chat)

    def _apply_learning(self) -> None:
        """Подхватывает то, чему программа научилась на оценках: порог и словарь этого канала."""
        try:
            self.det.set_sensitivity(self.cfg["sensitivity"], self.learner.channel_mult(self.channel))
            self.det.set_words(*self.learner.channel_words(self.channel))
        except Exception:
            log.exception("не смог применить обучение")

    # --- связь с главным процессом ---------------------------------------
    def emit(self, **msg) -> None:
        with self.out_lock:
            try:
                sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
                sys.stdout.flush()
            except (OSError, ValueError):
                self.stop.set()

    def _stdin_loop(self) -> None:
        try:
            for line in sys.stdin:
                line = line.strip()
                if line:
                    try:
                        self.cmds.put(json.loads(line))
                    except ValueError:
                        pass
        except Exception:
            pass
        self.stop.set()        # главный процесс закрылся: выходим, сиротами не остаёмся

    def _on_chat(self, t: float, user: str, text: str) -> None:
        with self.det_lock:
            self.det.add_message(t, user, text)

    # --- источник видео -----------------------------------------------------
    def _open_source(self):
        fake = os.environ.get("STREAMCLIPPER_FAKE_STREAM")
        if fake:                                    # для тестов: любая команда, выдающая MPEG-TS
            p = subprocess.Popen(shlex.split(fake), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self.quality, self.title = "тест", "Тестовый стрим"
            p.read = p.stdout.read                  # type: ignore[attr-defined]
            p.close = p.kill                        # type: ignore[attr-defined]
            return p
        from streamlink import Streamlink
        from streamlink.options import Options

        session = Streamlink()
        session.set_option("stream-timeout", 120)
        session.set_option("hls-segment-attempts", 5)
        session.set_option("hls-playlist-reload-attempts", 5)
        opts = Options()
        tok = (self.cfg.get("twitch_oauth") or "").strip()
        if tok:
            tok = tok.split(":", 1)[-1] if tok.lower().startswith("oauth:") else tok
            opts.set("api-header", [("Authorization", f"OAuth {tok}")])
        url = f"https://www.twitch.tv/{self.channel}"
        _name, pluginclass, resolved = session.resolve_url(url)
        plugin = pluginclass(session, resolved, opts)
        streams = plugin.streams()
        if not streams:
            return None
        try:
            self.title = plugin.get_title() or ""
            self.category = plugin.get_category() or ""
        except Exception:
            pass
        name = pick_quality(list(streams), self.cfg.get("quality", "best"))
        self.quality = name
        return streams[name].open()

    def _start_ffmpeg(self) -> subprocess.Popen:
        self.run_no += 1
        cmd = [util.find_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-fflags", "+genpts+discardcorrupt",
               "-f", "mpegts", "-i", "pipe:0", "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy",
               "-f", "segment", "-segment_time", str(segments.SEG_TIME), "-segment_format", "mpegts",
               "-strftime", "1", "-reset_timestamps", "0", segments.seg_pattern(self.buf_dir, self.run_no)]
        return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=util.NO_WINDOW)

    @staticmethod
    def _close_ffmpeg(ff: subprocess.Popen | None) -> None:
        if not ff:
            return
        try:
            ff.stdin.close()
        except OSError:
            pass
        try:
            ff.wait(8)
        except subprocess.TimeoutExpired:
            ff.kill()

    def _pump(self, src) -> None:
        q: queue.Queue = queue.Queue(maxsize=512)

        def reader():
            try:
                while not self.stop.is_set():
                    data = src.read(188 * 512)
                    if not data:
                        break
                    q.put(data)
            except Exception as e:
                log.info("поток закончился: %s", e)
            q.put(None)

        threading.Thread(target=reader, daemon=True, name="reader").start()
        ff = None
        last = time.time()
        try:
            while not self.stop.is_set():
                try:
                    chunk = q.get(timeout=1.0)
                except queue.Empty:
                    idle = time.time() - last
                    if ff and idle > GAP:
                        self._close_ffmpeg(ff)
                        ff = None
                        self.state = "ad" if time.time() < self.ad_until + 20 else "pause"
                    if idle > 600:
                        break
                    continue
                if chunk is None:
                    break
                if ff is None or ff.poll() is not None:
                    self._close_ffmpeg(ff)
                    ff = self._start_ffmpeg()
                self.state = "live"
                try:
                    ff.stdin.write(chunk)
                except OSError:
                    self._close_ffmpeg(ff)
                    ff = None
                last = time.time()
        finally:
            self._close_ffmpeg(ff)

    def _record_loop(self) -> None:
        fails = 0
        while not self.stop.is_set():
            if self.cfg.get("paused"):
                self.state = "paused"
                self.stop.wait(3)
                continue
            src = None
            try:
                src = self._open_source()
                fails = 0
            except Exception as e:
                fails += 1
                text = str(e)
                self.state, self.detail = "error", text[:200]
                log.warning("не удалось открыть стрим: %s", text)
                self.stop.wait(min(20 * fails, 180) + random.random() * 5)
                continue
            if src is None:
                self.state, self.detail = "offline", ""
                self.stop.wait(40 + random.random() * 20)
                continue
            self.detail = ""
            log.info("стрим в эфире, качество %s", self.quality)
            self.emit(t="event", kind="online", title=self.title, quality=self.quality)
            started = time.time()
            try:
                self._pump(src)
            except Exception:
                log.exception("ошибка записи")
            finally:
                try:
                    src.close()
                except Exception:
                    pass
            if not self.stop.is_set():
                self.state = "offline"
                if time.time() - started > 120:
                    self.emit(t="event", kind="offline")
                self.stop.wait(8)

    # --- клипы -----------------------------------------------------------
    def _make_candidate(self, ev: Event | None) -> None:
        try:
            now = time.time()
            if util.free_gb(self.cand_dir) < 1.0:
                self.emit(t="event", kind="disk_full")
                return
            if ev is None:
                length = float(self.cfg.get("manual_len", 45))
                want_start, want_end, anchor = now - length - 6, now, now - 8
                time.sleep(segments.SEG_TIME + 1)          # даём дописаться последнему кусочку
            else:
                want_start = ev.onset - PRE_ROLL
                want_end = min(ev.end + POST_ROLL, want_start + MAX_RAW)
                anchor = ev.peak
                deadline = time.time() + 20                # ждём, пока хвост окажется на диске
                while time.time() < deadline and not self.stop.is_set():
                    segs = segments.scan(self.buf_dir)
                    if segs and segs[-1].start >= want_end:
                        break
                    time.sleep(1)
            segs, start, end = segments.select(segments.scan(self.buf_dir), want_start, want_end, anchor)
            if not segs:
                log.info("для события нет видео в буфере (реклама или стрим не шёл)")
                if ev is None:
                    self.emit(t="event", kind="no_video")
                return
            cid = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
            raw = self.cand_dir / f"{self.channel}_{cid}.ts"
            with open(raw, "wb") as out:
                for s in segs:
                    try:
                        with open(s.path, "rb") as f:
                            while True:
                                b = f.read(1 << 20)
                                if not b:
                                    break
                                out.write(b)
                    except OSError:
                        pass
            raw_start = segs[0].start
            job = {
                "id": cid, "channel": self.channel, "raw": str(raw), "raw_start": raw_start,
                "raw_dur": segs[-1].end - raw_start, "title": self.title, "category": self.category,
                "kind": "manual" if ev is None else ev.kind,
                # авто-клипы не должны повторять друг друга; ручной клип берём целиком, как просили
                "not_before": 0.0 if ev is None else max(0.0, self.last_clip_end - raw_start),
                "win_start": start - raw_start, "win_end": end - raw_start,
            }
            if ev is None:
                job.update(onset=end - raw_start - 10, peak=end - raw_start - 8, end=end - raw_start,
                           score=0, base_score=0, peak_z=0, share=0, messages=0, top=[], chat=[])
                with self.det_lock:
                    msgs = [(t, u, x) for (t, u, x) in self.det.msgs if t >= start]
                job["chat"] = [[round(t - raw_start, 1), u, x] for (t, u, x) in msgs][-200:]
                # ручной клип = «этот момент программа пропустила». Запоминаем, чем на него реагировал чат
                cnt: dict[str, int] = {}
                for (_t, _u, x) in msgs:
                    for tok in tokenize(x):
                        cnt[tok] = cnt.get(tok, 0) + 1
                with self.det_lock:
                    base, total = self.det.tok_base, self.det.tok_msgs + 60
                    toks = [k for k, n in sorted(cnt.items(), key=lambda kv: -kv[1])[:40]
                            if n >= 4 and learnable(k)
                            and (n / max(1, len(msgs))) / ((base.get(k, 0) + 1) / total) >= 4][:10]
                self.learner.record(cid, self.channel, "manual", {}, toks, 0, 0)
            else:
                learned = self.learner.predict(self.channel, ev.features, ev.score)
                self.learner.record(cid, self.channel, ev.kind, ev.features, ev.tokens, ev.score, learned)
                job.update(onset=ev.onset - raw_start, peak=ev.peak - raw_start, end=ev.end - raw_start,
                           score=learned, base_score=ev.score, peak_z=ev.peak_z, share=ev.share, messages=ev.messages, top=ev.top,
                           chat=[[round(t - raw_start, 1), u, x] for (t, u, x) in ev.chat])
            self.last_clip_end = end
            tmp = str(raw) + ".json.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(job, f, ensure_ascii=False)
            os.replace(tmp, str(raw)[:-3] + ".json")
            self.clips += 1
            self.emit(t="candidate", job=str(raw)[:-3] + ".json", score=job["score"], kind=job["kind"])
        except Exception:
            log.exception("не получилось собрать заготовку клипа")

    # --- главный цикл ---------------------------------------------------
    def run(self) -> int:
        threading.Thread(target=self._stdin_loop, daemon=True, name="stdin").start()
        threading.Thread(target=self._record_loop, daemon=True, name="rec").start()
        if os.environ.get("STREAMCLIPPER_FAKE_CHAT"):
            threading.Thread(target=_fake_chat, args=(self,), daemon=True).start()
        else:
            self.chat.start()
        _watch_ads(self)
        n = 0
        while not self.stop.is_set():
            time.sleep(1.0)
            n += 1
            now = time.time()
            while True:
                try:
                    cmd = self.cmds.get_nowait()
                except queue.Empty:
                    break
                c = cmd.get("cmd")
                if c == "stop":
                    self.stop.set()
                elif c == "clip_now":
                    threading.Thread(target=self._make_candidate, args=(None,), daemon=True).start()
                elif c == "reload":
                    self.cfg = config.load()
                    with self.det_lock:
                        self._apply_learning()
            with self.det_lock:
                ev = self.det.tick(now)
                rate, level, hot = self.det.rate, self.det.level, self.det.hype is not None
            if ev:
                threading.Thread(target=self._make_candidate, args=(ev,), daemon=True).start()
            if n % 5 == 0:
                keep = max(4, float(self.cfg.get("buffer_minutes", 6))) * 60
                self.buf_bytes = segments.prune(self.buf_dir, keep, now)
            if n % 2 == 0:
                self.emit(t="status", state=self.state, detail=self.detail, rate=round(rate, 1),
                          level=round(level, 2), hot=hot, buf_mb=round(self.buf_bytes / 1e6),
                          chat=self.chat.connected, title=self.title, quality=self.quality)
        self.chat.stop()
        time.sleep(0.3)
        return 0


def pick_quality(names: list[str], want: str) -> str:
    """best = как у стримера. Число = не выше этой высоты кадра (экономит диск и интернет)."""
    want = str(want or "best").lower().rstrip("p")
    if not want.isdigit():
        return "best" if "best" in names else names[-1]
    limit = int(want)
    ranked = []
    for n in names:
        m = re.fullmatch(r"(\d+)p(\d+)?", n)
        if m:
            ranked.append((int(m.group(1)), int(m.group(2) or 30), n))
    ok = [r for r in ranked if r[0] <= limit]
    if ok:
        return max(ok)[2]
    if ranked:
        return min(ranked)[2]
    return "best" if "best" in names else names[-1]


def _fake_chat(worker: Worker) -> None:
    """Для тестов: обычный чат, а на указанной секунде 15 секунд смеха."""
    burst_at = float(os.environ.get("STREAMCLIPPER_FAKE_CHAT") or 95)
    t0 = time.time()
    rng = random.Random(7)
    calm = ["привет", "какая игра", "го катку", "норм", "стример топ", "да", "нет", "когда турнир"]
    fun = ["АХАХАХАХ", "ахахах", "KEKW", "ору", "пхпхпх", "что он несёт АХАХА", "LUL", "в нарезку"]
    worker.chat.connected = True
    while not worker.stop.is_set():
        el = time.time() - t0
        hot = burst_at <= el < burst_at + 15
        for _ in range(rng.randint(30, 45) if hot else rng.randint(6, 12)):
            text = rng.choice(fun) if (hot and rng.random() < 0.85) or rng.random() < 0.05 else rng.choice(calm)
            worker._on_chat(time.time(), f"user{rng.randint(1, 500)}", text)
        time.sleep(1)


def _watch_ads(worker: Worker) -> None:
    """streamlink сам выбрасывает рекламные куски и пишет об этом в лог. Запоминаем, чтобы показать статус."""
    pat = re.compile(r"advertisement break of (\d+) second")

    class H(logging.Handler):
        def emit(self, record):
            try:
                msg = record.getMessage()
            except Exception:
                return
            m = pat.search(msg)
            if m:
                worker.ad_until = time.time() + int(m.group(1))
                log.info("реклама %s с: пропускаю", m.group(1))
            elif "pre-roll" in msg:
                worker.ad_until = time.time() + 30

    logging.getLogger("streamlink").addHandler(H())
    logging.getLogger("streamlink").setLevel(logging.INFO)


def main(channel: str) -> int:
    util.setup_logging(f"worker_{channel}")
    from . import updater
    updater.activate_streamlink()
    log.info("старт записи канала %s", channel)
    return Worker(channel).run()
