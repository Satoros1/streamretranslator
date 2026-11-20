"""Самообучение. Всё локально, без токенов и без интернета.

Программа запоминает каждый пойманный момент (как вёл себя чат) и то, что с ним стало:
понравился клип владельцу (👍) или нет (👎). На этих примерах она учится трём вещам:

1. Оценка. Поверх базовой формулы обучается маленькая модель (логистическая регрессия),
   которая поправляет оценку «смешности» под вкус владельца. Пока оценок мало, модель
   ничего не меняет; чем больше оценок, тем сильнее её голос.
2. Чувствительность по каналам. Много 👎 на канале: порог срабатывания растёт.
   Владелец жмёт «Клип сейчас» (значит момент был пропущен): порог падает.
3. Словарь канала. Слова и эмоуты, которыми именно этот чат реагирует на удачные моменты
   (локальные мемы, эмоуты канала), начинают считаться реакцией. Слова, которые
   всплывают в плохих клипах (спам, боты, рейды), наоборот, перестают учитываться.

Каждая оценка и каждый сделанный из неё вывод записываются в дневник простыми словами (см. bible.py):
его можно прочитать на странице «Обучение».
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
import time

from . import config

FEATURES = ("z", "share", "laugh", "dur", "msgs", "clipreq", "uniq", "learned")
MIN_LABELS = 8          # с этого числа оценок модель начинает влиять
L2 = 2.0
MULT_MIN, MULT_MAX = 0.6, 1.6
DIARY_KEEP = 400        # столько последних записей дневника хранится

_TOK = re.compile(r"[^\W\d_]{2,30}", re.U)
_REP = re.compile(r"(.)\1{2,}", re.U)


def tokenize(text: str) -> set[str]:
    """Слова и названия эмоутов из сообщения, в нижнем регистре, «ааааа» сжимается до «аа»."""
    return {_REP.sub(r"\1\1", t.lower()) for t in _TOK.findall(text or "")}


_STOP = set("""это что как так вот ну да нет не на по за из от до для при был была было есть его её ее их там тут
ещё еще уже все всё кто где когда если или тоже же бы то он она они оно мы вы ты мне меня тебя тебе нас вас
мой твой свой этот эта эти тот чем чтобы почему зачем очень просто надо можно будет быть себя сам""".split())
# английский чат: служебные и самые частые слова, ими реагируют на что угодно, а не на удачный момент
_STOP |= set("""the and for you your youre that this with are was were not but have has had what why how who when
where which while his her him she they them their there here its can cant don dont didn doesn isn wasn aren won
just like get got really one all out about yeah yes yea yep nope too very been from will would could should then
than now some any more much many also only even still back did does doing going gonna wanna gotta know think see
look want make made need say said tell guy guys chat game play playing played stream streamer time good bad man
well okay our into over after before again never ever every thing things something nothing everything way let lets
these those because cause cuz right left new old first last next actually literally maybe sure please thanks thank
people someone everyone today tonight tomorrow day week year""".split())


def learnable(token: str) -> bool:
    """Годится ли слово в словарь канала: не служебное и не то, что и так есть в общем словаре смеха."""
    if len(token) < 3 or token in _STOP:
        return False
    from .lexicon import classify
    return not any(classify(token))


def vector(f: dict) -> list[float]:
    """Признаки момента в сопоставимом масштабе (примерно 0..1)."""
    return [
        min(1.5, math.log1p(max(0.0, f.get("z", 0))) / 3.0),
        min(1.0, f.get("share", 0)),
        min(1.0, f.get("laugh", 0)),
        min(1.5, f.get("dur", 0) / 40.0),
        min(1.5, math.log1p(f.get("msgs", 0)) / 6.0),
        min(1.0, f.get("clipreq", 0) * 10),
        min(1.0, f.get("uniq", 0)),
        min(1.0, f.get("learned", 0)),
    ]


def _sig(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))


def _logit(p: float) -> float:
    p = max(0.03, min(0.97, p))
    return math.log(p / (1 - p))


class Learner:
    def __init__(self, path: str | None = None):
        self.path = path or str(config.data_dir() / "learn.db")
        self.lock = threading.Lock()
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS clips(
                id TEXT PRIMARY KEY, channel TEXT, created REAL, kind TEXT, features TEXT, tokens TEXT,
                base_score INTEGER, score INTEGER, path TEXT DEFAULT '', title TEXT DEFAULT '',
                descr TEXT DEFAULT '', label INTEGER, label_src TEXT DEFAULT '', labeled_at REAL);
            CREATE INDEX IF NOT EXISTS clips_path ON clips(path);
            CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS diary(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, channel TEXT, text TEXT);
            """)
        self._cache: tuple[float, dict] = (0.0, {})

    def _db(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        return db

    # --- запись ------------------------------------------------------------
    def record(self, clip_id: str, channel: str, kind: str, features: dict, tokens: list[str],
               base_score: int, score: int) -> None:
        with self.lock, self._db() as db:
            db.execute("INSERT OR REPLACE INTO clips(id, channel, created, kind, features, tokens, base_score, score)"
                       " VALUES(?,?,?,?,?,?,?,?)",
                       (clip_id, channel, time.time(), kind, json.dumps(features), json.dumps(tokens, ensure_ascii=False),
                        base_score, score))

    def set_clip(self, clip_id: str, path: str, title: str, descr: str, score: int) -> None:
        with self.lock, self._db() as db:
            db.execute("UPDATE clips SET path=?, title=?, descr=?, score=? WHERE id=?",
                       (path, title, descr, score, clip_id))

    def drop(self, clip_id: str) -> None:
        """Момент не стал клипом (отбракован): в списке его не показываем."""
        with self.lock, self._db() as db:
            db.execute("DELETE FROM clips WHERE id=? AND path=''", (clip_id,))

    def label_path(self, path: str, good: bool | None, src: str) -> bool:
        # у клипа может быть два файла (вертикальный и обычный): оценка любого из них относится к одному моменту
        twin = re.sub(r"_9x16(\.mp4)$", r"\1", path) if path.endswith("_9x16.mp4") else re.sub(r"(\.mp4)$", r"_9x16\1", path)
        with self.lock, self._db() as db:
            row = db.execute("SELECT id, label, label_src FROM clips WHERE path IN (?, ?)", (path, twin)).fetchone()
        if not row:
            return False
        if src == "telegram" and row["label_src"] == "app":
            return False                              # оценка, поставленная в программе, главнее
        return self.label(row["id"], good, src)

    def label(self, clip_id: str, good: bool | None, src: str = "app") -> bool:
        val = None if good is None else int(bool(good))
        with self.lock, self._db() as db:
            row = db.execute("SELECT label, channel, title FROM clips WHERE id=?", (clip_id,)).fetchone()
            if not row or row["label"] == val:
                return False
            db.execute("UPDATE clips SET label=?, label_src=?, labeled_at=? WHERE id=?",
                       (val, src, time.time(), clip_id))
        if val is not None:
            where = "реакциями в канале" if src == "telegram" else "в программе"
            what = f"клип «{row['title']}»" if row["title"] else "клип"
            self.note(row["channel"], f"Проба: {what} {'понравился' if val else 'не понравился'} (оценка {where}).")
        self.train()
        return True

    # --- дневник ------------------------------------------------------------
    def note(self, channel: str, text: str) -> None:
        """Запись в дневник обучения: что произошло и какой из этого сделан вывод."""
        self.note_many([(channel, text)])

    def note_many(self, entries: list[tuple[str, str]]) -> None:
        if not entries:
            return
        now = time.time()
        with self.lock, self._db() as db:
            db.executemany("INSERT INTO diary(ts, channel, text) VALUES(?,?,?)", [(now, c or "", t) for c, t in entries])
            db.execute("DELETE FROM diary WHERE id <= (SELECT MAX(id) FROM diary) - ?", (DIARY_KEEP,))

    def diary(self, limit: int = 40) -> list[dict]:
        """Последние записи дневника, новые сверху."""
        with self.lock, self._db() as db:
            rows = db.execute("SELECT ts, channel, text FROM diary ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict[str, dict]:
        """По каналам: сколько клипов сделано, сколько понравилось, не понравилось и вырезано кнопкой."""
        with self.lock, self._db() as db:
            rows = db.execute("SELECT channel, SUM(path != '') AS clips, SUM(label = 1) AS good, SUM(label = 0) AS bad, "
                              "SUM(kind = 'manual') AS manual FROM clips GROUP BY channel").fetchall()
        return {r["channel"]: {k: int(r[k] or 0) for k in ("clips", "good", "bad", "manual")} for r in rows}

    # --- обучение -----------------------------------------------------------
    def train(self) -> dict:
        """Пересчитывает всё с нуля по всем оценкам. Это доли секунды даже на тысячах клипов."""
        before = self.model()
        with self.lock, self._db() as db:
            rows = db.execute("SELECT channel, kind, features, tokens, base_score, label FROM clips "
                              "WHERE label IS NOT NULL OR kind='manual' ORDER BY created DESC LIMIT 1500").fetchall()
        auto = [r for r in rows if r["kind"] != "manual" and r["label"] is not None]
        channels = sorted({r["channel"] for r in rows})

        # 1) модель поправки оценки
        w = [0.0] * len(FEATURES)
        bias = {c: 0.0 for c in channels}
        if len(auto) >= MIN_LABELS:
            data = [(vector(json.loads(r["features"] or "{}")), _logit(r["base_score"] / 100.0),
                     r["channel"], float(r["label"])) for r in auto]
            lam = L2 / len(data)
            for _ in range(200):
                gw = [0.0] * len(w)
                gb = {c: 0.0 for c in channels}
                for x, off, ch, y in data:
                    err = _sig(off + sum(a * b for a, b in zip(w, x)) + bias[ch]) - y
                    for i, xi in enumerate(x):
                        gw[i] += err * xi
                    gb[ch] += err
                for i in range(len(w)):
                    w[i] -= 0.3 * (gw[i] / len(data) + lam * w[i])
                for c in channels:
                    bias[c] -= 0.3 * (gb[c] / len(data) + lam * bias[c])

        # 2) чувствительность по каналам
        mult = {}
        for c in channels:
            m = 1.0
            for r in rows:
                if r["channel"] != c:
                    continue
                if r["kind"] == "manual":
                    m *= 0.94                         # пропустили момент: слушаем чат внимательнее
                elif r["label"] == 0:
                    m *= 1.05                         # мусор: строже
                elif r["label"] == 1:
                    m *= 0.985
            mult[c] = round(max(MULT_MIN, min(MULT_MAX, m)), 3)

        # 3) словарь канала
        words: dict[str, dict] = {}
        for c in channels:
            pos: dict[str, float] = {}
            neg: dict[str, float] = {}
            for r in rows:
                if r["channel"] != c:
                    continue
                good = r["label"] == 1 or (r["kind"] == "manual" and r["label"] != 0)
                bad = r["label"] == 0
                if not (good or bad):
                    continue
                for t in json.loads(r["tokens"] or "[]"):
                    (pos if good else neg)[t] = (pos if good else neg).get(t, 0) + 1
            plus = [t for t, n in pos.items() if n >= 3 and n / (n + neg.get(t, 0)) >= 0.75]
            minus = [t for t, n in neg.items() if n >= 3 and n / (n + pos.get(t, 0)) >= 0.75]
            words[c] = {"pos": sorted(plus)[:200], "neg": sorted(minus)[:200]}

        model = {"w": w, "bias": bias, "mult": mult, "words": words, "labels": len(auto), "trained": time.time()}
        with self.lock, self._db() as db:
            db.execute("INSERT OR REPLACE INTO kv(key, value) VALUES('model', ?)", (json.dumps(model, ensure_ascii=False),))
        self._cache = (0.0, {})
        try:
            from . import bible
            self.note_many(bible.lessons(before, model))      # что изменилось после этой оценки, простыми словами
        except Exception:
            pass                                              # дневник не должен мешать обучению
        return model

    def model(self) -> dict:
        ts, m = self._cache
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            mtime = 0.0
        if m and ts >= mtime:
            return m
        try:
            with self.lock, self._db() as db:
                row = db.execute("SELECT value FROM kv WHERE key='model'").fetchone()
            m = json.loads(row["value"]) if row else {}
        except Exception:
            m = {}
        self._cache = (time.time(), m)
        return m

    # --- применение -----------------------------------------------------------
    def predict(self, channel: str, features: dict, base_score: int) -> int:
        m = self.model()
        w = m.get("w") or []
        if len(w) != len(FEATURES) or m.get("labels", 0) < MIN_LABELS:
            return int(base_score)
        z = _logit(base_score / 100.0) + sum(a * b for a, b in zip(w, vector(features)))
        z += (m.get("bias") or {}).get(channel, 0.0)
        return max(1, min(100, int(round(100 * _sig(z)))))

    def channel_mult(self, channel: str) -> float:
        return float((self.model().get("mult") or {}).get(channel, 1.0))

    def channel_words(self, channel: str) -> tuple[set[str], set[str]]:
        w = (self.model().get("words") or {}).get(channel) or {}
        return set(w.get("pos", [])), set(w.get("neg", []))

    def examples(self, channel: str, n: int = 4) -> tuple[list[str], list[str]]:
        """Последние понравившиеся и забракованные клипы канала: подсказка для ИИ о вкусе владельца."""
        out = ([], [])
        with self.lock, self._db() as db:
            for label, bucket in ((1, out[0]), (0, out[1])):
                for r in db.execute("SELECT title, descr FROM clips WHERE channel=? AND label=? AND title!='' "
                                    "ORDER BY labeled_at DESC LIMIT ?", (channel, label, n)):
                    bucket.append((r["title"] + ". " + r["descr"]).strip()[:200])
        return out

    def recent(self, limit: int = 200) -> list[dict]:
        with self.lock, self._db() as db:
            rows = db.execute("SELECT id, channel, created, kind, score, path, title, label FROM clips "
                              "WHERE path!='' ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def summary(self) -> str:
        with self.lock, self._db() as db:
            good = db.execute("SELECT COUNT(*) FROM clips WHERE label=1").fetchone()[0]
            bad = db.execute("SELECT COUNT(*) FROM clips WHERE label=0").fetchone()[0]
        m = self.model()
        nwords = sum(len(v.get("pos", [])) + len(v.get("neg", [])) for v in (m.get("words") or {}).values())
        if good + bad == 0:
            return "Обучение: оценок пока нет. Отмечай клипы кнопками справа, и отбор будет подстраиваться под тебя"
        state = "модель работает" if m.get("labels", 0) >= MIN_LABELS else f"до включения модели ещё {MIN_LABELS - m.get('labels', 0)} оценок"
        return f"Обучение: хороших клипов {good}, плохих {bad} · выучено слов чата: {nwords} · {state}"
