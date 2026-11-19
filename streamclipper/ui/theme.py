"""Внешний вид окна.

Светлая рабочая область, белые карточки с мягкой тенью, тёмное боковое меню и фиолетовый акцент
(в тон Twitch). Шрифт Inter идёт вместе с программой, поэтому текст выглядит одинаково на любом компьютере.
Иконки: контурные 24x24 в духе Lucide (лицензия ISC), рисуются кодом.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QIcon, QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap)
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget

BG = "#F4F4FA"
CARD = "#FFFFFF"
BORDER = "#E8E7F1"
BORDER_STRONG = "#D5D3E3"
INK = "#17151F"
MUTED = "#6C6A7C"
FAINT = "#9A98AB"

ACCENT = "#7C4DFF"
ACCENT_2 = "#B14DFF"
ACCENT_HOVER = "#6B3BF2"
ACCENT_SOFT = "#F0EAFF"
ACCENT_INK = "#4520B8"

SIDE = "#15131F"
SIDE_2 = "#1E1A30"
SIDE_TEXT = "#A8A4BE"
SIDE_ACTIVE = "#2B2547"

GOOD = "#12A150"
GOOD_SOFT = "#E2F7EA"
LIVE = "#F0384E"
LIVE_SOFT = "#FFE7EA"
WARN = "#A8690C"
WARN_SOFT = "#FFF2D2"
DANGER = "#D1293D"
DANGER_SOFT = "#FFE7EA"

FONT = "Segoe UI"
DISPLAY = "Segoe UI"
GRAD = f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_2})"
GRAD_HOVER = f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT_HOVER}, stop:1 #A03BF2)"
HEAT = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #FFB03A, stop:1 #FF4D7D)"


def assets() -> Path:
    return Path(__file__).resolve().parent.parent / "assets"


def load_fonts() -> None:
    """Подключает Inter из папки программы. Если его нет, остаётся системный шрифт."""
    global FONT, DISPLAY
    try:
        for f in sorted((assets() / "fonts").glob("*.ttf")):
            QFontDatabase.addApplicationFont(str(f))
    except OSError:
        pass
    families = set(QFontDatabase.families())
    FONT = next((n for n in ("Inter", "Segoe UI Variable Text", "Segoe UI", "DejaVu Sans") if n in families), FONT)
    DISPLAY = next((n for n in ("Inter Display", FONT) if n in families), FONT)


def app_font() -> QFont:
    f = QFont(FONT)
    f.setPointSizeF(10.5)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return f


def shadow(w: QWidget, blur: int = 28, dy: int = 6, alpha: int = 22) -> None:
    """Мягкая тень под карточкой."""
    eff = QGraphicsDropShadowEffect(w)
    eff.setBlurRadius(blur)
    eff.setOffset(0, dy)
    eff.setColor(QColor(40, 28, 100, alpha))
    w.setGraphicsEffect(eff)


def stylesheet(asset_dir: str | None = None) -> str:
    f, d = FONT, DISPLAY
    check = "image: none;"
    if asset_dir:                      # галочка для чекбоксов: рисуем файл один раз
        try:
            path = os.path.join(asset_dir, "check.svg")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" '
                         'stroke="#FFFFFF" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round">'
                         '<path d="M19 7 10 16l-4.5-4.5"/></svg>')
            check = f'image: url("{path.replace(os.sep, "/")}");'
        except OSError:
            pass
    return f"""
