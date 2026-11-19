"""Чат стрима поверх вертикального клипа.

Сообщения берутся из записи чата с точным временем и появляются в клипе тогда же, когда появлялись
в эфире. Чат большого канала летит быстрее, чем можно прочитать, поэтому показывается не каждое
сообщение: чем живее чат, тем чаще новые сообщения, но не чаще, чем успевает глаз.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from . import lexicon

BOTS = {"nightbot", "streamelements", "moobot", "fossabot", "wizebot", "streamlabs", "soundalerts", "sery_bot",
        "pokemoncommunitygame", "botrixoficial", "own3d", "kofistreambot", "streamstickers", "tangiabot"}
LINK = re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|ru|net|org|tv|gg|me|io|cc|su|xyz|link|ly|рф)\b", re.I)
PRE = 6.0            # сколько секунд чата до начала клипа показать сразу, чтобы чат не был пустым
MAX_RATE = 5.5       # больше сообщений в секунду глаз не успевает
# Цвета, которые Twitch раздаёт зрителям, не выбравшим свой
DEFAULT_COLORS = ["#FF0000", "#0000FF", "#008000", "#B22222", "#FF7F50", "#9ACD32", "#FF4500", "#2E8B57",
                  "#DAA520", "#D2691E", "#5F9EA0", "#1E90FF", "#FF69B4", "#8A2BE2", "#00FF7F"]
SIZES = {"s": 30, "m": 36, "l": 44}


def hide_list(text: str) -> list[str]:
    return [w.strip().lower() for w in re.split(r"[,;\n]", text or "") if w.strip()]


def _score(text: str, recent: list[str]) -> float:
    n = len(text)
    s = 2.0 if 2 <= n <= 40 else 1.0 if n <= 80 else 0.0 if n <= 140 else -3.0
    if any(lexicon.classify(text)):
        s += 1.0
    if text.startswith("@"):
        s -= 1.0
    if lexicon.normalize(text) in recent:
        s -= 2.0
    return s


def select(chatlog: list, start: float, dur: float, hide: list[str] | None = None) -> list[dict]:
    """Какие сообщения и когда показать в клипе. Время в ответе считается от начала клипа."""
    hide = hide or []
    lo, hi = start - PRE, start + dur
    msgs = []
    for row in chatlog or []:
        try:
            t, name, color, text = float(row[0]), str(row[1]), str(row[2] or ""), str(row[3])
            emotes = row[4] if len(row) > 4 else []
        except (TypeError, ValueError, IndexError):
            continue
        text = text.strip()
        low = text.lower()
        if not (lo <= t <= hi) or not text or text[0] in "!/":
            continue
        if name.lower() in BOTS or name.lower().endswith("bot") or LINK.search(text):
            continue
        if any(w in low for w in hide):
            continue
        msgs.append({"t": t - start, "name": name, "color": color, "text": text, "emotes": emotes})
    msgs.sort(key=lambda m: m["t"])
    if not msgs:
        return []
    per_sec: dict[int, int] = {}
    for m in msgs:
        per_sec[math.floor(m["t"])] = per_sec.get(math.floor(m["t"]), 0) + 1

    def slot_len(t: float) -> float:
        s = math.floor(t)
        rate = sum(per_sec.get(s + d, 0) for d in (-1, 0, 1)) / 3.0
        return 1.0 / max(1.0, min(MAX_RATE, 1.1 * math.sqrt(max(rate, 0.01))))

    out: list[dict] = []
    recent: list[str] = []
    i, n = 0, len(msgs)
    while i < n:
        t0 = msgs[i]["t"]
        end = t0 + slot_len(t0)
        j = i
        best, best_s = None, -1e9
        while j < n and msgs[j]["t"] < end:
            s = _score(msgs[j]["text"], recent)
            if s > best_s:
                best, best_s = msgs[j], s
            j += 1
        if best is not None:
            if out and best["t"] < out[-1]["t"] + 0.12:
                best = dict(best, t=out[-1]["t"] + 0.12)
            out.append(best)
            recent = (recent + [lexicon.normalize(best["text"])])[-4:]
        i = j
    return out


def tokenize(text: str, emotes: list, third: dict[str, str], twitch_url) -> list[tuple]:
    """Режет сообщение на слова и смайлы. Смайл: ("emote", адрес картинки, слово)."""
    marks = {}
    for e in emotes or []:
        try:
            marks[int(e[0])] = (int(e[1]), str(e[2]))
        except (TypeError, ValueError, IndexError):
            continue
    out: list[tuple] = []
    for m in re.finditer(r"\S+", text):
        word = m.group(0)
        mark = marks.get(m.start())
        if mark and mark[0] == m.end() - 1:
            out.append(("emote", twitch_url(mark[1]), word))
        elif word in third:
            out.append(("emote", third[word], word))
        else:
            out.append(("text", word))
    return out


def nick_color(name: str, color: str) -> tuple[int, int, int]:
    """Цвет ника, как в Twitch, но подсветлённый, чтобы читался на тёмной плашке."""
    import colorsys
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color or ""):
        color = DEFAULT_COLORS[sum(ord(c) for c in name.lower()) % len(DEFAULT_COLORS)]
    r, g, b = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    if l < 0.62:
        l = 0.62 + (l * 0.1)
    r, g, b = colorsys.hls_to_rgb(h, min(l, 0.86), s)
    return round(r * 255), round(g * 255), round(b * 255)


def chat_text(chatlog: list, start: float, dur: float, limit: int = 600) -> str:
    """Чат за время клипа простым текстом, для файла с описанием."""
    lines = []
    for row in chatlog or []:
        try:
            t = float(row[0]) - start
            if 0 <= t <= dur:
                lines.append(f"[{int(t) // 60}:{int(t) % 60:02d}] {row[1]}: {row[3]}")
        except (TypeError, ValueError, IndexError):
            continue
    return "\n".join(lines[:limit])


# --- рисование (нужен PySide6; вызывается только в отдельном процессе рендера) ---------------------

class ChatPainter:
    """Готовит плашки сообщений один раз, а потом быстро собирает из них кадры."""
    FPS = 30
    APPEAR = 0.22        # секунд на появление сообщения

    # насколько плотная плашка под сообщением (0 = её нет, только текст с тенью)
    STYLES = {"text": 0, "soft": 96, "solid": 205}

    def __init__(self, width: int, height: int, size: str = "m", style: str = "soft"):
        from PySide6.QtGui import QFont, QFontDatabase, QFontMetricsF
        self.w, self.h = width, height
        self.px = SIZES.get(size, SIZES["m"])
        self.alpha = self.STYLES.get(style, self.STYLES["soft"])
        fonts = Path(__file__).resolve().parent / "assets" / "fonts"
        for f in sorted(fonts.glob("Inter-*.ttf")):
            QFontDatabase.addApplicationFont(str(f))
        family = "Inter" if "Inter" in QFontDatabase.families() else QFont().defaultFamily()
        self.font = QFont(family)
        self.font.setPixelSize(self.px)
        self.font.setWeight(QFont.Weight.Medium)
        self.bold = QFont(family)
        self.bold.setPixelSize(self.px)
        self.bold.setWeight(QFont.Weight.Bold)
        self.fm, self.fmb = QFontMetricsF(self.font), QFontMetricsF(self.bold)
        self.pad_x, self.pad_y = round(self.px * 0.5), round(self.px * 0.3)
        self.gap = round(self.px * 0.28)
        self.line_h = round(self.px * 1.34)
        self.emote_h = round(self.px * 1.22)
        self.space = self.fm.horizontalAdvance(" ")
        self.bubbles: list[tuple[float, object, int]] = []     # (время, картинка, высота)
        self._emotes: dict[str, object] = {}

    def _emote(self, path: str | None):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImageReader
        if not path:
            return None
        if path not in self._emotes:
            reader = QImageReader(path)
            reader.setDecideFormatFromContent(True)
            img = reader.read()
            if img.isNull() or img.height() < 4:
                self._emotes[path] = None
            else:
                img = img.scaledToHeight(self.emote_h, Qt.TransformationMode.SmoothTransformation)
                if img.width() > self.emote_h * 3:
                    img = img.scaled(self.emote_h * 3, self.emote_h, Qt.AspectRatioMode.IgnoreAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
                self._emotes[path] = img
        return self._emotes[path]

    def add(self, t: float, name: str, color: str, tokens: list[tuple], images: dict[str, str]) -> None:
        from PySide6.QtCore import QPointF, QRectF, Qt
        from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath
        max_w = self.w - 2 * self.pad_x
        name = name[:25]
        # раскладка в строки: ник, потом слова и смайлы
        items = [("nick", name + ":", self.fmb.horizontalAdvance(name + ":"))]
        for tok in tokens:
            img = self._emote(images.get(tok[1])) if tok[0] == "emote" else None
            if img is not None:
                items.append(("emote", img, float(img.width())))
            else:
                word = tok[2] if tok[0] == "emote" else tok[1]
                wd = self.fm.horizontalAdvance(word)
                if wd > max_w * 0.9:                                   # одно «слово» длиннее строки: сокращаем
                    word = self.fm.elidedText(word, Qt.TextElideMode.ElideRight, max_w * 0.9)
                    wd = self.fm.horizontalAdvance(word)
                items.append(("text", word, wd))
        lines: list[list[tuple]] = [[]]
        x = 0.0
        cut = False
        for it in items:
            need = it[2] + (self.space if lines[-1] else 0)
            if lines[-1] and x + need > max_w:
                if len(lines) == 3:
                    cut = True
                    break
                lines.append([])
                x, need = 0.0, it[2]
            lines[-1].append((x + (need - it[2]), it))
            x += need
        if cut:                                                        # не влезло в три строки: ставим многоточие
            dots = self.fm.horizontalAdvance("…")
            while lines[-1] and lines[-1][-1][0] + lines[-1][-1][1][2] + self.space + dots > max_w:
                lines[-1].pop()
            last_x = lines[-1][-1][0] + lines[-1][-1][1][2] + self.space if lines[-1] else 0.0
            lines[-1].append((last_x, ("text", "…", dots)))
        content_w = max((ln[-1][0] + ln[-1][1][2]) for ln in lines if ln)
        bw = math.ceil(content_w + 2 * self.pad_x)
        bh = self.line_h * len(lines) + 2 * self.pad_y
        img = QImage(bw, bh, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                         | QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        radius = min(bh / 2, self.px * 0.62)
        path.addRoundedRect(QRectF(0, 0, bw, bh), radius, radius)
        if self.alpha:
            p.fillPath(path, QColor(12, 10, 22, self.alpha))
        nick_rgb = QColor(*nick_color(name, color))
        # мягкая тень под буквами: текст читается на любом фоне, даже без плашки
        shadow = QColor(0, 0, 0, 150 if self.alpha < 150 else 0)
        sh = max(1.5, self.px * 0.055)
        for li, ln in enumerate(lines):
            top = self.pad_y + li * self.line_h
            base = top + (self.line_h + self.fm.ascent() - self.fm.descent()) / 2
            for x0, (kind, val, wd) in ln:
                px = self.pad_x + x0
                if kind == "emote":
                    p.drawImage(QPointF(px, top + (self.line_h - val.height()) / 2), val)
                else:
                    p.setFont(self.bold if kind == "nick" else self.font)
                    if shadow.alpha():
                        p.setPen(shadow)
                        p.drawText(QPointF(px, base + sh), val)
                        p.drawText(QPointF(px + sh * 0.5, base + sh * 0.5), val)
                    p.setPen(nick_rgb if kind == "nick" else QColor(255, 255, 255))
                    p.drawText(QPointF(px, base), val)
        p.end()
        self.bubbles.append((t, img, bh))

    def frames(self, dur: float):
        """Кадры чата подряд (BGRA с прозрачностью), по FPS штук в секунду."""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage, QPainter
        canvas = QImage(self.w, self.h, QImage.Format.Format_ARGB32_Premultiplied)
        bubbles = sorted(self.bubbles, key=lambda b: b[0])
        times = [b[0] for b in bubbles]
        # Лента сообщений едет вверх. rest[k]: где низ сообщения k, когда оно и все до него уже на месте
        rest, acc = [], 0.0
        for _t, _img, bh in bubbles:
            acc += bh + self.gap
            rest.append(acc)
        total = int(math.ceil(dur * self.FPS)) + 2
        last_bytes = None
        last_key = None
        hi = 0                                           # сколько сообщений уже появилось к текущему кадру
        for n in range(total):
            t = n / self.FPS
            while hi < len(times) and times[hi] <= t:
                hi += 1
            moving = hi > 0 and t - times[hi - 1] < self.APPEAR + 1.0 / self.FPS
            key = (hi, None if not moving else n)
            if key == last_key and last_bytes is not None:
                yield last_bytes                         # ничего не двигалось: тот же кадр
                continue
            canvas.fill(Qt.GlobalColor.transparent)
            if hi:
                ease = {}
                scroll = rest[hi - 1]                    # на сколько лента уже уехала вверх
                for k in range(hi - 1, -1, -1):
                    e = min(1.0, max(0.0, (t - times[k]) / self.APPEAR))
                    if e >= 1.0:
                        break
                    ease[k] = 1 - (1 - e) ** 3
                    scroll -= (bubbles[k][2] + self.gap) * (1 - ease[k])
                p = QPainter(canvas)
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                for k in range(hi - 1, -1, -1):
                    _bt, img, bh = bubbles[k]
                    bottom = self.h - 4 - scroll + rest[k] - self.gap
                    top = bottom - bh
                    if bottom < 0:
                        break
                    fade = min(1.0, max(0.0, (top + self.px) / (self.px * 2)))    # у верхнего края плавно гаснет
                    alpha = ease.get(k, 1.0) * fade
                    if alpha > 0.01:
                        p.setOpacity(alpha)
                        p.drawImage(0, round(top), img)
                p.end()
            last_bytes = bytes(canvas.constBits())[: self.w * self.h * 4]
            last_key = key
            yield last_bytes
