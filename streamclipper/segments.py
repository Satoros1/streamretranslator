"""Кольцевой буфер на диске.

Стрим пишется короткими кусочками по ~4 секунды. Старые кусочки постоянно удаляются,
поэтому один стрим занимает на диске всего несколько сотен мегабайт, сколько бы он ни шёл.

Имя кусочка: номер «прогона» (запись без разрывов и рекламы), время его старта и порядковый номер.
Точное место каждого кусочка внутри прогона ffmpeg сам пишет в файл-список рядом (.csv).
Брать время из часов в момент создания файла нельзя: Twitch часто отдаёт видео пачками по
несколько секунд, тогда два кусочка рождаются в одну и ту же секунду.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

SEG_RE = re.compile(r"r(\d+)_(\d+)_(\d+)\.ts$")
LIST_RE = re.compile(r"r(\d+)_(\d+)\.csv$")
SEG_TIME = 4
HOLE = 0.6                    # между соседними кусочками пропало больше этого: в записи дыра (видео не успело скачаться)
LIST_TAIL = 256 * 1024        # из списка читаем только хвост: это часы записи, буфер намного короче


@dataclass
class Segment:
    path: str
    run: int
    start: float
    end: float
    size: int


def run_files(directory: Path, run: int, now: float | None = None) -> tuple[str, str]:
    """Имена для нового прогона: шаблон кусочков для ffmpeg и файл-список с их точным временем."""
    stem = f"r{run:05d}_{int((now or time.time()) * 1000)}"
    folder = str(directory)
    return os.path.join(folder.replace("%", "%%"), stem + "_%06d.ts"), os.path.join(folder, stem + ".csv")


def _read_times(path: str) -> tuple[dict[str, tuple[float, float]], bool]:
    """Список ffmpeg: имя кусочка -> (начало, конец) в секундах записи. Второе значение: прочитан ли список с начала."""
    out: dict[str, tuple[float, float]] = {}
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - LIST_TAIL))
            data = f.read()
    except OSError:
        return out, False
    whole = size <= LIST_TAIL
    lines = data.decode("utf-8", "replace").split("\n")
    lines.pop()                                   # после последнего перевода строки: пусто или недописанная строка
    for line in lines if whole else lines[1:]:
        parts = line.strip().rsplit(",", 2)
        if len(parts) != 3:
            continue
        try:
            out[parts[0].strip('"')] = (float(parts[1]), float(parts[2]))
        except ValueError:
            continue
    return out, whole


def _timeline(run: int, anchor_ms: int, items: list, times: dict, whole: bool) -> list[Segment]:
    """Переводит место кусочков в записи в обычное время по часам.

    Видео приходит рывками и с задержками, но раньше срока прийти не может. Поэтому за точку отсчёта
    берётся самый «быстрый» кусочек прогона: его время закрытия на диске минус место в записи.
    """
    anchor = anchor_ms / 1000.0
    shifts = [st.st_mtime - times[name][1] for (_seq, name, _p, st) in items if name in times]
    first = times.get(f"r{run:05d}_{anchor_ms}_000000.ts") if whole else None
    if first:
        shifts.append(anchor - first[0])          # самое начало записи пришло ровно в момент её старта
    shift = min(shifts) if shifts else None
    out: list[Segment] = []
    for i, (_seq, name, path, st) in enumerate(items):
        if shift is not None and name in times:
            start, end = times[name][0] + shift, times[name][1] + shift
        else:                                     # кусочек ещё пишется, в списке его пока нет
            if out:
                start = out[-1].end
            elif i == len(items) - 1:
                start = anchor
            else:
                start = st.st_mtime - SEG_TIME
            # Когда запись отстаёт от эфира, часы уходят далеко вперёд от видео. Кусочек не бывает намного
            # длиннее положенного, поэтому не верим, что в нём полминуты: иначе клип из него выйдет в 2 секунды.
            end = min(max(st.st_mtime, start), start + 1.5 * SEG_TIME)
        out.append(Segment(path, run, start, end, st.st_size))
    # Конец кусочка = начало следующего в том же прогоне: без щелей и наложений. Но если между ними пропало
    # видео (интернет не успел его скачать), щель остаётся: это дыра, клип через неё резать нельзя.
    for a, b in zip(out, out[1:]):
        if b.start - a.end <= HOLE:
            a.end = max(a.start, b.start)
    return out


def scan(directory: Path) -> list[Segment]:
    out: list[Segment] = []
    try:
        names = os.listdir(directory)
    except OSError:
        return out
    runs: dict[tuple[int, int], list] = {}
    for n in names:
        m = SEG_RE.match(n)
        if not m:
            continue
        p = os.path.join(directory, n)
        try:
            st = os.stat(p)
        except OSError:
            continue
        runs.setdefault((int(m.group(1)), int(m.group(2))), []).append((int(m.group(3)), n, p, st))
    for (run, anchor_ms), items in sorted(runs.items()):
        items.sort()
        times, whole = _read_times(os.path.join(directory, f"r{run:05d}_{anchor_ms}.csv"))
        out += _timeline(run, anchor_ms, items, times, whole)
    return out


def _drop_lists(directory: Path, older_than: float | None) -> None:
    """Убирает списки прогонов, от которых на диске уже не осталось ни одного кусочка."""
    try:
        names = os.listdir(directory)
    except OSError:
        return
    alive = {m.group(1, 2) for m in map(SEG_RE.match, names) if m}
    for n in names:
        m = LIST_RE.match(n)
        if not m or m.group(1, 2) in alive:
            continue
        p = os.path.join(directory, n)
        try:
            if older_than is None or os.stat(p).st_mtime < older_than:
                os.remove(p)
        except OSError:
            pass


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
    _drop_lists(directory, now - 120)             # свежий список не трогаем: его прогон мог ещё не начать писать
    return total


def wipe(directory: Path) -> None:
    for s in scan(directory):
        try:
            os.remove(s.path)
        except OSError:
            pass
    _drop_lists(directory, None)


def pieces(segs: list[Segment]) -> list[list[Segment]]:
    """Непрерывные отрезки записи. Внутри отрезка нет ни рекламы, ни дыр: из него можно резать клип."""
    out: list[list[Segment]] = []
    for s in segs:
        if out and out[-1][-1].run == s.run and s.start - out[-1][-1].end <= HOLE:
            out[-1].append(s)
        else:
            out.append([s])
    return out


def select(segs: list[Segment], start: float, end: float, anchor: float,
           slack: float = 15.0) -> tuple[list[Segment], float, float]:
    """Подбирает кусочки для окна [start, end] и никогда не перешагивает разрыв: ни рекламу, ни дыру в записи.

    anchor: момент, который обязан попасть в клип (пик). Берётся непрерывный отрезок записи, где он лежит,
    и окно обрезается по границам этого отрезка. slack: на сколько секунд отрезок может не доставать до anchor.
    Возвращает (кусочки, начало, конец).
    """
    best: list[Segment] | None = None
    best_dist = None
    for rs in pieces(segs):
        lo, hi = rs[0].start, rs[-1].end
        dist = 0.0 if lo <= anchor <= hi else min(abs(anchor - lo), abs(anchor - hi))
        if best_dist is None or dist < best_dist:
            best, best_dist = rs, dist
    if not best or (best_dist or 0) > slack:
        return [], 0.0, 0.0
    start = max(start, best[0].start)
    end = min(end, best[-1].end)
    chosen = [s for s in best if s.end > start and s.start < end]
    if not chosen or end - start < 3:
        return [], 0.0, 0.0
    return chosen, start, end
