"""Внешний вид окна.

Направление (по скиллу unslop, github.com/heydamir/unslop, автор Damir Sokolovsky, CC BY 4.0):
  Программа: смотрит сразу несколько Twitch-стримов, ловит моменты, когда чат взрывается, и режет их в клипы.
  Характер: внимательная, быстрая, с огоньком.
  Мир: аппаратная прямого эфира ночью: стена мониторов, лампы «в эфире», индикаторы уровня.
  Яркие места: живые кадры стримов в карточках и индикатор движа в чате.
  Фирменная деталь: сегментный индикатор уровня, как у звукового пульта. Им показан движ в чате у стрима
  и сила момента у клипа.

Поверхности графитовые и нейтральные, акцент один: янтарный, цвет «горячего» момента. Красная точка
значит «идёт запись», зелёный и красный в остальном несут только смысл (хорошо/плохо).
Шрифты идут вместе с программой: Inter для интерфейса, JetBrains Mono для цифр и служебных строк.
Иконки: контурные 24x24 в духе Lucide (лицензия ISC), линия 1.5, рисуются кодом.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPalette, QPixmap
from PySide6.QtWidgets import QApplication

# --- цвета ----------------------------------------------------------------------------------------
SIDE = "#0F0F10"          # самая глубокая поверхность: боковое меню
BG = "#141415"            # фон страницы
CARD = "#19191B"          # карточка
RAISED = "#232325"        # поле ввода, выбранный пункт меню, кнопка
HOVER = "#2A2A2D"         # наведение на приподнятое
PRESSED = "#1E1E20"
TEXT = "#EDEDED"          # основной текст
TEXT_2 = "#A1A1A6"        # второстепенный
MUTED = "#85858B"         # подписи, служебные строки, иконки
DIM = "#5E5E64"           # выключенное


def _rgba(hex_color: str, alpha: float) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {round(alpha * 255)})"


LINE = _rgba(TEXT, 0.08)        # тонкая линия-разделитель и рамка карточки
LINE_2 = _rgba(TEXT, 0.12)      # рамка полей ввода
LINE_3 = _rgba(TEXT, 0.24)      # рамка галочки

ACCENT = "#F2A541"        # янтарный: горячий момент, выбор, фокус
ACCENT_HI = "#F6B963"
ON_ACCENT = "#141415"

LIVE = "#E5484D"          # точка «идёт запись»
GOOD = "#4CC38A"
BAD = "#E5484D"

# старые имена: на случай, если где-то ещё остались
INK = TEXT
DANGER = BAD

FONT = "Inter"
MONO = "JetBrains Mono"


def assets() -> Path:
    return Path(__file__).resolve().parent.parent / "assets"


def load_fonts() -> None:
    """Подключает шрифты из папки программы. Если их нет, остаются системные."""
    global FONT, MONO
    families: set[str] = set()
    try:
        for f in sorted((assets() / "fonts").glob("*.ttf")):
            families.update(QFontDatabase.applicationFontFamilies(QFontDatabase.addApplicationFont(str(f))))
    except OSError:
        pass
    families.update(QFontDatabase.families())
    FONT = next((n for n in ("Inter", "Segoe UI Variable Text", "Segoe UI", "DejaVu Sans") if n in families), "Segoe UI")
    MONO = next((n for n in ("JetBrains Mono", "Cascadia Mono", "Consolas", "DejaVu Sans Mono") if n in families),
                "Consolas")


def app_font() -> QFont:
    f = QFont(FONT)
    f.setPixelSize(14)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return f


def mono_font(px: int = 12, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont(MONO)
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


def apply_palette(app: QApplication) -> None:
    """Цвета для того, что рисует сама система: подсказка в пустом поле, выделение, окна-вопросы."""
    pal = app.palette()
    for role, color in ((QPalette.ColorRole.Window, BG), (QPalette.ColorRole.WindowText, TEXT),
                        (QPalette.ColorRole.Base, RAISED), (QPalette.ColorRole.AlternateBase, CARD),
                        (QPalette.ColorRole.Text, TEXT), (QPalette.ColorRole.Button, RAISED),
                        (QPalette.ColorRole.ButtonText, TEXT), (QPalette.ColorRole.ToolTipBase, RAISED),
                        (QPalette.ColorRole.ToolTipText, TEXT), (QPalette.ColorRole.Highlight, HOVER),
                        (QPalette.ColorRole.HighlightedText, TEXT), (QPalette.ColorRole.Link, ACCENT),
                        (QPalette.ColorRole.PlaceholderText, MUTED)):
        pal.setColor(role, QColor(color))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(DIM))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(DIM))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(DIM))
    app.setPalette(pal)


def _svg_file(asset_dir: str | None, name: str, body: str, stroke: str, width: float = 2.0) -> str:
    """Картинка для стилей (галочка, стрелка списка). Пишется в папку данных один раз при запуске."""
    if not asset_dir:
        return "image: none;"
    try:
        path = os.path.join(asset_dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" '
                     f'stroke="{stroke}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
        return f'image: url("{path.replace(os.sep, "/")}");'
    except OSError:
        return "image: none;"


def stylesheet(asset_dir: str | None = None) -> str:
    f, m = FONT, MONO
    check = _svg_file(asset_dir, "check.svg", '<path d="M19 7 10 16l-4.5-4.5"/>', ON_ACCENT, 3)
    chevron = _svg_file(asset_dir, "chevron.svg", '<path d="m6 9 6 6 6-6"/>', MUTED, 2)
    return f"""
