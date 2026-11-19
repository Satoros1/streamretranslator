"""Окно программы. Слева меню, справа страницы: Стримы, Клипы, Telegram, Журнал, Настройки."""
from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtCore import QRectF
from PySide6.QtGui import QAction, QPainter
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QScrollArea, QSlider, QSpinBox, QStackedWidget, QSystemTrayIcon, QToolButton,
                               QVBoxLayout, QWidget)

from . import APP_NAME, __version__, bible, config, system, updater, util
from .supervisor import Supervisor
from .uploader import TG_KEYS, pick_channel, summarize_test
from .ui import theme as T


# ----------------------------------------------------------------- мелкие помощники
def label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    w.setWordWrap(wrap)
    return w


def button(text: str, kind: str | None = None, icon: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    if icon:
        b.setIcon(T.icon(icon, "#FFFFFF" if kind == "primary" else T.INK))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def tool(icon: str, tip: str, name: str | None = None, checkable: bool = False,
         on_color: str | None = None) -> QToolButton:
    b = QToolButton()
    b.setIcon(T.icon(icon, T.MUTED, on_color=on_color))
    b.setIconSize(QSize(18, 18))
    b.setToolTip(tip)
    b.setCheckable(checkable)
    if name:
        b.setObjectName(name)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def card(name: str = "card") -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName(name)
    lay = QVBoxLayout(f)
    lay.setContentsMargins(22, 18, 22, 18)
    lay.setSpacing(10)
    T.shadow(f)
    return f, lay


def restyle(w: QWidget, name: str) -> None:
    if w.objectName() != name:
        w.setObjectName(name)
        w.style().unpolish(w)
        w.style().polish(w)


def clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()


def page(title: str, subtitle: str) -> tuple[QScrollArea, QVBoxLayout]:
    area = QScrollArea()
    area.setWidgetResizable(True)
    inner = QWidget()
    inner.setObjectName("page")
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(36, 30, 36, 30)
    lay.setSpacing(16)
    head = QVBoxLayout()
    head.setSpacing(6)
    head.addWidget(label(title, "h1"))
    head.addWidget(label(subtitle, "muted", wrap=True))
    lay.addLayout(head)
    area.setWidget(inner)
    return area, lay


def setting_row(title: str, desc: str, control: QWidget, last: bool = False) -> QFrame:
    row = QFrame()
    row.setObjectName("rowLast" if last else "row")
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 8, 0, 10)
    v = QVBoxLayout()
    v.setSpacing(2)
    v.addWidget(label(title))
    if desc:
        v.addWidget(label(desc, "small", wrap=True))
    h.addLayout(v, 1)
    h.addSpacing(16)
    h.addWidget(control, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return row


class Avatar(QWidget):
    """Цветная плитка с первой буквой канала."""

    def __init__(self, name: str, size: int = 46):
        super().__init__()
        self.name = name
        self.setFixedSize(size, size)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        T.draw_tile(p, QRectF(0, 0, self.width(), self.height()), self.name[:1].upper(), self.name, self.width() * 0.3)
        p.end()


def stat_tile(name: str) -> tuple[QFrame, QLabel]:
    f = QFrame()
    f.setObjectName("stat")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(18, 14, 18, 14)
    lay.setSpacing(2)
    value = label("0", "statValue")
    lay.addWidget(value)
    lay.addWidget(label(name, "statName"))
    T.shadow(f, 22, 4, 16)
    return f, value


class Bridge(QObject):
    """Результаты фоновых задач возвращаются в поток окна через сигнал."""
    done = Signal(object, object, object)

    def __init__(self):
        super().__init__()
        self.done.connect(lambda cb, res, err: cb(res, err))

    def run(self, fn, cb) -> None:
        def work():
            try:
                res, err = fn(), None
            except Exception as e:      # показываем человеку, а не роняем окно
                res, err = None, e
            self.done.emit(cb, res, err)
        threading.Thread(target=work, daemon=True).start()


# Как оценивать клипы в канале. Ключ: включены ли там реакции (on, off, пусто = неизвестно).
_HOW_TO_VOTE = ("Чтобы оценить клип, зажми сообщение с ним в канале (или дважды тапни) и поставь 👍 или 👎. "
                "Считаются оценки всех подписчиков: чего под клипом больше, так он и оценён. Удалённый клип "
                "считается плохим. На оценках программа учится, что брать, а что нет.")
VOTES_TEXT = {
    "": _HOW_TO_VOTE,
    "on": "Реакции в канале включены ✅ " + _HOW_TO_VOTE,
    "off": "В канале сейчас выключены реакции, поэтому оценить клип негде. Нажми кнопку ниже, и под клипами "
           "появятся 👍 и 👎. " + _HOW_TO_VOTE,
}


# ----------------------------------------------------------------- карточка стрима
STATE_CHIP = {"live": "chipLive", "ad": "chipWarn", "pause": "chipWarn", "error": "chipWarn",
              "restarting": "chipWarn", "offline": "chipGray", "starting": "chipGray", "paused": "chipGray"}


class StreamCard(QFrame):
    def __init__(self, name: str, win: "MainWindow"):
        super().__init__()
        self.setObjectName("card")
        self.name = name
        T.shadow(self)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 16, 16, 16)
        lay.setSpacing(16)
        lay.addWidget(Avatar(name), 0, Qt.AlignmentFlag.AlignTop)
        left = QVBoxLayout()
        left.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(label(name, "title"))
        self.chip = label("запускается", "chipGray")
        top.addWidget(self.chip)
        top.addStretch(1)
        left.addLayout(top)
        self.title = label("", "muted")
        left.addWidget(self.title)
        self.meta = label("", "small")
        left.addWidget(self.meta)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(label("движ в чате", "small"))
        self.level = QProgressBar()
        self.level.setRange(0, 100)
        self.level.setTextVisible(False)
        self.level.setFixedWidth(220)
        bar.addWidget(self.level)
        bar.addStretch(1)
        left.addLayout(bar)
        lay.addLayout(left, 1)
        self.clip_btn = button("Клип сейчас", "primary", "clips")
        self.clip_btn.setToolTip("Сохранить последние секунды прямо сейчас, если увидел что-то смешное сам")
        self.clip_btn.clicked.connect(lambda: win.sup.clip_now(name))
        lay.addWidget(self.clip_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        rm = tool("trash", "Убрать стрим из списка")
        rm.clicked.connect(lambda: win.remove_channel(name))
        lay.addWidget(rm, 0, Qt.AlignmentFlag.AlignVCenter)

    def update_from(self, s: dict) -> None:
        hot = s["text"].startswith("🔥")
        slow = bool(s.get("slow"))                       # интернет не успевает: это важнее, чем «идёт запись»
        self.chip.setText(("● " if s["state"] == "live" and not hot and not slow else "") + s["text"])
        self.chip.setToolTip("Видео с Twitch скачивается медленнее, чем идёт эфир. Программа сама снизит качество "
                             "записи. Если не помогает, убери часть стримов." if slow else "")
        restyle(self.chip, "chipHot" if hot else "chipWarn" if slow else STATE_CHIP.get(s["state"], "chipGray"))
        restyle(self, "cardHot" if hot else "card")
        self.title.setText(s.get("title") or ("Жду, когда стример выйдет в эфир" if s["state"] == "offline" else ""))
        self.title.setVisible(bool(self.title.text()))
        parts = []
        if s["state"] in ("live", "ad", "pause"):
            parts.append(f"чат: {s['rate']:.0f} сообщ/с" if s.get("chat") else "чат: подключаюсь…")
            if s.get("quality"):
                parts.append(f"качество {s['quality']}")
            parts.append(f"на диске {s['buf_mb']} МБ")
        parts.append(f"клипов: {s['clips']}")
        self.meta.setText(" · ".join(parts))
        self.level.setValue(int(100 * s.get("level", 0)))
        self.clip_btn.setEnabled(s["state"] in ("live", "ad", "pause"))


# ----------------------------------------------------------------- главное окно
class MainWindow(QWidget):
    def __init__(self, sup: Supervisor):
        super().__init__()
        self.sup = sup
        self.bridge = Bridge()
        self.cards: dict[str, StreamCard] = {}
        self.really_quit = False
        self.for_update = False                 # программа закрывается, чтобы установщик поставил новую версию
        self.upd: dict = {}                     # найденное обновление: version, url, auto, state
        self.update_manual = False              # в прошлый раз установщик не справился: предлагаем скачать руками
        self.update_unattended = False          # только для проверки сборки: обновляться без вопроса
        self.setObjectName("root")
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(T.app_icon())
        self.resize(1120, 780)
        self.setMinimumSize(900, 600)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.update_bar = QFrame()
        self.update_bar.setObjectName("updateBar")
        ub = QHBoxLayout(self.update_bar)
        ub.setContentsMargins(20, 8, 20, 8)
        self.update_label = label("", wrap=True)
        ub.addWidget(self.update_label, 1)
        self.update_alt = button("Скачать вручную")
        self.update_alt.clicked.connect(lambda: webbrowser.open(self.upd.get("url") or updater.PAGE))
        self.update_alt.hide()
        ub.addWidget(self.update_alt)
        self.update_btn = button("Обновить", "primary")
        self.update_btn.clicked.connect(self.update_clicked)
        ub.addWidget(self.update_btn)
        self.update_bar.hide()
        outer.addWidget(self.update_bar)

        body = QHBoxLayout()
        body.setSpacing(0)
        outer.addLayout(body, 1)
        body.addWidget(self._sidebar())
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        for build in (self._page_streams, self._page_clips, self._page_learn, self._page_telegram, self._page_log,
                      self._page_settings):
            self.stack.addWidget(build())
        self.go(0)

        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(T.app_icon(), self)
            menu = QMenu()
            a1 = QAction("Открыть", self)
            a1.triggered.connect(self.show_window)
            a2 = QAction("Выход", self)
            a2.triggered.connect(self.quit)
            menu.addAction(a1)
            menu.addAction(a2)
            self.tray.setContextMenu(menu)
            self.tray.setToolTip(APP_NAME)
            self.tray.activated.connect(lambda r: self.show_window() if r == QSystemTrayIcon.ActivationReason.Trigger else None)
            self.tray.show()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(500)
        self.refresh_clips()

    # --- боковое меню ---------------------------------------------------
    def _sidebar(self) -> QWidget:
        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(228)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(16, 22, 16, 18)
        lay.setSpacing(5)
        brand = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(T.draw_logo(64))
        logo.setScaledContents(True)
        logo.setFixedSize(34, 34)
        brand.setSpacing(10)
        brand.addWidget(logo)
        brand.addWidget(label(APP_NAME, "brand"))
        brand.addStretch(1)
        lay.addLayout(brand)
        lay.addSpacing(22)
        self.nav: list[QPushButton] = []
        for i, (text, ic) in enumerate((("Стримы", "streams"), ("Клипы", "clips"), ("Обучение", "learn"),
                                        ("Telegram", "send"), ("Журнал", "log"), ("Настройки", "settings"))):
            b = QPushButton("  " + text)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setIcon(T.icon(ic, T.SIDE_TEXT, on_color="#FFFFFF"))
            b.setIconSize(QSize(18, 18))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, i=i: self.go(i))
            lay.addWidget(b)
            self.nav.append(b)
        lay.addStretch(1)
        self.side_status = label("", "sideSmall", wrap=True)
        lay.addWidget(self.side_status)
        lay.addSpacing(4)
        lay.addWidget(label(f"Версия {__version__}", "sideSmall"))
        return side

    def go(self, i: int) -> None:
        self.stack.setCurrentIndex(i)
        for n, b in enumerate(self.nav):
            b.setChecked(n == i)
        if i == 1:
            self.refresh_clips()
        if i == self.PAGE_LEARN:
            self.refresh_learn()

    # --- страница «Стримы» ------------------------------------------------
    def _page_streams(self) -> QWidget:
        area, lay = page("Стримы", "Вставь ссылку на Twitch-стрим. Программа сама дождётся эфира, будет записывать "
                                   "и вырезать моменты, на которые чат реагирует сильнее всего.")
        stats = QHBoxLayout()
        stats.setSpacing(12)
        self.stat: dict[str, QLabel] = {}
        for key, name in (("live", "стримов в эфире"), ("clips", "клипов сегодня"), ("queue", "ждут отправки"),
                          ("disk", "свободно на диске")):
            tile, value = stat_tile(name)
            self.stat[key] = value
            stats.addWidget(tile, 1)
        lay.addLayout(stats)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://www.twitch.tv/имя_канала")
        self.url.returnPressed.connect(self.add_channel)
        row.addWidget(self.url, 1)
        add = button("Добавить", "primary", "plus")
        add.clicked.connect(self.add_channel)
        row.addWidget(add)
        self.pause_btn = button("Пауза", None, "pause")
        self.pause_btn.clicked.connect(self.toggle_pause)
        row.addWidget(self.pause_btn)
        lay.addLayout(row)
        self.add_error = label("", "error")
        self.add_error.hide()
        lay.addWidget(self.add_error)
        self.streams_box = QVBoxLayout()
        self.streams_box.setSpacing(14)
        lay.addLayout(self.streams_box)
        self.empty, el = card()
        el.addWidget(label("Пока нет ни одного стрима", "h2"))
        el.addWidget(label("Скопируй ссылку на канал в браузере и вставь её в поле выше. Можно добавить несколько "
                           "стримов, они пишутся одновременно и не мешают друг другу.", "muted", wrap=True))
        lay.addWidget(self.empty)
        lay.addStretch(1)
        return area

    def add_channel(self) -> None:
        ok, msg = self.sup.add_channel(self.url.text())
        self.add_error.setVisible(not ok)
        self.add_error.setText("" if ok else msg)
        if ok:
            self.url.clear()
            self.refresh_streams()

    def remove_channel(self, name: str) -> None:
        if QMessageBox.question(self, APP_NAME, f"Убрать {name} из списка? Запись этого стрима остановится.") \
                == QMessageBox.StandardButton.Yes:
            self.sup.remove_channel(name)
            self.refresh_streams()

    def toggle_pause(self) -> None:
        self.sup.set_paused(not self.sup.cfg.get("paused"))

    def refresh_streams(self) -> None:
        snap = self.sup.snapshot()
        names = [s["name"] for s in snap]
        for name in list(self.cards):
            if name not in names:
                self.cards.pop(name).deleteLater()
        for s in snap:
            c = self.cards.get(s["name"])
            if c is None:
                c = self.cards[s["name"]] = StreamCard(s["name"], self)
                self.streams_box.addWidget(c)
            c.update_from(s)
        self.empty.setVisible(not snap)
        paused = bool(self.sup.cfg.get("paused"))
        self.pause_btn.setText("Продолжить" if paused else "Пауза")
        self.pause_btn.setIcon(T.icon("play" if paused else "pause"))
        live = sum(1 for x in snap if x["state"] in ("live", "ad", "pause"))
        self.stat["live"].setText(f"{live} из {len(snap)}" if snap else "0")
        self.stat["clips"].setText(str(sum(self.sup.stats.get("made", {}).values())))
        q = self.sup.tg.queue.counts(1.5).get("pending", 0) if self.sup.cfg.get("tg_enabled") else 0
        self.stat["queue"].setText(str(q))
        self.stat["disk"].setText(f"{self.sup.free_gb:.0f} ГБ")

    # --- страница «Клипы» -------------------------------------------------
    def _page_clips(self) -> QWidget:
        area, lay = page("Клипы", "Все нарезанные моменты. Ставь 👍 удачным и 👎 неудачным: программа учится на твоих "
                                  "оценках и со временем отбирает точнее. То же самое можно делать реакциями в Telegram-канале.")
        row = QHBoxLayout()
        self.learn_label = label("", "muted", wrap=True)
        row.addWidget(self.learn_label, 1)
        folder = button("Открыть папку", None, "folder")
        folder.clicked.connect(lambda: system.open_folder(self.sup.cfg["clips_dir"]))
        row.addWidget(folder)
        lay.addLayout(row)
        self.clips_card, self.clips_box = card()
        self.clips_box.setSpacing(0)
        lay.addWidget(self.clips_card)
        lay.addStretch(1)
        return area

    def refresh_clips(self) -> None:
        try:
            clips = self.sup.learner.recent(80)
            self.learn_label.setText(self.sup.learner.summary())
        except Exception:
            clips = []
        clear(self.clips_box)
        if not clips:
            self.clips_box.addWidget(label("Клипов пока нет. Они появятся здесь, как только чат на каком-нибудь "
                                           "стриме взорвётся.", "muted", wrap=True))
            return
        for i, c in enumerate(clips):
            self.clips_box.addWidget(self._clip_row(c, last=i == len(clips) - 1))

    def _clip_row(self, c: dict, last: bool) -> QFrame:
        row = QFrame()
        row.setObjectName("rowLast" if last else "row")
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 11, 0, 11)
        h.addWidget(Avatar(c["channel"], 38))
        h.addSpacing(12)
        v = QVBoxLayout()
        v.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(8)
        exists = os.path.exists(c["path"])
        t = label(c["title"] or os.path.basename(c["path"]), "title")
        top.addWidget(t)
        if c["kind"] != "manual":
            top.addWidget(label(f"{c['score']}", "score"))
        else:
            top.addWidget(label("вручную", "chipGray"))
        top.addStretch(1)
        v.addLayout(top)
        when = time.strftime("%d.%m %H:%M", time.localtime(c["created"]))
        v.addWidget(label(f"{c['channel']} · {when}" + ("" if exists else " · файл удалён"), "small"))
        h.addLayout(v, 1)
        up = tool("up", "Хороший клип: таких побольше", "vote", checkable=True, on_color=T.GOOD)
        down = tool("down", "Плохой клип: таких не надо", "voteBad", checkable=True, on_color=T.DANGER)
        up.setChecked(c["label"] == 1)
        down.setChecked(c["label"] == 0)

        def vote(good: bool, me: QToolButton, other: QToolButton, cid=c["id"]):
            other.setChecked(False)
            value = good if me.isChecked() else None
            self.bridge.run(lambda: self.sup.label_clip(cid, value),
                            lambda _r, _e: self.learn_label.setText(self.sup.learner.summary()))

        up.clicked.connect(lambda: vote(True, up, down))
        down.clicked.connect(lambda: vote(False, down, up))
        h.addWidget(up)
        h.addWidget(down)
        play = tool("play", "Посмотреть клип")
        play.setEnabled(exists)
        play.clicked.connect(lambda: self._open(c["path"]))
        h.addWidget(play)
        return row

    def _open(self, path: str) -> None:
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                system.open_folder(os.path.dirname(path))
        except OSError as e:
            QMessageBox.warning(self, APP_NAME, f"Не открывается: {e}")

    # --- страница «Telegram» ------------------------------------------------
    def _page_telegram(self) -> QWidget:
        cfg = self.sup.cfg
        area, lay = page("Telegram", "Клипы уходят в твой канал файлом, без сжатия: качество остаётся как в записи. "
                                     "Размер файла до 2 ГБ.")
        c, cl = card()
        st = QHBoxLayout()
        st.addWidget(label("Состояние", "h2"))
        self.tg_chip = label("выключен", "chipGray")
        st.addWidget(self.tg_chip)
        st.addStretch(1)
        cl.addLayout(st)
        self.tg_enabled = QCheckBox("Отправлять клипы в Telegram")
        self.tg_enabled.setChecked(bool(cfg["tg_enabled"]))
        cl.addWidget(self.tg_enabled)
        lay.addWidget(c)

        c, cl = card()
        cl.addWidget(label("Шаг 1. Ключи приложения", "h2"))
        cl.addWidget(label("Telegram требует их от любой программы, которая шлёт большие файлы. Получаются один раз, "
                           "бесплатно: открой my.telegram.org, войди по номеру телефона, выбери «API development tools», "
                           "создай приложение с любым названием и скопируй сюда api_id и api_hash.", "small", wrap=True))
        link = button("Открыть my.telegram.org", "link")
        link.clicked.connect(lambda: webbrowser.open("https://my.telegram.org/apps"))
        cl.addWidget(link)
        self.tg_api_id = QLineEdit(str(cfg["tg_api_id"]))
        self.tg_api_id.setPlaceholderText("api_id, например 1234567")
        self.tg_api_hash = QLineEdit(str(cfg["tg_api_hash"]))
        self.tg_api_hash.setPlaceholderText("api_hash, длинная строка из букв и цифр")
        self.tg_api_hash.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        r = QHBoxLayout()
        r.addWidget(self.tg_api_id, 1)
        r.addWidget(self.tg_api_hash, 2)
        cl.addLayout(r)
        lay.addWidget(c)

        c, cl = card()
        cl.addWidget(label("Шаг 2. Кто отправляет", "h2"))
        self.tg_mode = QComboBox()
        self.tg_mode.addItem("Мой аккаунт Telegram", "user")
        self.tg_mode.addItem("Бот (токен от @BotFather)", "bot")
        self.tg_mode.setCurrentIndex(1 if cfg["tg_mode"] == "bot" else 0)
        cl.addWidget(self.tg_mode)
        self.user_box = QWidget()
        ul = QVBoxLayout(self.user_box)
        ul.setContentsMargins(0, 0, 0, 0)
        ul.addWidget(label("Вход как в обычный Telegram: номер, потом код из приложения. Пароль и код никуда не "
                           "отправляются, кроме самого Telegram, и хранятся только на этом компьютере.", "small", wrap=True))
        r = QHBoxLayout()
        self.tg_phone = QLineEdit(str(cfg["tg_phone"]))
        self.tg_phone.setPlaceholderText("+79991234567")
        r.addWidget(self.tg_phone, 1)
        self.code_btn = button("Получить код")
        self.code_btn.clicked.connect(self.tg_request_code)
        r.addWidget(self.code_btn)
        ul.addLayout(r)
        r = QHBoxLayout()
        self.tg_code = QLineEdit()
        self.tg_code.setPlaceholderText("код из Telegram")
        self.tg_pass = QLineEdit()
        self.tg_pass.setPlaceholderText("облачный пароль, если включён")
        self.tg_pass.setEchoMode(QLineEdit.EchoMode.Password)
        r.addWidget(self.tg_code, 1)
        r.addWidget(self.tg_pass, 1)
        self.login_btn = button("Войти", "primary")
        self.login_btn.clicked.connect(self.tg_submit_code)
        r.addWidget(self.login_btn)
        ul.addLayout(r)
        cl.addWidget(self.user_box)
        self.tg_bot_token = QLineEdit(str(cfg["tg_bot_token"]))
        self.tg_bot_token.setPlaceholderText("токен бота, например 123456:ABC-DEF… (бота нужно сделать админом канала)")
        self.tg_bot_token.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        cl.addWidget(self.tg_bot_token)
        self.tg_mode.currentIndexChanged.connect(self._tg_mode_changed)
        lay.addWidget(c)

        c, cl = card()
        cl.addWidget(label("Шаг 3. Куда отправлять", "h2"))
        r = QHBoxLayout()
        self.tg_channel = QComboBox()
        self.tg_channel.setEditable(True)
        self.tg_channel.lineEdit().setPlaceholderText("@имя_канала или ссылка t.me/…")
        if cfg["tg_channel"]:                           # показываем название канала, а не его номер
            self.tg_channel.addItem(str(cfg.get("tg_channel_title") or cfg["tg_channel"]), str(cfg["tg_channel"]))
        r.addWidget(self.tg_channel, 1)
        self.chan_btn = button("Мои каналы", None, "refresh")
        self.chan_btn.setToolTip("Показать каналы, где ты админ, чтобы выбрать из списка")
        self.chan_btn.clicked.connect(self.tg_load_channels)
        r.addWidget(self.chan_btn)
        cl.addLayout(r)
        self.tg_alerts = QCheckBox("Присылать уведомления: стрим закончился, мало места, ошибки, итоги дня")
        self.tg_alerts.setChecked(bool(cfg["tg_alerts"]))
        cl.addWidget(self.tg_alerts)
        self.tg_alerts_chat = QLineEdit(str(cfg["tg_alerts_chat"]))
        self.tg_alerts_chat.setPlaceholderText("куда слать уведомления (пусто = в «Избранное»)")
        cl.addWidget(self.tg_alerts_chat)
        self.tg_delete = QCheckBox("Удалять клип с компьютера после отправки")
        self.tg_delete.setChecked(bool(cfg["delete_after_upload"]))
        cl.addWidget(self.tg_delete)
        lay.addWidget(c)

        r = QHBoxLayout()
        save = button("Сохранить и подключиться", "primary")
        save.clicked.connect(self.tg_save)
        r.addWidget(save)
        self.test_btn = button("Отправить проверочное сообщение")
        self.test_btn.setToolTip("Отправляет сообщение в канал для клипов и в чат для уведомлений и показывает, куда оно дошло")
        self.test_btn.clicked.connect(self.tg_test)
        r.addWidget(self.test_btn)
        r.addStretch(1)
        lay.addLayout(r)
        self.tg_msg = label("", "muted", wrap=True)
        lay.addWidget(self.tg_msg)

        c, cl = card()
        cl.addWidget(label("Оценки клипов", "h2"))
        self.votes_lbl = label(VOTES_TEXT[""], "muted", wrap=True)
        cl.addWidget(self.votes_lbl)
        r = QHBoxLayout()
        self.votes_btn = button("Включить оценки 👍 и 👎 в канале")
        self.votes_btn.setToolTip("Разрешает в твоём канале реакции 👍 и 👎 под сообщениями. Остальные настройки канала "
                                  "не меняются. Выключить можно в самом Telegram: канал → Изменить → Реакции")
        self.votes_btn.clicked.connect(self.tg_votes_on)
        self.votes_btn.setVisible(False)
        r.addWidget(self.votes_btn)
        r.addStretch(1)
        cl.addLayout(r)
        lay.addWidget(c)
        lay.addStretch(1)
        self._tg_mode_changed()
        return area

    def tg_votes_on(self) -> None:
        self.votes_btn.setEnabled(False)

        def done(res, err):
            self.votes_btn.setEnabled(True)
            why = str(err) if err else res
            if why:
                self.tg_note(f"Оценки включить не получилось: {why}", True)
            else:
                self.tg_note("Готово: под клипами в канале теперь можно ставить 👍 и 👎.")

        self.bridge.run(self.sup.tg.enable_reactions, done)

    def _tg_mode_changed(self) -> None:
        bot = self.tg_mode.currentData() == "bot"
        self.user_box.setVisible(not bot)
        self.tg_bot_token.setVisible(bot)
        self.chan_btn.setVisible(not bot)

    def tg_note(self, text: str, error: bool = False) -> None:
        self.tg_msg.setText(text)
        restyle(self.tg_msg, "error" if error else "muted")

    def _tg_collect(self) -> dict:
        cfg = dict(self.sup.cfg)
        chan, title = pick_channel(self.tg_channel.currentText(),
                                   [(self.tg_channel.itemText(i), self.tg_channel.itemData(i))
                                    for i in range(self.tg_channel.count())], self.tg_channel.currentIndex())
        cfg.update(tg_enabled=self.tg_enabled.isChecked(), tg_mode=self.tg_mode.currentData(),
                   tg_api_id=self.tg_api_id.text().strip(), tg_api_hash=self.tg_api_hash.text().strip(),
                   tg_bot_token=self.tg_bot_token.text().strip(), tg_phone=self.tg_phone.text().strip(),
                   tg_channel=chan, tg_channel_title=title, tg_alerts=self.tg_alerts.isChecked(),
                   tg_alerts_chat=self.tg_alerts_chat.text().strip(), delete_after_upload=self.tg_delete.isChecked())
        return cfg

    def tg_save(self) -> None:
        self.sup.apply_config(self._tg_collect())
        self.tg_note("Сохранено. Состояние подключения видно вверху страницы.")

    def tg_test(self) -> None:
        tg = self.sup.tg
        shown = self._tg_collect()
        if any(shown.get(k) != self.sup.cfg.get(k) for k in TG_KEYS):
            return self.tg_note("На этой странице есть несохранённые изменения. Сначала нажми «Сохранить и "
                                "подключиться», дождись состояния «подключён» и проверь ещё раз.", True)
        if tg.status not in ("ok", "no_channel"):
            return self.tg_note(f"Telegram пока не подключён ({tg.status_text}). Сначала нажми «Сохранить и "
                                "подключиться» и дождись состояния «подключён».", True)
        self.test_btn.setEnabled(False)
        self.tg_note("Отправляю проверочное сообщение…")

        def done(res, err):
            self.test_btn.setEnabled(True)
            if err:
                return self.tg_note(f"Не получилось: {err}", True)
            text, bad = summarize_test(res, bool(self.sup.cfg.get("tg_alerts", True)))
            self.tg_note(text, bad)

        self.bridge.run(tg.send_test, done)

    def tg_request_code(self) -> None:
        cfg = self._tg_collect()
        cfg["tg_enabled"] = True
        self.tg_enabled.setChecked(True)
        self.sup.apply_config(cfg)
        phone = cfg["tg_phone"]
        if not phone:
            return self.tg_note("Впиши номер телефона в международном формате, например +79991234567", True)
        self.code_btn.setEnabled(False)
        self.tg_note("Подключаюсь к Telegram…")

        def work():
            for _ in range(40):                       # ждём, пока поток Telegram поднимет соединение
                if self.sup.tg.status in ("need_login", "ok", "no_channel"):
                    break
                if self.sup.tg.status == "error":
                    raise RuntimeError(self.sup.tg.status_text)
                time.sleep(0.5)
            if self.sup.tg.status in ("ok", "no_channel"):
                return "Ты уже вошёл в аккаунт"
            return self.sup.tg.request_code(phone)

        def done(res, err):
            self.code_btn.setEnabled(True)
            self.tg_note(f"Не получилось: {err}" if err else f"{res}. Впиши код ниже и нажми «Войти».", bool(err))

        self.bridge.run(work, done)

    def tg_submit_code(self) -> None:
        code, pwd = self.tg_code.text().strip(), self.tg_pass.text()
        if not code:
            return self.tg_note("Впиши код, который пришёл в Telegram", True)
        self.login_btn.setEnabled(False)

        def done(res, err):
            self.login_btn.setEnabled(True)
            if err:
                self.tg_note(f"Не получилось войти: {err}", True)
            elif res == "2fa":
                self.tg_note("У аккаунта включён облачный пароль. Впиши его в поле справа и нажми «Войти» ещё раз.", True)
            else:
                self.tg_code.clear()
                self.tg_pass.clear()
                self.tg_note("Вход выполнен. Теперь нажми «Мои каналы» и выбери канал для клипов.")

        self.bridge.run(lambda: self.sup.tg.submit_code(code, pwd), done)

    def tg_load_channels(self) -> None:
        self.chan_btn.setEnabled(False)

        def done(res, err):
            self.chan_btn.setEnabled(True)
            if err:
                return self.tg_note(f"Не удалось получить список каналов: {err}. Сначала войди в аккаунт.", True)
            cur = self.tg_channel.currentText().strip()
            saved = str(self.sup.cfg.get("tg_channel", ""))
            self.tg_channel.clear()
            for cid, title in res:
                self.tg_channel.addItem(title, cid)
            # Прежний выбор сохраняем. Если его в списке нет, не выбираем ничего: иначе первым в списке
            # молча оказался бы чужой канал, и клипы ушли бы не туда.
            keep = next((i for i, (cid, title) in enumerate(res) if cid == saved or title == cur), -1)
            self.tg_channel.setCurrentIndex(keep)
            if keep < 0:
                self.tg_channel.setEditText("" if res else cur)
            self.tg_note(f"Найдено каналов: {len(res)}. Выбери нужный в списке и нажми «Сохранить и подключиться»." if res
                         else "Не нашёл каналов, где ты админ. Создай канал в Telegram и нажми ещё раз.")

        self.bridge.run(self.sup.tg.list_channels, done)

    # --- страница «Обучение» -------------------------------------------------
    PAGE_LEARN = 2

    def _page_learn(self) -> QWidget:
        area, lay = page("Обучение", "Как программа выбирает моменты и чему она научилась на твоих оценках. "
                                     "Страница пишется сама: после каждой оценки сюда добавляется вывод.")
        self.learn_box = QVBoxLayout()
        self.learn_box.setSpacing(16)
        lay.addLayout(self.learn_box)
        b = button("Открыть текстовым файлом", "link")
        b.setToolTip("Тот же текст одним файлом: его можно переслать или сохранить.")
        b.clicked.connect(self.open_bible)
        lay.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addStretch(1)
        self._learn_shown: list | None = None
        self._learn_at = 0.0
        return area

    def _bible(self) -> list[tuple[str, list[str]]]:
        return bible.build(self.sup.learner, self.sup.cfg, {s["name"]: s for s in self.sup.snapshot()})

    def refresh_learn(self) -> None:
        self._learn_at = time.time()
        try:
            sections = self._bible()
        except Exception as e:
            self.sup.say(f"Страница «Обучение» не собралась: {e}", "error")
            return
        if sections == self._learn_shown:
            return
        self._learn_shown = sections
        clear(self.learn_box)
        for title, lines in sections:
            f, cl = card()
            cl.addWidget(label(title, "h2"))
            for ln in lines:
                cl.addWidget(label("•  " + ln, "muted", wrap=True))
            self.learn_box.addWidget(f)

    def open_bible(self) -> None:
        try:
            path = str(bible.save(self._bible()))
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                webbrowser.open("file://" + path)
        except Exception as e:
            self.sup.say(f"Файл «{bible.FILE}» не открылся: {e}", "error")

    # --- страница «Журнал» ------------------------------------------------
    def _page_log(self) -> QWidget:
        w = QWidget()
        w.setObjectName("page")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(36, 30, 36, 30)
        lay.setSpacing(14)
        lay.addWidget(label("Журнал", "h1"))
        lay.addWidget(label("Всё, что делает программа. Если что-то пошло не так, причина будет здесь.", "muted"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1500)
        lay.addWidget(self.log, 1)
        b = button("Открыть папку с подробными логами", "link")
        b.clicked.connect(lambda: system.open_folder(str(config.subdir("logs"))))
        lay.addWidget(b)
        return w

    # --- страница «Настройки» ---------------------------------------------
    SETTINGS = [
        ("Запись", [
            ("quality", "Качество записи", "Чем ниже, тем меньше нагрузка на интернет и диск. «Как у стримера» даёт лучшие клипы.",
             "choice", [("best", "Как у стримера"), ("1080", "До 1080p"), ("720", "До 720p"), ("480", "До 480p")]),
            ("quality_floor", "При медленном интернете не опускать ниже", "Когда интернет не успевает за стримами, "
             "программа сама снижает качество записи, но не ниже этого. Если не хватает и на него, она попросит "
             "убрать стрим: мутные клипы никому не нужны.", "choice",
             [("1080", "1080p"), ("720", "720p"), ("480", "480p"), ("0", "Опускать сколько нужно")]),
            ("buffer_minutes", "Сколько последних минут хранить", "Запись идёт по кругу: старое стирается само. "
             "6 минут в лучшем качестве занимают около 350 МБ на стрим.", "int", (4, 30)),
            ("max_streams", "Максимум стримов одновременно", "", "int", (1, 12)),
            ("twitch_oauth", "Токен Twitch (необязательно)", "Если у аккаунта есть Turbo или подписка на канал, с токеном "
             "не будет рекламных пауз. Без токена реклама просто вырезается из записи.", "secret", None),
        ]),
        ("Вид клипа", [
            ("clip_format", "Формат клипа", "Вертикальный: 1080×1920, сразу готов для TikTok, Shorts и Reels. "
             "«Как на стриме» сохраняет запись без изменений.", "choice",
             [("tiktok", "Вертикальный для TikTok"), ("original", "Как на стриме, 16:9"), ("both", "Оба файла")]),
            ("tt_layout", "Вертикальный кадр", "Крупно: как в TikTok-нарезках, видео во всю ширину, края по бокам "
             "немного обрезаются, чат под видео. Целиком: стрим виден весь, но мельче. На весь экран: ещё крупнее, "
             "по бокам обрезается больше половины.", "choice",
             [("big", "Крупно, как в TikTok-нарезках"), ("blur", "Стрим целиком, фон размыт"), ("crop", "На весь экран")]),
            ("tt_title", "Заголовок над видео", "Крупная надпись над видео. С включённым ИИ программа сама пишет, что "
             "происходит на экране. Без ИИ надпись простая: «Чат разрывает от смеха».", "bool", None),
            ("tt_title_text", "Свой текст заголовка", "Если заполнить, эта надпись будет на каждом клипе. "
             "Пусто: программа придумывает сама.", "text", None),
            ("chat_style", "Чат стрима в клипе", "Сообщения появляются в клипе тогда же, когда были в эфире. "
             "Удалённые модераторами сообщения, боты и ссылки не показываются.", "choice",
             [("soft", "Полупрозрачный"), ("text", "Только текст"), ("solid", "На тёмных плашках"), ("off", "Не показывать")]),
            ("chat_size", "Размер чата", "", "choice", [("s", "Мелкий"), ("m", "Средний"), ("l", "Крупный")]),
            ("chat_hide", "Не показывать сообщения со словами", "Через запятую. Сообщение с любым из этих слов "
             "в клип не попадёт.", "text", None),
        ]),
        ("Клипы", [
            ("clips_dir", "Папка для клипов", "", "folder", None),
            ("sensitivity", "Чувствительность", "1: только самые мощные моменты. 5: брать почти любую реакцию чата.", "slider", (1, 5)),
            ("clips_per_hour", "Клипов в час, не больше", "Со всех стримов вместе. Когда моментов больше, они ждут "
             "очереди: первым выходит самый сильный, а самые слабые пропускаются. Клипы по кнопке «Клип сейчас» "
             "не считаются. 0: без предела.", "int", (0, 120)),
            ("clip_min", "Минимальная длина клипа, секунд", "", "int", (10, 120)),
            ("clip_max", "Максимальная длина клипа, секунд", "", "int", (15, 180)),
            ("lead_in", "Секунд до реакции чата", "Чат реагирует с опозданием. Столько секунд перед его реакцией попадёт "
             "в клип, чтобы был виден сам момент и подводка к нему.", "int", (5, 45)),
            ("manual_len", "Длина клипа по кнопке «Клип сейчас», секунд", "", "int", (15, 150)),
            ("min_score", "Не сохранять клипы с оценкой ниже", "0: сохранять всё, что поймано.", "int", (0, 95)),
            ("max_clips_gb", "Лимит папки клипов, ГБ", "Когда папка больше, самые старые отправленные клипы удаляются.", "int", (1, 2000)),
            ("min_free_gb", "Беречь свободное место на диске, ГБ", "Если на диске остаётся меньше, новые клипы не "
             "сохраняются, а тебе приходит уведомление.", "int", (1, 500)),
        ]),
        ("Описание клипов", [
            ("whisper", "Распознавать речь стримера", "Работает на этом компьютере, бесплатно. В описании клипа появится "
             "фраза, которую сказал стример. При первом клипе скачает модель (около 500 МБ).", "bool", None),
            ("whisper_model", "Модель распознавания", "small: хороший баланс. medium точнее, но медленнее и тяжелее.",
             "choice", [("base", "base (быстрая)"), ("small", "small"), ("medium", "medium (точная)")]),
            ("ai_enabled", "Заголовок, описание и оценка от ИИ", "Claude читает расшифровку и чат, пишет заголовок и "
             "описание, уточняет границы клипа. Один короткий запрос на клип, доли цента.", "bool", None),
            ("ai_api_key", "Ключ Anthropic API", "Берётся на console.anthropic.com. Хранится только на этом компьютере.", "secret", None),
            ("ai_frames", "Показывать ИИ кадры из клипа", "ИИ видит, что происходит на экране, и заголовок получается "
             "по делу, а не только по словам. Вместе с кадрами запрос всё равно стоит доли цента.", "bool", None),
            ("ai_filter", "Разрешить ИИ отбрасывать несмешное", "", "bool", None),
        ]),
        ("Система", [
            ("autostart", "Запускать вместе с Windows", "Программа стартует свёрнутой и продолжает следить за стримами.", "bool", None),
            ("prevent_sleep", "Не давать компьютеру засыпать", "", "bool", None),
            ("auto_update_streamlink", "Обновлять модуль записи Twitch автоматически", "Twitch иногда меняет правила, "
             "и запись ломается. С этой галочкой программа чинит себя сама.", "bool", None),
        ]),
    ]

    def _page_settings(self) -> QWidget:
        cfg = self.sup.cfg
        area, lay = page("Настройки", "Всё уже настроено разумно, менять что-либо необязательно.")
        self.fields: dict[str, tuple[str, QWidget]] = {}
        for section, rows in self.SETTINGS:
            c, cl = card()
            cl.setSpacing(0)
            cl.addWidget(label(section, "h2"))
            for i, (key, title, desc, kind, opt) in enumerate(rows):
                w: QWidget
                if kind == "bool":
                    w = QCheckBox()
                    w.setChecked(bool(cfg[key]))
                elif kind == "int":
                    w = QSpinBox()
                    w.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    w.setFixedWidth(84)
                    w.setRange(*opt)
                    w.setValue(int(cfg[key]))
                elif kind == "slider":
                    w = QWidget()
                    hl = QHBoxLayout(w)
                    hl.setContentsMargins(0, 0, 0, 0)
                    s = QSlider(Qt.Orientation.Horizontal)
                    s.setRange(*opt)
                    s.setValue(int(cfg[key]))
                    s.setFixedWidth(170)
                    val = label(str(cfg[key]), "title")
                    s.valueChanged.connect(lambda v, val=val: val.setText(str(v)))
                    hl.addWidget(s)
                    hl.addWidget(val)
                    w.slider = s  # type: ignore[attr-defined]
                elif kind == "choice":
                    w = QComboBox()
                    for value, text in opt:
                        w.addItem(text, value)
                    w.setCurrentIndex(max(0, w.findData(str(cfg[key]))))
                elif kind == "folder":
                    w = QWidget()
                    hl = QHBoxLayout(w)
                    hl.setContentsMargins(0, 0, 0, 0)
                    e = QLineEdit(str(cfg[key]))
                    e.setMinimumWidth(300)
                    pick = button("Выбрать…")
                    pick.clicked.connect(lambda _=False, e=e: e.setText(
                        QFileDialog.getExistingDirectory(self, "Папка для клипов", e.text()) or e.text()))
                    hl.addWidget(e)
                    hl.addWidget(pick)
                    w.edit = e  # type: ignore[attr-defined]
                else:
                    w = QLineEdit(str(cfg[key]))
                    w.setMinimumWidth(300)
                    if kind == "secret":
                        w.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
                self.fields[key] = (kind, w)
                cl.addWidget(setting_row(title, desc, w, last=i == len(rows) - 1))
            lay.addWidget(c)
        c, cl = card()
        cl.setSpacing(0)
        cl.addWidget(label("Обновление программы", "h2"))
        self.check_btn = button("Проверить обновления")
        self.check_btn.clicked.connect(self.check_updates)
        how = ("сверху появится полоска с кнопкой «Обновить»: программа сама скачает новую версию, поставит её "
               "и откроется заново." if self.sup.self_update else
               "сверху появится полоска с кнопкой «Скачать».")
        cl.addWidget(setting_row(f"Установлена версия {__version__}", "Программа сама проверяет, не вышла ли новая: "
                                 "при запуске и дважды в сутки. Когда выйдет, " + how, self.check_btn, last=True))
        self.check_label = label("", "small", wrap=True)
        self.check_label.hide()
        cl.addWidget(self.check_label)
        lay.addWidget(c)
        r = QHBoxLayout()
        save = button("Сохранить", "primary")
        save.clicked.connect(self.save_settings)
        r.addWidget(save)
        self.saved_label = label("", "muted")
        r.addWidget(self.saved_label)
        r.addStretch(1)
        lay.addLayout(r)
        lay.addStretch(1)
        return area

    def save_settings(self) -> None:
        cfg = dict(self.sup.cfg)
        for key, (kind, w) in self.fields.items():
            if kind == "bool":
                cfg[key] = w.isChecked()
            elif kind == "int":
                cfg[key] = w.value()
            elif kind == "slider":
                cfg[key] = w.slider.value()
            elif kind == "choice":
                cfg[key] = w.currentData()
            elif kind == "folder":
                cfg[key] = w.edit.text().strip() or config.default_clips_dir()
            else:
                cfg[key] = w.text().strip()
        cfg["clip_max"] = max(cfg["clip_max"], cfg["clip_min"])
        self.sup.apply_config(cfg)
        self.saved_label.setText("Сохранено в " + time.strftime("%H:%M:%S"))

    # --- обновление экрана --------------------------------------------------
    def tick(self) -> None:
        clips_changed = False
        while True:
            try:
                ev = self.sup.events.get_nowait()
            except Exception:
                break
            t = ev.get("t")
            if t == "log":
                self.log.appendPlainText(("⚠ " if ev.get("level") == "error" else "") + f"{ev['time']}  {ev['text']}")
            elif t in ("clip", "learned"):
                clips_changed = True
                if t == "clip" and self.tray and not self.isVisible():
                    self.tray.showMessage(APP_NAME, f"Новый клип: {ev.get('channel')}", T.app_icon(), 4000)
            elif t in ("update", "no_update", "update_error", "update_progress", "update_ready", "update_failed"):
                self.on_update(ev)
        if not self.isVisible():
            return
        self.refresh_streams()
        if clips_changed and self.stack.currentIndex() == 1:
            self.refresh_clips()
        if self.stack.currentIndex() == self.PAGE_LEARN and time.time() - self._learn_at > 5:
            self.refresh_learn()
        tg = self.sup.tg
        q = tg.queue.counts(1.5).get("pending", 0) if self.sup.cfg.get("tg_enabled") else 0
        self.tg_chip.setText(tg.status_text + (f" · в очереди {q}" if q else ""))
        votes = tg.votes if tg.status == "ok" else ""
        if self.votes_lbl.text() != VOTES_TEXT[votes]:
            self.votes_lbl.setText(VOTES_TEXT[votes])
        self.votes_btn.setVisible(votes == "off")
        restyle(self.tg_chip, {"ok": "chip", "off": "chipGray", "connecting": "chipGray"}.get(tg.status, "chipWarn"))
        lines = [f"Telegram: {tg.status_text}" + (f", в очереди {q}" if q else "")]
        if tg.progress:
            lines.append("Отправка " + tg.progress[-28:])
        if self.sup.editing:
            lines.append("Монтирую клип…")
        held = self.sup.pacer.waiting()
        if held:
            lines.append(f"Моментов ждёт очереди: {held}")
        lines.append(f"Свободно на диске: {self.sup.free_gb:.0f} ГБ")
        if self.sup.cpu:
            lines.append(f"Процессор {self.sup.cpu:.0f}% · память {self.sup.ram:.0f}%")
        self.side_status.setText("\n".join(lines))

    # --- обновление программы -----------------------------------------------
    def check_updates(self) -> None:
        self.check_btn.setEnabled(False)
        self._say_check("Проверяю…")
        self.sup.check_update_now()

    def _say_check(self, text: str) -> None:
        self.check_label.setText(text)
        self.check_label.setVisible(bool(text))

    def on_update(self, ev: dict) -> None:
        t = ev["t"]
        if t in ("update", "no_update", "update_error"):          # ответ на «Проверить обновления» или фоновую проверку
            asked = not self.check_btn.isEnabled()
            self.check_btn.setEnabled(True)
            if t == "no_update":
                self._say_check("У тебя последняя версия.")
            elif t == "update_error":
                self._say_check(f"Не получилось проверить: {ev['error']}.")
            elif asked:
                self._say_check(f"Вышла новая версия {ev['version']}. Кнопка обновления вверху окна.")
        if t == "update":
            new = self.upd.get("version") != ev["version"]
            if self.upd.get("state") in ("downloading", "installing"):
                return                                            # обновление уже идёт: полоску не трогаем
            self.upd = {"version": ev["version"], "url": ev["url"], "state": "found",
                        "auto": bool(ev.get("auto")) and not self.update_manual}
            self.update_label.setText(f"Вышла новая версия {ev['version']}")
            self.update_btn.setText("Обновить" if self.upd["auto"] else "Скачать")
            self.update_btn.setEnabled(True)
            self.update_alt.hide()
            self.update_bar.show()
            if new and self.tray and not self.isVisible():
                self.tray.showMessage(APP_NAME, f"Вышла новая версия {ev['version']}. Открой окно программы, "
                                                "чтобы обновить.", T.app_icon(), 6000)
            if self.update_unattended and self.upd["auto"]:
                self.start_update()
        elif t == "update_progress" and self.upd.get("state") == "downloading":
            self.update_label.setText(f"Скачиваю обновление {self.upd['version']}: {ev['pct']}%")
        elif t == "update_failed":
            self._update_trouble(f"Обновление не скачалось: {ev['error']}.")
        elif t == "update_ready":
            try:
                updater.launch_installer(ev["path"], ev["version"])
            except Exception as e:
                self.sup.say(f"Обновление {ev['version']}: {e}", "error")
                self._update_trouble(f"Обновление не началось: {e}.")
                return
            self.upd["state"] = "installing"
            self.update_label.setText(f"Ставлю версию {ev['version']}. Программа сейчас закроется и откроется сама.")
            self.sup.say(f"Ставлю версию {ev['version']}: программа закроется и откроется сама")
            QTimer.singleShot(1200, self.quit_for_update)         # даём прочитать надпись

    def _update_trouble(self, text: str) -> None:
        self.upd["state"] = "failed"
        self.update_label.setText(text + " Попробуй ещё раз или скачай установщик вручную.")
        self.update_btn.setText("Повторить")
        self.update_btn.setEnabled(True)
        self.update_alt.show()
        self.update_bar.show()

    def confirm_update(self) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"Обновить программу до версии {self.upd.get('version', '')}?")
        box.setInformativeText("Программа скачает новую версию, закроется примерно на минуту и откроется сама. "
                               "Запись стримов на это время прервётся, поэтому лучше обновляться, когда в эфире "
                               "нет ничего важного. Настройки и клипы останутся на месте.")
        yes = box.addButton("Обновить", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Позже", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is yes

    def update_clicked(self) -> None:
        if not self.upd:
            return
        if not self.upd.get("auto"):
            webbrowser.open(self.upd.get("url") or updater.PAGE)
            return
        if self.upd.get("state") in ("found", "failed") and self.confirm_update():
            self.start_update()

    def start_update(self) -> None:
        if not self.sup.download_update():
            return
        self.upd["state"] = "downloading"
        self.update_label.setText(f"Скачиваю обновление {self.upd['version']}: 0%")
        self.update_btn.setText("Скачиваю…")
        self.update_btn.setEnabled(False)
        self.update_alt.hide()

    def quit_for_update(self) -> None:
        self.for_update = True
        self.quit()

    # --- окно и трей --------------------------------------------------------
    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit(self) -> None:
        self.really_quit = True
        self.close()

    def closeEvent(self, e) -> None:  # noqa: N802
        if not self.really_quit and self.tray and self.sup.snapshot():
            e.ignore()
            self.hide()
            self.tray.showMessage(APP_NAME, "Программа работает в фоне и продолжает запись. Выход: правой кнопкой по "
                                            "значку возле часов.", T.app_icon(), 5000)
            return
        self.timer.stop()
        self.sup.shutdown(for_update=self.for_update)
        e.accept()
        QApplication.quit()


def main(argv: list[str]) -> int:
    util.setup_logging("app")
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    T.load_fonts()
    app.setFont(T.app_font())
    app.setStyleSheet(T.stylesheet(str(config.data_dir())))
    if not system.single_instance():
        QMessageBox.information(None, APP_NAME, "Программа уже запущена. Ищи её значок возле часов.")
        return 0
    sup = Supervisor()
    sup.start()
    win = MainWindow(sup)
    outcome, ver = updater.finish_update(argv)            # чем кончилось обновление, начатое в прошлый раз
    if outcome == "ok":
        sup.say(f"Программа обновлена до версии {ver}")
    elif outcome == "failed":
        win.update_manual = True
        sup.say(f"Обновление до {ver} не установилось. Скачай установщик вручную: кнопка «Скачать» вверху окна "
                "появится через полминуты", "error")
    # Проверка сборки: программа сама находит «новую версию» на этом же компьютере и ставит её без вопроса
    win.update_unattended = bool(updater.test_api()) and "--updated" not in argv
    if "--minimized" in argv and win.tray:
        pass                                   # автозапуск: сидим в трее и не мешаем
    else:
        win.show()
    shot = os.environ.get("STREAMCLIPPER_SCREENSHOT")     # для проверки интерфейса при сборке
    if shot:
        def snap(i=[0]):
            win.go(i[0])
            win.tick()
            QApplication.processEvents()
            win.grab().save(f"{shot}_{i[0]}.png")
            i[0] += 1
            if i[0] >= win.stack.count():
                win.quit()
        st = QTimer(win)
        st.timeout.connect(snap)
        st.start(int(os.environ.get("STREAMCLIPPER_SCREENSHOT_MS", "1500")))
    code = app.exec()
    return code
