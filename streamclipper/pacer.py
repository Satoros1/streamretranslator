"""Очередь моментов: клипы не идут сплошным потоком, и первым выходит самый сильный.

Отбор по чату уже строгий, но вечером несколько больших стримов взрываются одновременно, и клипов всё равно
может выйти больше, чем хочется смотреть. Поэтому между автоматическими клипами держится промежуток
(настройка «Клипов в час, не больше»). Пойманные моменты ждут своей очереди, и первым выходит самый сильный.
Ждать могут немногие: когда моментов больше, самый слабый пропускается.

В спокойный час никто не ждёт и ничего не пропадает: момент пойман, промежуток уже прошёл, клип монтируется сразу.
Клипы по кнопке «Клип сейчас» сюда не попадают вовсе.
"""
from __future__ import annotations

import threading
from collections import deque
from typing import NamedTuple

HOLD = 3              # столько моментов может ждать очереди; лишний, самый слабый, пропускается
STALE = 1800          # момент ждёт дольше этого: его всё время обходят более сильные, клипом он уже не станет


class Waiting(NamedTuple):
    rank: float       # сила момента: с ней сравниваются моменты разных каналов
    seq: int          # номер по порядку
    t: float          # когда пойман
    job: str          # файл заготовки
    score: int        # оценка, которую видит человек


def _first(it: Waiting) -> tuple[float, int]:
    return (it.rank, -it.seq)         # сильнее раньше, из равных раньше тот, что пойман раньше


class Pacer:
    def __init__(self, per_hour: int = 0, hold: int = HOLD):
        self.hold = hold
        self.gap = 0.0                                   # секунд между клипами; 0 = предела нет
        self._lock = threading.Lock()
        self._wait: list[Waiting] = []
        self._seq = 0
        self._outs: deque[float] = deque(maxlen=8)      # когда выходили последние клипы
        self.set_rate(per_hour)

    def set_rate(self, per_hour) -> None:
        try:
            n = max(0, int(per_hour or 0))
        except (TypeError, ValueError):
            n = 0
        self.gap = 3600.0 / n if n else 0.0

    def offer(self, job: str, rank: float, score: int, now: float) -> list[tuple[str, int]]:
        """Момент встаёт в очередь. Возвращает тех, кому в ней не хватило места: [(файл, оценка)]."""
        lost = []
        with self._lock:
            self._seq += 1
            self._wait.append(Waiting(float(rank), self._seq, now, job, int(score)))
            while len(self._wait) > self.hold:
                worst = min(self._wait, key=lambda it: (it.rank, it.seq))      # из равных уходит ждавший дольше
                self._wait.remove(worst)
                lost.append((worst.job, worst.score))
        return lost

    def due(self, now: float) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
        """Кому пора на монтаж и кто ждал слишком долго: ([(файл, оценка)], [(файл, оценка)])."""
        with self._lock:
            if not self.gap:                             # предел снят: всё, что ждало, идёт на монтаж
                out = sorted(self._wait, key=_first, reverse=True)
                self._wait = []
                return [(it.job, it.score) for it in out], []
            if self._outs and self._outs[-1] > now:      # часы компьютера перевели назад
                self._outs.clear()
            limit = max(STALE, 2 * self.gap)
            old = [it for it in self._wait if now - it.t > limit]
            if old:
                self._wait = [it for it in self._wait if now - it.t <= limit]
            out = []
            if self._wait and (not self._outs or now - self._outs[-1] >= self.gap):
                best = max(self._wait, key=_first)
                self._wait.remove(best)
                self._outs.append(now)
                out.append(best)
            return [(it.job, it.score) for it in out], [(it.job, it.score) for it in old]

    def refund(self) -> None:
        """Момент ушёл на монтаж, но клипом не стал (слишком короткий кусок, дыра в записи): его место
        в расписании освобождается, и следующий из очереди выходит без ожидания."""
        with self._lock:
            if self._outs:
                self._outs.pop()

    def eta(self, job: str, now: float) -> float:
        """Примерно когда до этого момента дойдёт очередь."""
        with self._lock:
            order = [it.job for it in sorted(self._wait, key=_first, reverse=True)]
            pos = order.index(job) if job in order else len(order)
            first = max(now, self._outs[-1] + self.gap) if self._outs else now
            return first + pos * self.gap

    def flush(self) -> list[tuple[str, int]]:
        """Все, кто ждёт, самые сильные первыми. Очередь после этого пуста."""
        with self._lock:
            out = sorted(self._wait, key=_first, reverse=True)
            self._wait = []
            return [(it.job, it.score) for it in out]

    def waiting(self) -> int:
        with self._lock:
            return len(self._wait)


def every(gap: float) -> str:
    """Промежуток между клипами словами: «5 мин», «40 с»."""
    return f"{gap / 60:.0f} мин" if gap >= 90 else f"{gap:.0f} с"