* {{ font-family: "{f}", "Segoe UI", sans-serif; color: {TEXT}; }}
QWidget#root, QWidget#page, QScrollArea, QScrollArea > QWidget > QWidget, QStackedWidget {{ background: {BG}; }}
QScrollArea {{ border: none; }}
QWidget#sidebar {{ background: {SIDE}; border-right: 1px solid {LINE}; }}

QLabel {{ background: transparent; font-size: 14px; }}
QLabel#h1 {{ font-size: 28px; font-weight: 500; }}
QLabel#h2 {{ font-size: 16px; font-weight: 500; }}
QLabel#title {{ font-size: 16px; font-weight: 500; }}
QLabel#clipTitle {{ font-size: 14px; font-weight: 500; }}
QLabel#muted {{ color: {TEXT_2}; font-size: 14px; }}
QLabel#small {{ color: {MUTED}; font-size: 13px; }}
QLabel#status {{ color: {TEXT_2}; font-size: 13px; }}
QLabel#statusHot {{ color: {ACCENT}; font-size: 13px; font-weight: 500; }}
QLabel#statusBad {{ color: {BAD}; font-size: 13px; }}
QLabel#statusGood {{ color: {GOOD}; font-size: 13px; }}
QLabel#meta {{ font-family: "{m}"; color: {MUTED}; font-size: 12px; }}
QLabel#figure {{ font-family: "{m}"; font-size: 24px; font-weight: 500; }}
QLabel#score {{ font-family: "{m}"; color: {TEXT_2}; font-size: 12px; }}
QLabel#value {{ font-family: "{m}"; color: {TEXT}; font-size: 14px; }}
QLabel#brand {{ font-size: 16px; font-weight: 600; }}
QLabel#sideMeta {{ font-family: "{m}"; color: {MUTED}; font-size: 12px; }}
QLabel#sideKey {{ color: {MUTED}; font-size: 12px; }}
QLabel#error {{ color: {BAD}; font-size: 13px; }}

QFrame#card {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 12px; }}
QFrame#cardHot {{ background: {CARD}; border: 1px solid {_rgba(ACCENT, 0.45)}; border-radius: 12px; }}
QFrame#strip {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 12px; }}
QFrame#vline {{ background: {LINE}; border: none; }}
QFrame#updateBar {{ background: {CARD}; border: none; border-bottom: 1px solid {LINE}; }}
QFrame#row {{ background: transparent; border: none; border-bottom: 1px solid {LINE}; }}
QFrame#rowLast {{ background: transparent; border: none; }}

QPushButton {{ font-size: 14px; font-weight: 500; border-radius: 8px; padding: 8px 16px; min-height: 20px;
              background: {RAISED}; border: 1px solid {LINE}; color: {TEXT}; }}
QPushButton:hover {{ background: {HOVER}; }}
QPushButton:pressed {{ background: {PRESSED}; }}
QPushButton:focus {{ border-color: {_rgba(ACCENT, 0.7)}; }}
QPushButton:disabled {{ color: {DIM}; background: {CARD}; border-color: {LINE}; }}
QPushButton#primary {{ background: {TEXT}; color: {ON_ACCENT}; border: 1px solid {TEXT}; }}
QPushButton#primary:hover {{ background: #FFFFFF; border-color: #FFFFFF; }}
QPushButton#primary:pressed {{ background: #D4D4D8; border-color: #D4D4D8; }}
QPushButton#primary:focus {{ border-color: {ACCENT}; }}
QPushButton#primary:disabled {{ background: {RAISED}; color: {DIM}; border-color: {RAISED}; }}
QPushButton#link {{ border: none; background: transparent; color: {ACCENT}; padding: 0; min-height: 20px;
                   font-weight: 500; text-align: left; }}
