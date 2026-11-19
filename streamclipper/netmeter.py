"""Успевает ли интернет за стримом.

streamlink качает видео кусочками по ~2 секунды и отдаёт кусочек целиком, когда тот скачан. Пока кусочек
качается быстрее, чем длится, запас есть. Если на скачивание уходит столько же времени, сколько он длится,
или больше, запись отстаёт от эфира: видео приходит с дырами, а в окне появляется «нет данных».

Здесь это измеряется напрямую, по каждому кусочку. Так нехватку интернета можно отличить от рекламы
и от неполадок у самого стримера: в те минуты кусочки просто не качаются, и замер молчит.
"""
from __future__ import annotations

import threading
import time
from collections import deque

WINDOW = 90.0         # судим по последним полутора минутам
BUSY = 45.0           # и только если за это время на скачивание ушло не меньше стольких секунд
SLOW = 0.9            # кусочек качается почти столько же, сколько длится: запаса нет
HEADROOM = 0.8        # новое качество должно занимать не больше этой доли измеренной скорости


class NetMeter:
    def __init__(self, window: float = WINDOW):
        self.window = window
        self._items: deque[tuple[float, float, float, int]] = deque()      # (когда, качался с, длится с, байт)
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._items.clear()

    def add(self, took: float, media: float, size: int, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            self._items.append((now, max(0.0, took), max(0.0, media), max(0, int(size))))
            while self._items and self._items[0][0] < now - self.window:
                self._items.popleft()

    def read(self, now: float | None = None) -> tuple[float, float, float]:
        """(нагрузка, сколько секунд ушло на скачивание, скорость в бит/с) за последние полторы минуты.
        Нагрузка 0.2: кусочек на 2 секунды качается за 0.4 с. Нагрузка 1 и больше: запись отстаёт от эфира."""
        now = time.time() if now is None else now
        with self._lock:
            items = [it for it in self._items if it[0] >= now - self.window]
        took = sum(it[1] for it in items)
        media = sum(it[2] for it in items)
        size = sum(it[3] for it in items)
        return (took / media if media > 0 else 0.0), took, (size * 8 / took if took > 0 else 0.0)

    def slow(self, now: float | None = None) -> bool:
        load, busy, _speed = self.read(now)
        return busy >= BUSY and load >= SLOW


def _size(res) -> int:
    try:
        n = int(res.headers.get("Content-Length") or 0)
    except (TypeError, ValueError, AttributeError):
        n = 0
    if not n and getattr(res, "_content_consumed", False):      # тело уже скачано целиком: посчитать его ничего не стоит
        try:
            n = len(res.content)
        except Exception:
            n = 0
    return n


def attach(src, meter: NetMeter, cap_kbps: float = 0.0) -> bool:
    """Подключает замер к открытому стриму streamlink: засекает, сколько качается каждый кусочек.

    streamlink обновляется сам, и внутри у него что-то может поменяться. Тогда замера просто не будет
    (вернётся False): запись от него не зависит.

    cap_kbps: только для проверок. Изображает медленный интернет: кусочек «качается» не быстрее этой скорости.
    """
    writer = getattr(src, "writer", None)
    fetch = getattr(writer, "fetch", None)
    if writer is None or not callable(fetch):
        return False

    def timed(segment, *args, **kwargs):
        # perf_counter, а не monotonic: у monotonic в Windows шаг около 16 мс, и быстро скачанный кусочек
        # выходил «скачанным за ноль секунд»
        t0 = time.perf_counter()
        res = fetch(segment, *args, **kwargs)
        try:
            media = float(getattr(segment, "duration", 0) or 0)
            took = time.perf_counter() - t0
            if media <= 0 or getattr(segment, "ad", False):
                return res
            if res is None:
                # Кусочек не скачался. Если на это ушло больше, чем он длится, запись отстала ещё сильнее:
                # так бывает, когда интернета совсем не хватает и скачивание обрывается по времени.
                if took >= media:
                    meter.add(took, media, 0)
                return res
            size = _size(res)
            if cap_kbps > 0 and size:
                need = size * 8 / (cap_kbps * 1000.0)
                if need > took:
                    time.sleep(need - took)
                    took = need
            meter.add(took, media, size)
        except Exception:
            pass                                    # замер никогда не должен мешать записи
        return res

    try:
        writer.fetch = timed
    except Exception:
        return False
    return True


def rank(streams: dict) -> list[tuple[int, int, str, int]]:
    """Качества стрима по возрастанию: (высота кадра, кадров/с, имя, бит/с из плейлиста или 0)."""
    import re
    rates: dict[str, int] = {}
    try:
        for st in streams.values():
            mv = getattr(st, "multivariant", None)
            if mv is not None:
                rates = {p.uri: int(p.stream_info.bandwidth or 0) for p in mv.playlists
                         if getattr(p, "stream_info", None) is not None and getattr(p.stream_info, "bandwidth", None)}
                break
    except Exception:
        rates = {}
    out = []
    for name, st in streams.items():
        m = re.fullmatch(r"(\d+)p(\d+)?", name)
        if m:
            out.append((int(m.group(1)), int(m.group(2) or 30), name, rates.get(getattr(st, "url", None), 0)))
    return sorted(out)


FLOOR = 720           # ниже такой высоты кадра запись сама себя не снижает (меняется в настройках)


def floor_of(cfg: dict) -> int:
    """Настройка «при медленном интернете не опускать ниже»: высота кадра. 0: опускать сколько нужно."""
    try:
        return max(0, int(str(cfg.get("quality_floor", FLOOR)).lower().rstrip("p") or 0))
    except (TypeError, ValueError):
        return FLOOR


def lower(ranked: list[tuple[int, int, str, int]], current: str, speed: float,
          floor: int = 0) -> tuple[int, int, str, int] | None:
    """Какое качество взять, когда интернет не успевает за текущим. None: ниже уже некуда.

    speed: измеренная скорость скачивания этого стрима, бит/с. Берётся самое высокое качество, которому её
    хватает с запасом. Если скорости плейлист не называет, спускаемся на одну ступень.
    floor: ниже этой высоты кадра не опускаемся, даже если интернета не хватает и на неё.
    """
    ranked = sorted(ranked)
    if not ranked:
        return None
    cur = next((r for r in ranked if r[2] == current), ranked[-1])      # «best» и незнакомое имя = самое высокое
    below = [r for r in ranked if r[:2] < cur[:2] and r[0] >= floor]
    if not below:
        return None
    if speed > 0 and all(r[3] for r in below):
        fits = [r for r in below if r[3] <= HEADROOM * speed]
        return max(fits) if fits else min(below)    # не влезает ничего: берём самое лёгкое
    return max(below)
