"""Детектор «движа» в чате.

Идея: у каждого канала своя норма. Мы постоянно меряем, сколько реакций в секунду
обычно (медиана за последние 10 минут), и ловим всплеск относительно ЭТОЙ нормы.
Срабатывание засчитывается только когда чат именно смеётся/орёт, а не спамит командами.
Событие выдаётся ПОСЛЕ того, как движ закончился, чтобы взять момент целиком.

Большой чат посмеивается постоянно, и каждый смешок выше нормы. Поэтому всплеск ещё и сравнивается
с собственными всплесками этого канала за последние полтора часа: клипом становится только то, что входит
в самые сильные минуты, держится несколько секунд и идёт от многих зрителей, а не от пары спамеров.
"""
from __future__ import annotations

import math
import statistics
from collections import Counter, deque
from dataclasses import dataclass, field

from .learning import learnable, tokenize
from .lexicon import classify, normalize, reaction_weight

WINDOW = 5            # сглаживание, секунд
HISTORY = 600         # по скольким секундам считаем норму канала
WARMUP = 75           # первые секунды только учимся
MAX_HYPE = 90         # движ длиннее этого режем принудительно
QUIET = 7             # столько секунд тишины = движ закончился
REFRACTORY = 25       # после события не срабатываем: чат ещё обсуждает тот же момент

#           z реакций, минимум реакций за окно, z общего потока
SENS = {1: (8.0, 16, 4.0), 2: (6.0, 12, 3.2), 3: (4.5, 8, 2.5), 4: (3.4, 6, 2.0), 5: (2.6, 4, 1.6)}
# Всплеск должен быть сильнее такой доли минут за последние полтора часа. 0.92 = входит в 8% самых сильных
# минут, то есть не больше примерно пяти клипов в час с одного канала. 0 = не сравнивать.
RARE = {1: 0.97, 2: 0.95, 3: 0.92, 4: 0.80, 5: 0.0}
RARE_HISTORY = 90     # по скольким минутам судим, что для канала «сильно»
RARE_MIN = 15         # пока минут наблюдения меньше, сравнивать не с чем: берём только заметно сильные всплески
EARLY = 3.0           # во сколько раз строже порог, пока сравнивать не с чем
MIN_DUR = 8.0         # реакция короче этого = чат «чуть посмеялся»
MIN_USERS = 6         # реагировало меньше зрителей = это не чат, а пара человек
MIN_UNIQ = 0.4        # доля разных зрителей среди сообщений; ниже = спам одних и тех же
# После клипа столько секунд с этого канала берётся только момент заметно сильнее прошлого. В горячие минуты чат
# взрывается раз за разом, и без паузы один стрим давал по пять клипов за восемь минут (проверка на Twitch).
PAUSE = {1: 600, 2: 420, 3: 300, 4: 150, 5: 0}
STRONGER = 1.3        # во сколько раз сильнее прошлого клипа должна быть реакция, чтобы не ждать конца паузы


@dataclass
class Event:
    onset: float          # когда чат начал реагировать
    peak: float           # самый жир
    end: float            # когда утихло
    score: int            # 0..100
    peak_z: float
    share: float          # доля смеха/реакций в сообщениях на пике
    kind: str             # laugh | hype
    messages: int
    top: list[str] = field(default_factory=list)
    features: dict = field(default_factory=dict)      # для самообучения
    tokens: list[str] = field(default_factory=list)   # слова, которые «выстрелили» именно в этот момент
    chat: list[tuple[float, str, str]] = field(default_factory=list)