QPushButton#link:hover {{ background: transparent; color: {ACCENT_HI}; text-decoration: underline; }}
QPushButton#nav {{ text-align: left; border: 1px solid transparent; border-radius: 8px; padding: 0 12px;
                  min-height: 34px; max-height: 34px; font-size: 14px; font-weight: 400; color: {TEXT_2};
                  background: transparent; }}
QPushButton#nav:hover {{ background: {CARD}; color: {TEXT}; }}
QPushButton#nav:checked {{ background: {RAISED}; color: {TEXT}; font-weight: 500; border: 1px solid {LINE}; }}
QPushButton#nav:focus {{ border-color: {_rgba(ACCENT, 0.7)}; }}

QToolButton {{ border: 1px solid transparent; padding: 8px; border-radius: 8px; background: transparent; }}
QToolButton:hover {{ background: {RAISED}; }}
QToolButton:pressed {{ background: {PRESSED}; }}
QToolButton:focus {{ border-color: {_rgba(ACCENT, 0.7)}; }}
QToolButton#vote:checked {{ background: {_rgba(GOOD, 0.14)}; }}
QToolButton#voteBad:checked {{ background: {_rgba(BAD, 0.14)}; }}

QLineEdit, QSpinBox {{ background: {RAISED}; border: 1px solid {LINE_2}; border-radius: 8px; padding: 8px 12px;
                      font-size: 14px; color: {TEXT}; selection-background-color: {_rgba(ACCENT, 0.35)};
                      selection-color: {TEXT}; }}
QLineEdit:hover, QSpinBox:hover {{ border-color: {LINE_3}; }}
QLineEdit:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled {{ color: {DIM}; }}
QSpinBox {{ font-family: "{m}"; min-width: 40px; padding: 8px 4px; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; height: 0; border: none; }}

QPlainTextEdit {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 12px; padding: 12px;
                 font-family: "{m}", "Consolas", monospace; font-size: 12px; color: {TEXT_2};
                 selection-background-color: {_rgba(ACCENT, 0.35)}; selection-color: {TEXT}; }}

QComboBox {{ background: {RAISED}; border: 1px solid {LINE_2}; border-radius: 8px; padding: 8px 12px;
            font-size: 14px; min-height: 20px; min-width: 160px; color: {TEXT}; }}
QComboBox:hover {{ border-color: {LINE_3}; }}
QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 28px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ {chevron} width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{ background: {RAISED}; border: 1px solid {LINE_2}; outline: 0; padding: 4px;
                              selection-background-color: {HOVER}; selection-color: {TEXT}; color: {TEXT}; }}

