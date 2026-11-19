"""Заголовок над видео в вертикальном клипе: крупный белый текст на тёмной плашке, как в TikTok-нарезках.

Рисуется один раз в картинку с прозрачным фоном, ffmpeg кладёт её поверх кадра на всю длину клипа.
Нужен PySide6, поэтому рисование идёт в отдельном процессе рендера (как и чат).
"""
from __future__ import annotations

import re
from pathlib import Path

MAX_W = 980               # шире плашка не бывает: кадр 1080, по бокам остаются поля
SIZES = (70, 62, 54, 48)  # размер букв: сначала крупно; если текст не влезает в три строки, мельче
MAX_LINES = 3
MAX_CHARS = 90


def clean(text: str) -> str:
    """Приводит заголовок к виду, который можно рисовать: одна строка, без точки в конце, без лишних пробелов
    и без кавычек вокруг всего заголовка (кавычки вокруг отдельного слова остаются)."""
    text = re.sub(r"\s+", " ", str(text or "")).strip().rstrip(". ")
    while (len(text) >= 2 and text[0] in "«\"'“„" and text[-1] in "»\"'”“"
           and not any(q in text[1:-1] for q in "«»\"“”„")):
        text = text[1:-1].strip().rstrip(". ")
    if not re.search(r"\w", text):
        return ""
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS].rsplit(" ", 1)[0].rstrip(",:;-– ") + "…"
    return text


def wrap(words: list[str], width, max_w: float) -> list[str]:
    """Раскладывает слова по строкам не шире max_w. width(строка) меряет ширину в точках."""
    lines: list[str] = []
    for w in words:
        if lines and width(lines[-1] + " " + w) <= max_w:
            lines[-1] += " " + w
        else:
            lines.append(w)
    return lines


def balance(words: list[str], width, max_w: float) -> list[str]:
    """Строки примерно одной длины: «угон инвалидной / коляски в гта 6», а не длинная строка и одно слово под ней."""
    lines = wrap(words, width, max_w)
    if len(lines) < 2:
        return lines
    lo, hi = min(max_w, max(width(w) for w in words)), max_w      # слово шире строки: шире строки не станут
    for _ in range(12):                                  # самая узкая ширина, при которой строк столько же
        mid = (lo + hi) / 2
        if len(wrap(words, width, mid)) <= len(lines):
            hi = mid
        else:
            lo = mid
    return wrap(words, width, hi)


def layout(text: str, width_for) -> tuple[int, list[str]]:
    """Подбирает размер букв и строки. width_for(размер) возвращает функцию ширины строки для этого размера."""
    words = clean(text).split(" ")
    best: tuple[int, list[str]] | None = None
    for px in SIZES:
        width = width_for(px)
        max_w = MAX_W - 2 * round(px * 0.5)
        lines = balance(words, width, max_w)
        best = (px, lines)
        if len(lines) <= MAX_LINES and all(width(ln) <= max_w for ln in lines):
            return best
    px, lines = best                                     # всё равно не влезло: три строки и многоточие
    width = width_for(px)
    max_w = MAX_W - 2 * round(px * 0.5)
    if len(lines) > MAX_LINES:
        lines = lines[:MAX_LINES]
        lines[-1] = _elide(lines[-1].rstrip("…"), width, max_w)
    lines = [ln if width(ln) <= max_w else _elide(ln.rstrip("…"), width, max_w) for ln in lines]
    return px, lines


def _elide(line: str, width, max_w: float) -> str:
    while line and width(line + "…") > max_w:
        line = line[:-1]
    return line.rstrip() + "…"


def render(text: str, out_path: str) -> tuple[int, int] | None:
    """Рисует заголовок в PNG. Возвращает (ширина, высота) картинки или None, если рисовать нечего."""
    if not clean(text):
        return None
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QImage, QPainter, QPainterPath
    fonts = Path(__file__).resolve().parent / "assets" / "fonts"
    for f in sorted(fonts.glob("Inter-*.ttf")):
        QFontDatabase.addApplicationFont(str(f))
    family = "Inter" if "Inter" in QFontDatabase.families() else QFont().defaultFamily()

    def font(px: int) -> QFont:
        f = QFont(family)
        f.setPixelSize(px)
        f.setWeight(QFont.Weight.Bold)
        return f

    px, lines = layout(text, lambda size: QFontMetricsF(font(size)).horizontalAdvance)
    fnt = font(px)
    fm = QFontMetricsF(fnt)
    pad_x, pad_y = round(px * 0.5), round(px * 0.34)
    line_h = round(px * 1.22)
    w = int(min(MAX_W, max(fm.horizontalAdvance(ln) for ln in lines) + 2 * pad_x)) // 2 * 2
    h = (line_h * len(lines) + 2 * pad_y) // 2 * 2
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
    path = QPainterPath()
    radius = px * 0.42
    path.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
    p.fillPath(path, QColor(8, 8, 12, 236))
    p.setFont(fnt)
    p.setPen(QColor(255, 255, 255))
    for i, ln in enumerate(lines):
        base = pad_y + i * line_h + (line_h + fm.ascent() - fm.descent()) / 2
        p.drawText(QPointF((w - fm.horizontalAdvance(ln)) / 2, base), ln)
    p.end()
    if not img.save(out_path, "PNG"):
        return None
    return w, h
