"""Внешний вид окна: тёплый светлый фон, белые карточки, зелёный акцент, боковое меню.

Стиль взят из LexoPlay (прошлая программа автора), чтобы обе выглядели как одна семья.
Иконки: контурные 24x24 в духе Lucide (лицензия ISC), рисуются кодом, файлов не нужно.
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontDatabase, QIcon, QPainter, QPen, QPixmap

BG = "#F6F5F1"
SIDEBAR = "#EFEDE7"
CARD = "#FFFFFF"
BORDER = "#E2DFD7"
BORDER_STRONG = "#C9C6BC"
INK = "#1F1F1D"
MUTED = "#5F5D57"
GREEN = "#0F6E56"
GREEN_HOVER = "#0C5A47"
GREEN_SOFT = "#DCEFE8"
GREEN_INK = "#0F3D31"
WARN = "#8A6A1F"
WARN_SOFT = "#FBF1D9"
DANGER = "#B3261E"
DANGER_SOFT = "#F9E1DF"
LIVE = "#D93A3A"

FONT = "Segoe UI"
DISPLAY = "Segoe UI"


def load_fonts() -> None:
    global FONT, DISPLAY
    families = set(QFontDatabase.families())
    for name in ("Segoe UI Variable Text", "Segoe UI", "DejaVu Sans"):
        if name in families:
            FONT = name
            break
    DISPLAY = "Segoe UI Variable Display" if "Segoe UI Variable Display" in families else FONT


def stylesheet(asset_dir: str | None = None) -> str:
    f, d = FONT, DISPLAY
    check = "image: none;"
    if asset_dir:                      # галочка для чекбоксов: рисуем файл один раз
        try:
            import os
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
QWidget#sidebar {{ background: {SIDEBAR}; border-right: 1px solid {BORDER}; }}
QLabel {{ background: transparent; font-size: 10.5pt; }}
QLabel#h1 {{ font-family: "{d}"; font-size: 20pt; font-weight: 700; }}
QLabel#h2 {{ font-family: "{d}"; font-size: 12.5pt; font-weight: 700; }}
QLabel#muted {{ color: {MUTED}; font-size: 10pt; }}
QLabel#small {{ color: {MUTED}; font-size: 9pt; }}
QLabel#title {{ font-size: 12pt; font-weight: 700; }}
QLabel#brand {{ font-family: "{d}"; font-size: 13.5pt; font-weight: 700; }}
QLabel#error {{ color: {DANGER}; font-size: 9.5pt; }}
QLabel#chip {{ background: {GREEN_SOFT}; color: {GREEN_INK}; border-radius: 9px; padding: 2px 9px;
              font-size: 8.5pt; font-weight: 700; }}
QLabel#chipGray {{ background: #ECEAE4; color: {MUTED}; border-radius: 9px; padding: 2px 9px;
                  font-size: 8.5pt; font-weight: 700; }}
QLabel#chipLive {{ background: {DANGER_SOFT}; color: {DANGER}; border-radius: 9px; padding: 2px 9px;
                  font-size: 8.5pt; font-weight: 700; }}
QLabel#chipWarn {{ background: {WARN_SOFT}; color: {WARN}; border-radius: 9px; padding: 2px 9px;
                  font-size: 8.5pt; font-weight: 700; }}
QLabel#score {{ background: {GREEN}; color: white; border-radius: 9px; padding: 2px 9px; font-size: 9pt; font-weight: 700; }}
QFrame#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#cardHot {{ background: {CARD}; border: 2px solid {GREEN}; border-radius: 12px; }}
QFrame#banner {{ background: {WARN_SOFT}; border: 1px solid #EBD9A8; border-radius: 12px; }}
QFrame#updateBar {{ background: {GREEN_SOFT}; border: none; border-bottom: 1px solid #BFE0D4; }}
QFrame#row {{ background: transparent; border: none; border-bottom: 1px solid #EEECE6; }}
QFrame#rowLast {{ background: transparent; border: none; }}
QPushButton {{ font-size: 10pt; font-weight: 600; border-radius: 8px; padding: 8px 14px; min-height: 22px;
              background: transparent; border: 1px solid {BORDER_STRONG}; }}
QPushButton:hover {{ background: #EFEDE6; }}
QPushButton:pressed {{ background: #E6E3DA; }}
QPushButton:disabled {{ color: #A9A69D; border-color: {BORDER}; }}
QPushButton#primary {{ background: {GREEN}; color: white; border: 1px solid {GREEN}; }}
QPushButton#primary:hover {{ background: {GREEN_HOVER}; }}
QPushButton#primary:disabled {{ background: #9CC7B9; border-color: #9CC7B9; color: white; }}
QPushButton#link {{ border: none; color: {GREEN}; padding: 2px 0; font-weight: 700; text-align: left; }}
QPushButton#link:hover {{ background: transparent; text-decoration: underline; }}
QPushButton#nav {{ text-align: left; border: none; border-radius: 8px; padding: 10px 12px; font-size: 10.5pt;
                  font-weight: 600; color: #4A4945; }}
QPushButton#nav:hover {{ background: #E6E3DB; }}
QPushButton#nav:checked {{ background: {GREEN_SOFT}; color: {GREEN_INK}; }}
QToolButton {{ border: none; padding: 6px; border-radius: 8px; background: transparent; }}
QToolButton:hover {{ background: #EFEDE6; }}
QToolButton#vote:checked {{ background: {GREEN_SOFT}; }}
QToolButton#voteBad:checked {{ background: {DANGER_SOFT}; }}
QLineEdit, QSpinBox {{ background: {CARD}; border: 1px solid {BORDER_STRONG}; border-radius: 8px; padding: 8px 10px;
            font-size: 10.5pt; selection-background-color: {GREEN_SOFT}; selection-color: {INK}; }}
QLineEdit:focus, QSpinBox:focus {{ border: 2px solid {GREEN}; padding: 7px 9px; }}
QSpinBox {{ min-width: 40px; padding: 8px 4px; }}
QSpinBox:focus {{ padding: 7px 3px; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; height: 0; border: none; }}
QPlainTextEdit {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px; padding: 10px;
                 font-family: "Consolas", "DejaVu Sans Mono", monospace; font-size: 9.5pt; }}
QComboBox {{ background: {CARD}; border: 1px solid {BORDER_STRONG}; border-radius: 8px; padding: 6px 10px;
            font-size: 10pt; min-height: 22px; min-width: 150px; }}
QComboBox:hover {{ border-color: {GREEN}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {CARD}; border: 1px solid {BORDER}; selection-background-color: {GREEN_SOFT};
                              selection-color: {INK}; outline: 0; padding: 4px; }}
QCheckBox {{ font-size: 10.5pt; spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {BORDER_STRONG}; background: {CARD}; }}
QCheckBox::indicator:checked {{ background: {GREEN}; border-color: {GREEN}; {check} }}
QSlider {{ min-height: 22px; background: transparent; }}
QSlider::groove:horizontal {{ height: 4px; background: #E4E1D8; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {GREEN}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {CARD}; border: 1px solid #C9C5BA; width: 16px; height: 16px;
                             margin: -7px 0; border-radius: 9px; }}
QProgressBar {{ background: #ECEAE4; border: none; border-radius: 3px; max-height: 6px; min-height: 6px; }}
QProgressBar::chunk {{ background: {GREEN}; border-radius: 3px; }}
QMenu {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 7px 18px; border-radius: 6px; font-size: 10pt; }}
QMenu::item:selected {{ background: {GREEN_SOFT}; color: {INK}; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px 3px; }}
QScrollBar::handle:vertical {{ background: #D6D3CA; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #BDB9AE; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QToolTip {{ background: {INK}; color: white; border: none; padding: 6px 8px; border-radius: 6px; }}
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
    "external": '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
}
_CACHE: dict = {}


def icon(name: str, color: str = INK, size: int = 18) -> QIcon:
    key = (name, color, size)
    if key in _CACHE:
        return _CACHE[key]
    from PySide6.QtSvg import QSvgRenderer

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" '
           f'stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{_ICONS[name]}</svg>')
    out = QIcon()
    for scale in (1, 2):
        px = size * scale
        pm = QPixmap(px, px)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        QSvgRenderer(QByteArray(svg.encode())).render(p, QRectF(0, 0, px, px))
        p.end()
        pm.setDevicePixelRatio(scale)
        out.addPixmap(pm)
    _CACHE[key] = out
    return out


def draw_logo(size: int) -> QPixmap:
    """Зелёный квадрат, белый треугольник «плей» и линия реза через него."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = float(size)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(GREEN))
    p.drawRoundedRect(QRectF(0, 0, s, s), s * 0.23, s * 0.23)
    p.setBrush(QColor("#FFFFFF"))
    p.drawPolygon([QPointF(s * 0.34, s * 0.26), QPointF(s * 0.76, s * 0.5), QPointF(s * 0.34, s * 0.74)])
    pen = QPen(QColor(GREEN), max(1.5, s * 0.07), Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(s * 0.5, s * 0.2), QPointF(s * 0.5, s * 0.8))
    p.end()
    return pm


def app_icon() -> QIcon:
    ic = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(draw_logo(s))
    return ic