QCheckBox {{ font-size: 14px; spacing: 8px; color: {TEXT}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {LINE_3}; background: {RAISED}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:focus {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; {check} }}

QSlider {{ min-height: 24px; background: transparent; }}
QSlider::groove:horizontal {{ height: 4px; background: {LINE_2}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; border: none; width: 14px; height: 14px; margin: -5px 0;
                             border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: #FFFFFF; }}

QProgressBar {{ background: {LINE_2}; border: none; border-radius: 2px; max-height: 4px; min-height: 4px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}

QMenu {{ background: {RAISED}; border: 1px solid {LINE_2}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 8px 16px; border-radius: 4px; font-size: 14px; color: {TEXT}; }}
QMenu::item:selected {{ background: {HOVER}; }}

QScrollBar:vertical {{ background: transparent; width: 12px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: {_rgba(TEXT, 0.14)}; border-radius: 4px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: {_rgba(TEXT, 0.24)}; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ height: 0; }}

QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {LINE_2}; padding: 6px 8px; border-radius: 6px;
           font-size: 13px; }}
QDialog, QMessageBox {{ background: {BG}; }}
QMessageBox QLabel {{ color: {TEXT}; }}
"""


# --- иконки ---------------------------------------------------------------------------------------
_ICONS = {
    "streams": '<path d="M4.9 19.1C1 15.2 1 8.8 4.9 4.9"/><path d="M7.8 16.2c-2.3-2.3-2.3-6.1 0-8.5"/>'
               '<circle cx="12" cy="12" r="2"/><path d="M16.2 7.8c2.3 2.3 2.3 6.1 0 8.5"/>'
               '<path d="M19.1 4.9C23 8.8 23 15.1 19.1 19"/>',
    "clips": '<circle cx="6" cy="6" r="3"/><path d="M8.12 8.12 12 12"/><path d="M20 4 8.12 15.88"/>'
             '<circle cx="6" cy="18" r="3"/><path d="M14.8 14.8 20 20"/>',
    "log": '<path d="M3 12a9 9 0 1 0 9-9 9.8 9.8 0 0 0-6.7 2.7L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
    "send": '<path d="M14.5 21.7a.5.5 0 0 0 .9 0L22 2.6a.5.5 0 0 0-.6-.6L2.3 8.5a.5.5 0 0 0 0 .9l8 3.2a2 2 0 0 1 1.1 1.1z"/>'
            '<path d="m21.9 2.1-10.9 10.9"/>',
    "learn": '<path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/><path d="M9 7h6M9 11h4"/>',
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
    "download": '<path d="M12 15V3"/><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/>',
    "tv": '<rect x="2" y="7" width="20" height="15" rx="2"/><path d="m17 2-5 5-5-5"/>',
}
_CACHE: dict = {}


def _pixmap(name: str, color: str, px: int) -> QPixmap:
    from PySide6.QtSvg import QSvgRenderer

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" '
           f'stroke="{color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">{_ICONS[name]}</svg>')
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode())).render(p, QRectF(0, 0, px, px))
    p.end()
    return pm


def icon(name: str, color: str = TEXT_2, size: int = 16, on_color: str | None = None,
         off_color: str | None = None) -> QIcon:
    """Иконка. on_color: цвет для нажатого (выбранного) состояния, off_color: для выключенной кнопки."""
    key = (name, color, size, on_color, off_color)
    if key in _CACHE:
        return _CACHE[key]
    out = QIcon()
    for scale in (1, 2, 3):
        for col, mode, state in ((color, QIcon.Mode.Normal, QIcon.State.Off),
                                 (on_color, QIcon.Mode.Normal, QIcon.State.On),
                                 (off_color or DIM, QIcon.Mode.Disabled, QIcon.State.Off)):
            if col:
                pm = _pixmap(name, col, size * scale)
                pm.setDevicePixelRatio(scale)
                out.addPixmap(pm, mode, state)
    _CACHE[key] = out
    return out


# --- рисунки --------------------------------------------------------------------------------------
def meter(p: QPainter, rect: QRectF, level: float, segments: int, lit: str, gap: float = 2.0) -> None:
    """Сегментный индикатор уровня, как на звуковом пульте. Горят сегменты слева, пустые едва видны."""
    level = max(0.0, min(1.0, level))
    w = (rect.width() - gap * (segments - 1)) / segments
    on = round(level * segments)
    if level > 0.02 and on == 0:
        on = 1                                         # хоть что-то в чате есть: один сегмент горит
    p.setPen(Qt.PenStyle.NoPen)
    off = QColor(TEXT)
    off.setAlphaF(0.08)
    color = QColor(lit)
    for i in range(segments):
        x = rect.left() + i * (w + gap)
        p.setBrush(color if i < on else off)
        p.drawRoundedRect(QRectF(x, rect.top(), w, rect.height()), 1, 1)


def monogram(p: QPainter, rect: QRectF, text: str, radius: float) -> None:
    """Плитка с первой буквой канала: когда кадра со стрима ещё нет."""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(RAISED))
    p.drawRoundedRect(rect, radius, radius)
    f = QFont(FONT)
    f.setPixelSize(max(10, int(rect.height() * 0.36)))
    f.setWeight(QFont.Weight.Medium)
    p.setFont(f)
    p.setPen(QColor(MUTED))
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


def _cut_play(s: float, color: QColor, inset: float) -> QPainterPath:
    """Знак программы: треугольник «плей», разрезанный вертикальной щелью, как клип, вырезанный из эфира."""
    left, right, top, bottom = inset + s * 0.06, s - inset, inset, s - inset
    mid_y = (top + bottom) / 2
    cut, gap = left + (right - left) * 0.46, s * 0.08
    tri = QPainterPath()
    tri.moveTo(left, top)
    tri.lineTo(right, mid_y)
    tri.lineTo(left, bottom)
    tri.closeSubpath()
    slit = QPainterPath()
    slit.addRect(QRectF(cut - gap / 2, 0, gap, s))
    return tri.subtracted(slit)


def draw_logo(size: int, tile: bool = True) -> QPixmap:
    """Значок программы: янтарный разрезанный «плей». tile: на графитовой плитке (значок окна и ярлыка)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = float(size)
    if tile:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(CARD))
        p.drawRoundedRect(QRectF(0, 0, s, s), s * 0.22, s * 0.22)
        inset = s * 0.25
    else:
        inset = s * 0.08
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(ACCENT))
    p.drawPath(_cut_play(s, QColor(ACCENT), inset))
    p.end()
    return pm


def app_icon() -> QIcon:
    ic = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(draw_logo(s))
    return ic
