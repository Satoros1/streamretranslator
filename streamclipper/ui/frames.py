"""Кадры для окна: последний кадр каждого идущего стрима и обложки клипов.

Кадр стрима берётся из кольцевого буфера записи: предпоследний кусочек (последний ещё пишется), один кадр,
уменьшенный. Обложка клипа: кадр из его середины. Всё делает ffmpeg в одном фоновом потоке с пониженным
приоритетом, по одной картинке за раз, поэтому запись и монтаж этого не замечают. Если ffmpeg нет или кадр
не получился, окно просто показывает букву канала.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path

from .. import config, util

log = logging.getLogger("frames")

LIVE_EVERY = 20.0           # как часто обновлять кадр одного стрима, секунд
RETRY_AFTER = 300.0         # кадр не получился: когда пробовать снова
KEEP_DAYS = 30              # обложки старше этого удаляются при запуске


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:80]


class Frames:
    def __init__(self, folder: Path | None = None):
        self.dir = Path(folder) if folder else config.subdir("thumbs")
        self.lock = threading.Lock()
        self.wanted: dict[str, tuple] = {}          # ключ -> задача (вид, источник, куда)
        self.ready: dict[str, tuple[str, float]] = {}   # ключ -> (картинка, когда сделана)
        self.failed: dict[str, float] = {}
        self.asked: dict[str, float] = {}           # стримы: когда последний раз просили кадр
        self.wake = threading.Event()
        self.stop = False
        self._prune()
        threading.Thread(target=self._loop, name="frames", daemon=True).start()

    # --- спрашивает окно -----------------------------------------------------------------------
    def stream(self, channel: str) -> tuple[str, float] | None:
        """Последний кадр стрима (путь, время). Заодно просит обновить его, если он старый."""
        key = "live:" + channel
        now = time.time()
        if now - self.asked.get(key, 0) >= LIVE_EVERY and now - self.failed.get(key, 0) >= LIVE_EVERY:
            self.asked[key] = now
            self._want(key, ("live", channel, self.dir / f"live_{_safe(channel)}.jpg"))
        with self.lock:
            return self.ready.get(key)

    def clip(self, clip_id: str, path: str) -> tuple[str, float] | None:
        """Обложка клипа. Делается один раз и лежит в папке программы."""
        key = "clip:" + clip_id
        out = self.dir / f"clip_{_safe(clip_id)}.jpg"
        with self.lock:
            if key in self.ready:
                return self.ready[key]
        if out.exists():
            got = (str(out), out.stat().st_mtime)
            with self.lock:
                self.ready[key] = got
            return got
        if os.path.exists(path) and time.time() - self.failed.get(key, 0) >= RETRY_AFTER:
            self._want(key, ("clip", path, out))
        return None

    def close(self) -> None:
        self.stop = True
        self.wake.set()

    # --- фон ------------------------------------------------------------------------------------
    def _want(self, key: str, job: tuple) -> None:
        with self.lock:
            self.wanted[key] = job
        self.wake.set()

    def _loop(self) -> None:
        while not self.stop:
            self.wake.wait(5)
            self.wake.clear()
            while not self.stop:
                with self.lock:
                    if not self.wanted:
                        break
                    key, job = next(iter(self.wanted.items()))
                    del self.wanted[key]
                try:
                    ok = self._make(*job)
                except Exception as e:                 # кадр не главное: молча показываем букву
                    log.info("кадр %s не получился: %s", key, e)
                    ok = False
                with self.lock:
                    if ok:
                        self.ready[key] = (str(job[2]), time.time())
                        self.failed.pop(key, None)
                    else:
                        self.failed[key] = time.time()

    def _make(self, kind: str, src: str, out: Path) -> bool:
        if kind == "live":
            seg = self._last_segment(config.subdir("buffer") / src)
            if not seg:
                return False
            return self._ffmpeg(["-i", seg], "scale=384:-2", out)
        # клип: кадр из середины, а если клип короче, то из начала
        return self._ffmpeg(["-ss", "12", "-i", src], "scale=-2:256", out) or \
            self._ffmpeg(["-i", src], "scale=-2:256", out)

    @staticmethod
    def _last_segment(folder: Path) -> str | None:
        try:
            segs = sorted((e for e in os.scandir(folder) if e.name.endswith(".ts") and e.stat().st_size > 50_000),
                          key=lambda e: e.stat().st_mtime)
        except OSError:
            return None
        if len(segs) < 2 or time.time() - segs[-1].stat().st_mtime > 60:
            return None                                 # запись стоит: старый кадр лучше не показывать как живой
        return segs[-2].path

    @staticmethod
    def _ffmpeg(inputs: list[str], scale: str, out: Path) -> bool:
        tmp = out.with_suffix(".tmp.jpg")
        cmd = [util.find_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", *inputs, "-frames:v", "1",
               "-vf", scale, "-q:v", "5", str(tmp)]
        try:
            subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=20, creationflags=util.NO_WINDOW | util.LOW_PRIORITY)
        except (OSError, subprocess.SubprocessError):
            return False
        try:
            if tmp.stat().st_size < 500:
                return False
            os.replace(tmp, out)
            return True
        except OSError:
            return False

    def _prune(self) -> None:
        cutoff = time.time() - KEEP_DAYS * 86400
        try:
            for e in os.scandir(self.dir):
                if e.name.startswith("live_") or e.name.endswith(".tmp.jpg") or e.stat().st_mtime < cutoff:
                    os.remove(e.path)           # кадры стримов с прошлого запуска уже не живые
        except OSError:
            pass