* {{ font-family: "{f}", "Segoe UI", sans-serif; color: {INK}; }}
QWidget#root, QWidget#page, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {BG}; }}
QScrollArea {{ border: none; }}
QWidget#sidebar {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {SIDE}, stop:1 {SIDE_2}); }}
QLabel {{ background: transparent; font-size: 10.5pt; }}
QLabel#h1 {{ font-family: "{d}"; font-size: 22pt; font-weight: 700; letter-spacing: -0.4px; }}
QLabel#h2 {{ font-family: "{d}"; font-size: 13pt; font-weight: 700; }}
QLabel#muted {{ color: {MUTED}; font-size: 10.5pt; }}
QLabel#small {{ color: {MUTED}; font-size: 9.5pt; }}
QLabel#title {{ font-size: 12.5pt; font-weight: 700; }}
QLabel#brand {{ font-family: "{d}"; font-size: 14pt; font-weight: 700; color: #FFFFFF; }}
QLabel#sideSmall {{ color: {SIDE_TEXT}; font-size: 9pt; }}
QLabel#error {{ color: {DANGER}; font-size: 10pt; font-weight: 500; }}
QLabel#statValue {{ font-family: "{d}"; font-size: 19pt; font-weight: 700; }}
QLabel#statName {{ color: {MUTED}; font-size: 9.5pt; font-weight: 500; }}
QLabel#chip {{ background: {GOOD_SOFT}; color: {GOOD}; border-radius: 10px; padding: 3px 10px;
              font-size: 9pt; font-weight: 600; }}
QLabel#chipHot {{ background: {HEAT}; color: white; border-radius: 10px; padding: 3px 10px;
                 font-size: 9pt; font-weight: 700; }}
QLabel#chipGray {{ background: #EEEDF5; color: {MUTED}; border-radius: 10px; padding: 3px 10px;
                  font-size: 9pt; font-weight: 600; }}
QLabel#chipLive {{ background: {LIVE_SOFT}; color: {LIVE}; border-radius: 10px; padding: 3px 10px;
                  font-size: 9pt; font-weight: 700; }}
QLabel#chipWarn {{ background: {WARN_SOFT}; color: {WARN}; border-radius: 10px; padding: 3px 10px;
                  font-size: 9pt; font-weight: 600; }}
QLabel#score {{ background: {GRAD}; color: white; border-radius: 10px; padding: 3px 10px; font-size: 9.5pt;
               font-weight: 700; }}
QFrame#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 16px; }}
QFrame#cardHot {{ background: {CARD}; border: 2px solid #FF7A5C; border-radius: 16px; }}
QFrame#stat {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame#updateBar {{ background: {ACCENT_SOFT}; border: none; border-bottom: 1px solid #DDD0FF; }}
QFrame#row {{ background: transparent; border: none; border-bottom: 1px solid #F0EFF6; }}
QFrame#rowLast {{ background: transparent; border: none; }}
QPushButton {{ font-size: 10.5pt; font-weight: 600; border-radius: 10px; padding: 9px 16px; min-height: 22px;
              background: {CARD}; border: 1px solid {BORDER_STRONG}; }}