class Detector:
    def __init__(self, sensitivity: int = 3):
        self.words_pos: set[str] = set()      # выученные слова-реакции этого канала
        self.words_neg: set[str] = set()      # выученный мусор (спам, боты)
        self.tok_base: Counter = Counter()    # как часто слова встречаются в обычном чате
        self.tok_msgs = 0
        self.set_sensitivity(sensitivity)
        self.bins: dict[int, list[float]] = {}        # сек -> [всего, реакции, смех, хайп]
        self.msgs: deque[tuple[float, str, str]] = deque()
        self.hist_total: deque[float] = deque(maxlen=HISTORY)
        self.hist_react: deque[float] = deque(maxlen=HISTORY)
        self.last_sec: int | None = None
        self.started: float | None = None
        self.hype: dict | None = None
        self.block_until = 0.0
        # самые сильные реакции по минутам: с ними сравнивается каждый новый всплеск
        self.minute_peaks: deque[tuple[int, float]] = deque(maxlen=RARE_HISTORY)
        self._minute: tuple[int, float] | None = None
        self.skipped: Counter = Counter()     # сколько всплесков отсеяно и почему (для журнала и «библии»)
        self.last_clip: tuple[float, float] | None = None     # последний клип: (когда кончилась реакция, её сила)
        self.last_skip = ""
        # для интерфейса
        self.rate = 0.0
        self.level = 0.0

    def set_sensitivity(self, s: int, mult: float = 1.0) -> None:
        """mult: поправка, выученная для канала (больше 1 = строже, меньше 1 = чувствительнее)."""
        zr, mr, zt = SENS.get(int(s), SENS[3])
        self.z_react, self.min_react, self.z_total = zr * mult, max(3, round(mr * mult)), zt * mult
        self.rare = RARE.get(int(s), RARE[3])
        self.pause = PAUSE.get(int(s), PAUSE[3])

    def set_words(self, pos: set[str], neg: set[str]) -> None:
        self.words_pos, self.words_neg = set(pos), set(neg)

    def restart(self, now: float) -> None:
        """Норма чата считается заново: стрим только что начался или чат долго был недоступен.

        Иначе первый же наплыв зрителей в начале стрима выглядит «всплеском» на фоне почти пустого
        чата до эфира, и программа вырезает заставку.
        """
        self.hist_total.clear()
        self.hist_react.clear()
        self.minute_peaks.clear()
        self._minute = None
        self.hype = None
        self.started = now
        self.block_until = 0.0
        self.last_clip = None

    # --- ввод ---------------------------------------------------------------
    def add_message(self, t: float, user: str, text: str) -> None:
        laugh, hype, clip = classify(text)
        toks = tokenize(text) if text and text[0] not in "!/" else set()
        learned = False
        if toks:
            if self.words_neg and toks & self.words_neg:
                laugh = hype = clip = False              # выученный мусор не считаем реакцией
            elif self.words_pos and toks & self.words_pos and not laugh:
                hype = learned = True                    # выученная реакция этого канала
            if self.hype is None:
                self.tok_base.update(toks)
                self.tok_msgs += 1
                if self.tok_msgs > 20000:                # постепенно забываем старое
                    self.tok_base = Counter({k: v // 2 for k, v in self.tok_base.items() if v > 1})
                    self.tok_msgs //= 2
        b = self.bins.setdefault(int(t), [0.0, 0.0, 0.0, 0.0])
        b[0] += 1
        b[1] += reaction_weight(laugh, hype, clip) + (0.3 if learned else 0.0)
        b[2] += 1 if laugh else 0
        b[3] += 1 if (hype or clip) and not laugh else 0
        self.msgs.append((t, user, text))

    # --- раз в секунду -----------------------------------------------------
    def tick(self, now: float) -> Event | None:
        cur = int(now) - 1
        if self.started is None:
            self.started = now
        if self.last_sec is None:
            self.last_sec = cur - 1
        if cur - self.last_sec > 120:        # ноутбук спал: начинаем учиться заново
            self.hist_total.clear()
            self.hist_react.clear()
            self.minute_peaks.clear()
            self._minute = None
            self.hype = None
            self.started = now
            self.last_sec = cur - 1
        ev = None
        while self.last_sec < cur:
            self.last_sec += 1
            ev = self._step(self.last_sec) or ev
        while self.msgs and self.msgs[0][0] < now - 240:
            self.msgs.popleft()
        for k in [k for k in self.bins if k < cur - 240]:
            del self.bins[k]
        return ev

    def rare_bar(self, before: float) -> float | None:
        """Какой силы реакцию (сообщений-реакций в секунду) надо превзойти, чтобы попасть в самые сильные минуты.
        before: минуты начиная с этой секунды не считаются, в них сам проверяемый всплеск. None: судить рано."""
        if not self.rare:
            return None
        vals = sorted(v for (m, v) in self.minute_peaks if m < int(before) // 60)
        if len(vals) < RARE_MIN:
            return None
        return vals[min(len(vals) - 1, max(0, math.ceil(self.rare * len(vals)) - 1))]

    def _too_weak(self, h: dict) -> str:
        """Почему всплеск не дотягивает до клипа. Пустая строка: дотягивает."""
        if not self.rare:
            return ""
        bar = self.rare_bar(h["onset"])
        if bar is None:
            if h["peak_z"] < self.z_react * EARLY:
                return "для первых минут наблюдения реакция слабовата"
        elif h["peak_rate"] <= bar:
            return f"у этого чата бывает сильнее ({h['peak_rate']:.1f} реакции/с при планке {bar:.1f})"
        last = self.last_clip
        if last and h["onset"] - last[0] < self.pause and h["peak_rate"] < STRONGER * last[1]:
            return (f"клип с этого канала только что был, а этот момент не сильнее "
                    f"({h['peak_rate']:.1f} реакции/с против {last[1]:.1f})")
        return ""

    def _skip(self, why: str) -> None:
        self.skipped[why.split(" (")[0]] += 1
        self.last_skip = why
        return None

    def _window(self, s: int) -> tuple[float, float, float, float]:
        tot = rea = lau = hyp = 0.0
        for k in range(s - WINDOW + 1, s + 1):
            b = self.bins.get(k)
            if b:
                tot += b[0]; rea += b[1]; lau += b[2]; hyp += b[3]
        return tot, rea, lau, hyp

    @staticmethod
    def _base(hist: deque[float], floor_abs: float, floor_rel: float) -> tuple[float, float]:
        vals = list(hist)[:-15] or list(hist)
        if not vals:
            return 0.0, floor_abs
        med = statistics.median(vals)
        mad = statistics.median(abs(v - med) for v in vals) * 1.4826
        # 1.3*sqrt(med/WINDOW): обычный случайный разброс потока сообщений, его не считаем всплеском
        return med, max(mad, floor_abs, floor_rel * med, 1.3 * math.sqrt(med / WINDOW))

    def _step(self, s: int) -> Event | None:
        tot5, rea5, lau5, hyp5 = self._window(s)
        total_rate, react_rate = tot5 / WINDOW, rea5 / WINDOW
        base_t, spread_t = self._base(self.hist_total, 0.4, 0.3)
        base_r, spread_r = self._base(self.hist_react, 0.3, 0.5)
        z_t = (total_rate - base_t) / spread_t
        z_r = (react_rate - base_r) / spread_r
        share = rea5 / tot5 if tot5 else 0.0
        self.rate = total_rate
        self.level = max(0.0, min(1.0, z_r / (self.z_react * 1.5)))
        minute = s // 60
        if self._minute is None or self._minute[0] != minute:
            if self._minute is not None:
                self.minute_peaks.append(self._minute)
            self._minute = (minute, react_rate)
        elif react_rate > self._minute[1]:
            self._minute = (minute, react_rate)

        if self.hype is None:
            self.hist_total.append(total_rate)
            self.hist_react.append(react_rate)
            warm = self.started is not None and s - self.started >= WARMUP
            hot = (z_r >= self.z_react and rea5 >= self.min_react
                   and z_t >= 1.0 and (share >= 0.3 or z_t >= self.z_total))
            if warm and hot and s >= self.block_until:
                self.hype = {"onset": float(s - WINDOW + 1), "peak": float(s), "peak_rate": react_rate,
                             "peak_z": z_r, "share": share, "base": base_r, "spread": spread_r,
                             "quiet": 0, "laugh": lau5, "hype": hyp5, "last_hot": float(s)}
            return None

        h = self.hype
        if react_rate > h["peak_rate"]:
            h.update(peak=float(s) - WINDOW / 2, peak_rate=react_rate, peak_z=(react_rate - h["base"]) / h["spread"],
                     share=share, laugh=lau5, hype=hyp5)
        calm = h["base"] + max(h["spread"], 0.3 * (h["peak_rate"] - h["base"]))
        if react_rate <= calm:
            h["quiet"] += 1
        else:
            h["quiet"] = 0
            h["last_hot"] = float(s)
        if h["quiet"] < QUIET and s - h["onset"] < MAX_HYPE:
            return None

        self.hype = None
        onset, end = h["onset"], h["last_hot"] - WINDOW / 2 + 1
        end = max(end, h["peak"] + 1)
        why = self._too_weak(h)
        if why:
            return self._skip(why)
        chat = [(t, u, x) for (t, u, x) in self.msgs if onset - 45 <= t <= end + 5]
        inside = [x for (t, _u, x) in chat if onset - 1 <= t <= end + 2]
        counts = Counter(normalize(x) for x in inside if x and x[0] not in "!/")
        top = [p for p, n in counts.most_common(8) if n >= 2 and len(p) > 1][:5]
        # что именно писали: слова, которых в обычном чате этого канала почти нет, а тут их много
        tok_cnt: Counter = Counter()
        users = set()
        clipreq = learned = 0
        for (t, u, x) in chat:
            if onset - 1 <= t <= end + 2 and x and x[0] not in "!/":
                tk = tokenize(x)
                tok_cnt.update(tk)
                users.add(u)
                clipreq += 1 if classify(x)[2] else 0
                learned += 1 if tk & self.words_pos else 0
        n_in = max(1, len(inside))
        if self.rare:                                    # на самой высокой чувствительности берём всё подряд
            if end - onset < MIN_DUR:
                return self._skip("чат отреагировал слишком коротко")
            if len(users) < MIN_USERS or len(users) / n_in < MIN_UNIQ:
                return self._skip("реагировало слишком мало зрителей")
        self.block_until = s + REFRACTORY
        self.last_clip = (end, h["peak_rate"])
        spikes = []
        for tok, n in tok_cnt.most_common(40):
            if n < max(4, 0.04 * n_in):
                break
            usual = (self.tok_base.get(tok, 0) + 1) / (self.tok_msgs + 60)
            if learnable(tok) and (n / n_in) / usual >= 4:
                spikes.append(tok)
        feats = {"z": round(h["peak_z"], 2), "share": round(h["share"], 3),
                 "laugh": round(h["laugh"] / max(1.0, h["laugh"] + h["hype"]), 3), "dur": round(end - onset, 1),
                 "msgs": len(inside), "clipreq": round(clipreq / n_in, 3), "uniq": round(len(users) / n_in, 3),
                 "learned": round(learned / n_in, 3)}
        power = h["peak_z"] * (0.5 + min(1.0, h["share"]))
        score = int(round(100 * (1 - math.exp(-power / 9.0))))
        return Event(onset=onset, peak=h["peak"], end=end, score=max(1, min(100, score)),
                     peak_z=round(h["peak_z"], 1), share=round(h["share"], 2),
                     kind="laugh" if h["laugh"] >= h["hype"] else "hype",
                     messages=len(inside), top=top, features=feats, tokens=spikes[:10], chat=chat[-400:])
