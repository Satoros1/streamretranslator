"""Кольцевой буфер на диске.

Стрим пишется короткими кусочками по ~4 секунды. Старые кусочки постоянно удаляются,
поэтому один стрим занимает на диске всего несколько сотен мегабайт, сколько бы он ни шёл.
Имя файла хранит номер «прогона» (кусок без разрывов и рекламы) и время начала.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

SEG_RE = re.compile(r"r(\d+)_(\d{8})_(\d{6})\.ts$")
SEG_TIME = 4


@dataclass
class Segment:
    path: str
    run: int
    start: float
    end: float
    size: int


def seg_pattern(directory: Path, run: int) -> str:
    return str(directory / f"r{run:05d}_%Y%m%d_%H%M%S.ts")


def scan(directory: Path) -> list[Segment]:
    out: list[Segment] = []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return out
    for n in names:
        m = SEG_RE.match(n)
        if not m:
            continue
        p = os.path.join(directory, n)
        try:
            st = os.stat(p)
        except OSError:
            continue
        start = time.mktime(time.strptime(m.group(2) + m.group(3), "%Y%m%d%H%M%S"))
        out.append(Segment(p, int(m.group(1)), start, max(st.st_mtime, start), st.st_size))
    out.sort(key=lambda s: (s.run, s.start))
    # конец кусочка = начало следующего в том же прогоне (точнее, чем время изменения файла)
    for a, b in zip(out, out[1:]):
        if a.run == b.run:
            a.end = b.start
    return out


def prune(directory: Path, keep_seconds: float, now: float | None = None) -> int:
    """Удаляет всё старше keep_seconds. Возвращает размер оставшегося буфера в байтах."""
    now = now or time.time()
    total = 0
    for s in scan(directory):
        if s.end < now - keep_seconds:
            try:
                os.remove(s.path)
            except OSError:
                pass
        else:
            total += s.size
    return total


def wipe(directory: Path) -> None:
    for s in scan(directory):
        try:
            os.remove(s.path)
        except OSError:
            pass


def select(segs: list[Segment], start: float, end: float, anchor: float) -> tuple[list[Segment], float, float]:
    """Подбирает кусочки для окна [start, end] и никогда не перешагивает разрыв (рекламу).

    anchor: момент, который обязан попасть в клип (пик). Берётся прогон, где он лежит,
    и окно обрезается по границам этого прогона. Возвращает (кусочки, начало, конец).
    """
    runs: dict[int, list[Segment]] = {}
    for s in segs:
        runs.setdefault(s.run, []).append(s)
    best: list[Segment] | None = None
    best_dist = None
    for rs in runs.values():
        lo, hi = rs[0].start, rs[-1].end
        dist = 0.0 if lo <= anchor <= hi else min(abs(anchor - lo), abs(anchor - hi))
        if best_dist is None or dist < best_dist:
            best, best_dist = rs, dist
    if not best or (best_dist or 0) > 15:
        return [], 0.0, 0.0
    start = max(start, best[0].start)
    end = min(end, best[-1].end)
    chosen = [s for s in best if s.end > start and s.start < end]
    if not chosen or end - start < 3:
        return [], 0.0, 0.0
    return chosen, start, end