QPushButton:hover {{ background: #F5F3FC; border-color: #BDB6DE; }}
QPushButton:pressed {{ background: #ECE8F9; }}
QPushButton:disabled {{ color: #B4B2C2; border-color: {BORDER}; background: #FAFAFD; }}
QPushButton#primary {{ background: {GRAD}; color: white; border: none; padding: 10px 18px; }}
QPushButton#primary:hover {{ background: {GRAD_HOVER}; }}
QPushButton#primary:disabled {{ background: #D9D2F3; color: white; }}
QPushButton#link {{ border: none; background: transparent; color: {ACCENT}; padding: 2px 0; font-weight: 600;
                   text-align: left; }}
QPushButton#link:hover {{ background: transparent; color: {ACCENT_HOVER}; text-decoration: underline; }}
QPushButton#nav {{ text-align: left; border: none; border-radius: 10px; padding: 11px 12px; font-size: 10.5pt;
                  font-weight: 500; color: {SIDE_TEXT}; background: transparent; }}
QPushButton#nav:hover {{ background: #211D33; color: #FFFFFF; }}
QPushButton#nav:checked {{ background: {SIDE_ACTIVE}; color: #FFFFFF; font-weight: 600; }}
QToolButton {{ border: none; padding: 7px; border-radius: 9px; background: transparent; }}
QToolButton:hover {{ background: #F1EFF8; }}
QToolButton#vote:checked {{ background: {GOOD_SOFT}; }}
QToolButton#voteBad:checked {{ background: {DANGER_SOFT}; }}
QLineEdit, QSpinBox {{ background: {CARD}; border: 1px solid {BORDER_STRONG}; border-radius: 10px; padding: 9px 12px;
            font-size: 10.5pt; selection-background-color: {ACCENT_SOFT}; selection-color: {INK}; }}
QLineEdit:focus, QSpinBox:focus {{ border: 2px solid {ACCENT}; padding: 8px 11px; }}
QSpinBox {{ min-width: 40px; padding: 9px 4px; font-weight: 600; }}
QSpinBox:focus {{ padding: 8px 3px; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; height: 0; border: none; }}
QPlainTextEdit {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 16px; padding: 12px;
                 font-family: "Cascadia Mono", "Consolas", "DejaVu Sans Mono", monospace; font-size: 9.5pt; }}
QComboBox {{ background: {CARD}; border: 1px solid {BORDER_STRONG}; border-radius: 10px; padding: 7px 12px;
            font-size: 10.5pt; min-height: 22px; min-width: 150px; }}
QComboBox:hover {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ background: {CARD}; border: 1px solid {BORDER}; selection-background-color: {ACCENT_SOFT};
                              selection-color: {INK}; outline: 0; padding: 4px; }}
QCheckBox {{ font-size: 10.5pt; spacing: 9px; }}
QCheckBox::indicator {{ width: 19px; height: 19px; border-radius: 6px; border: 1px solid {BORDER_STRONG}; background: {CARD}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; {check} }}
QSlider {{ min-height: 24px; background: transparent; }}
QSlider::groove:horizontal {{ height: 5px; background: #E7E5F1; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {GRAD}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {CARD}; border: 2px solid {ACCENT}; width: 14px; height: 14px;
                             margin: -7px 0; border-radius: 9px; }}
QProgressBar {{ background: #ECEBF4; border: none; border-radius: 4px; max-height: 8px; min-height: 8px; }}
QProgressBar::chunk {{ background: {HEAT}; border-radius: 4px; }}
QMenu {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px; padding: 5px; }}
QMenu::item {{ padding: 8px 18px; border-radius: 7px; font-size: 10.5pt; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; color: {INK}; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px 3px; }}
QScrollBar::handle:vertical {{ background: #D4D2E2; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #B7B4CC; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QToolTip {{ background: {INK}; color: white; border: none; padding: 7px 9px; border-radius: 7px; font-size: 9.5pt; }}
QDialog, QMessageBox {{ background: {BG}; }}
"""


_ICONS = {
    "streams": '<path d="M4.9 19.1C1 15.2 1 8.8 4.9 4.9"/><path d="M7.8 16.2c-2.3-2.3-2.3-6.1 0-8.5"/>'
               '<circle cx="12" cy="12" r="2"/><path d="M16.2 7.8c2.3 2.3 2.3 6.1 0 8.5"/>'
               '<path d="M19.1 4.9C23 8.8 23 15.1 19.1 19"/>',
    "clips": '<circle cx="6" cy="6" r="3"/><path d="M8.12 8.12 12 12"/><path d="M20 4 8.12 15.88"/>'
             '<circle cx="6" cy="18" r="3"/><path d="M14.8 14.8 20 20"/>',
    "log": '<path d="M3 12a9 9 0 1 0 9-9 9.8 9.8 0 0 0-6.7 2.7L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
    "send": '<path d="M14.5 21.7a.5.5 0 0 0 .9 0L22 2.6a.5.5 0 0 0-.6-.6L2.3 8.5a.5.5 0 0 0 0 .9l8 3.2a2 2 0 0 1 1.1 1.1z"/>'
            '<path d="m21.9 2.1-10.9 10.9"/>',
    "settings": '<path d="M21 4h-7M10 4H3M21 12h-9M8 12H3M21 20h-5M12 20H3M14 2v4M8 10v4M16 18v4"/>',
    "plus": '<path d="M5 12h14M12 5v14"/>',
    "folder": '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9l-.8-1.2A2 2 0 0 0 7.9 3H4a2 2 0 0 0-2 2v13'
              'a2 2 0 0 0 2 2Z"/>',
    "trash": '<path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>',
    "play": '<polygon points="6 3 20 12 6 21 6 3"/>',
    "pause": '<rect x="14" y="4" width="4" height="16" rx="1"/><rect x="6" y="4" width="4" height="16" rx="1"/>',
    "up": '<path d="M7 10v12"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8'
          'a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"/>',
    "down": '<path d="M17 14V2"/><path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8'
            'a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z"/>',
    "refresh": '<path d="M3 12a9 9 0 0 1 9-9 9.8 9.8 0 0 1 6.7 2.7L21 8"/><path d="M21 3v5h-5"/>'
               '<path d="M21 12a9 9 0 0 1-9 9 9.8 9.8 0 0 1-6.7-2.7L3 16"/><path d="M8 16H3v5"/>',
}
_CACHE: dict = {}


def _pixmap(name: str, color: str, px: int) -> QPixmap:
    from PySide6.QtSvg import QSvgRenderer

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" '
           f'stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{_ICONS[name]}</svg>')
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode())).render(p, QRectF(0, 0, px, px))
    p.end()
    return pm


def icon(name: str, color: str = INK, size: int = 18, on_color: str | None = None) -> QIcon:
    """Иконка. on_color: цвет для нажатого (выбранного) состояния кнопки."""
    key = (name, color, size, on_color)
    if key in _CACHE:
        return _CACHE[key]
    out = QIcon()
    for scale in (1, 2):
        pm = _pixmap(name, color, size * scale)
        pm.setDevicePixelRatio(scale)
        out.addPixmap(pm, QIcon.Mode.Normal, QIcon.State.Off)
        if on_color:
            pm2 = _pixmap(name, on_color, size * scale)
            pm2.setDevicePixelRatio(scale)
            out.addPixmap(pm2, QIcon.Mode.Normal, QIcon.State.On)
    _CACHE[key] = out
    return out


def tile_colors(seed: str) -> tuple[QColor, QColor]:
    """Два цвета для плитки канала. У одного и того же канала цвет всегда один."""
    pairs = [("#7C4DFF", "#B14DFF"), ("#FF6A3D", "#FF3D77"), ("#0FA3B1", "#3D7BFF"), ("#12A150", "#7BC61A"),
             ("#F0384E", "#FF7A5C"), ("#3D5AFE", "#7C4DFF"), ("#E0A100", "#FF6A3D"), ("#D63DB5", "#7C4DFF")]
    a, b = pairs[sum(ord(c) for c in seed) % len(pairs)]
    return QColor(a), QColor(b)


def draw_tile(p: QPainter, rect: QRectF, text: str, seed: str, radius: float) -> None:
    a, b = tile_colors(seed)
    g = QLinearGradient(rect.topLeft(), rect.bottomRight())
    g.setColorAt(0, a)
    g.setColorAt(1, b)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawRoundedRect(rect, radius, radius)
    f = QFont(DISPLAY)
    f.setPixelSize(int(rect.height() * 0.46))
    f.setWeight(QFont.Weight.Bold)
    p.setFont(f)
    p.setPen(QColor("#FFFFFF"))
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


def draw_logo(size: int) -> QPixmap:
    """Фиолетовый квадрат с градиентом, белый треугольник «плей» и линия реза через него."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = float(size)
    g = QLinearGradient(QPointF(0, 0), QPointF(s, s))
    g.setColorAt(0, QColor(ACCENT))
    g.setColorAt(1, QColor("#D04DFF"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawRoundedRect(QRectF(0, 0, s, s), s * 0.26, s * 0.26)
    tri = QPainterPath()
    tri.moveTo(s * 0.36, s * 0.27)
    tri.lineTo(s * 0.76, s * 0.5)
    tri.lineTo(s * 0.36, s * 0.73)
    tri.closeSubpath()
    p.setBrush(QColor("#FFFFFF"))
    p.setPen(QPen(QColor("#FFFFFF"), s * 0.06, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(tri)
    if size >= 24:
        pen = QPen(QColor("#9A4DFF"), max(1.5, s * 0.055), Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(s * 0.52, s * 0.16), QPointF(s * 0.52, s * 0.84))
    p.end()
    return pm


def app_icon() -> QIcon:
    ic = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(draw_logo(s))
    return ic
