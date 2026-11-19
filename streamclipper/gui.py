"""Окно программы. Слева меню, справа страницы: Стримы, Клипы, Telegram, Журнал, Настройки."""
from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QScrollArea, QSlider, QSpinBox, QStackedWidget, QSystemTrayIcon, QToolButton,
                               QVBoxLayout, QWidget)

from . import APP_NAME, __version__, config, system, util
from .supervisor import Supervisor
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


def tool(icon: str, tip: str, name: str | None = None, checkable: bool = False) -> QToolButton:
    b = QToolButton()
    b.setIcon(T.icon(icon, T.MUTED))
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
    lay.setContentsMargins(18, 14, 18, 14)
    lay.setSpacing(8)
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
    lay.setContentsMargins(32, 26, 32, 26)
    lay.setSpacing(14)
    lay.addWidget(label(title, "h1"))
    sub = label(subtitle, "muted", wrap=True)
    lay.addWidget(sub)
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


# ----------------------------------------------------------------- карточка стрима
STATE_CHIP = {"live": "chipLive", "ad": "chipWarn", "pause": "chipWarn", "error": "chipWarn",
              "restarting": "chipWarn", "offline": "chipGray", "starting": "chipGray", "paused": "chipGray"}


class StreamCard(QFrame):
    def __init__(self, name: str, win: "MainWindow"):
        super().__init__()
        self.setObjectName("card")
        self.name = name
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 14, 14, 14)
        left = QVBoxLayout()
        left.setSpacing(5)
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
        self.chip.setText(s["text"])
        restyle(self.chip, "chip" if s["text"].startswith("🔥") else STATE_CHIP.get(s["state"], "chipGray"))
        restyle(self, "cardHot" if s["text"].startswith("🔥") else "card")
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
        self.setObjectName("root")
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(T.app_icon())
        self.resize(1060, 740)
        self.setMinimumSize(900, 600)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.update_bar = QFrame()
        self.update_bar.setObjectName("updateBar")
        ub = QHBoxLayout(self.update_bar)
        ub.setContentsMargins(20, 8, 20, 8)
        self.update_label = label("")
        ub.addWidget(self.update_label, 1)
        self.update_btn = button("Скачать", "primary")
        ub.addWidget(self.update_btn)
        self.update_bar.hide()
        outer.addWidget(self.update_bar)

        body = QHBoxLayout()
        body.setSpacing(0)
        outer.addLayout(body, 1)
        body.addWidget(self._sidebar())
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        for build in (self._page_streams, self._page_clips, self._page_telegram, self._page_log, self._page_settings):
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
        side.setFixedWidth(214)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(14, 18, 14, 14)
        lay.setSpacing(4)
        brand = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(T.draw_logo(64))
        logo.setScaledContents(True)
        logo.setFixedSize(30, 30)
        brand.addWidget(logo)
        brand.addWidget(label(APP_NAME, "brand"))
        brand.addStretch(1)
        lay.addLayout(brand)
        lay.addSpacing(16)
        self.nav: list[QPushButton] = []
        for i, (text, ic) in enumerate((("Стримы", "streams"), ("Клипы", "clips"), ("Telegram", "send"),
                                        ("Журнал", "log"), ("Настройки", "settings"))):
            b = QPushButton("  " + text)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setIcon(T.icon(ic, "#4A4945"))
            b.setIconSize(QSize(18, 18))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, i=i: self.go(i))
            lay.addWidget(b)
            self.nav.append(b)
        lay.addStretch(1)
        self.side_status = label("", "small", wrap=True)
        lay.addWidget(self.side_status)
        lay.addWidget(label(f"Версия {__version__}", "small"))
        return side

    def go(self, i: int) -> None:
        self.stack.setCurrentIndex(i)
        for n, b in enumerate(self.nav):
            b.setChecked(n == i)
        if i == 1:
            self.refresh_clips()

    # --- страница «Стримы» ------------------------------------------------
    def _page_streams(self) -> QWidget:
        area, lay = page("Стримы", "Вставь ссылку на Twitch-стрим. Программа сама дождётся эфира, будет записывать "
                                   "и вырезать моменты, на которые чат реагирует сильнее всего.")
        row = QHBoxLayout()
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
        self.streams_box.setSpacing(10)
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
        h.setContentsMargins(0, 9, 0, 9)
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
        up = tool("up", "Хороший клип: таких побольше", "vote", checkable=True)
        down = tool("down", "Плохой клип: таких не надо", "voteBad", checkable=True)
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
        if cfg["tg_channel"]:
            self.tg_channel.addItem(str(cfg["tg_channel"]), str(cfg["tg_channel"]))
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
        test = button("Отправить проверочное сообщение")
        test.clicked.connect(lambda: (self.sup.tg.alert("Проверка связи: всё работает ✅"), self.tg_note("Отправил. "
                             "Сообщение придёт в чат для уведомлений (по умолчанию «Избранное»).")))
        r.addWidget(test)
        r.addStretch(1)
        lay.addLayout(r)
        self.tg_msg = label("", "muted", wrap=True)
        lay.addWidget(self.tg_msg)
        lay.addStretch(1)
        self._tg_mode_changed()
        return area

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
        chan = self.tg_channel.currentText().strip()
        idx = self.tg_channel.currentIndex()
        if idx >= 0 and self.tg_channel.itemText(idx) == chan and self.tg_channel.itemData(idx):
            chan = str(self.tg_channel.itemData(idx))
        cfg.update(tg_enabled=self.tg_enabled.isChecked(), tg_mode=self.tg_mode.currentData(),
                   tg_api_id=self.tg_api_id.text().strip(), tg_api_hash=self.tg_api_hash.text().strip(),
                   tg_bot_token=self.tg_bot_token.text().strip(), tg_phone=self.tg_phone.text().strip(),
                   tg_channel=chan, tg_alerts=self.tg_alerts.isChecked(),
                   tg_alerts_chat=self.tg_alerts_chat.text().strip(), delete_after_upload=self.tg_delete.isChecked())
        return cfg

    def tg_save(self) -> None:
        self.sup.apply_config(self._tg_collect())
        self.tg_note("Сохранено. Состояние подключения видно вверху страницы.")

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
            cur = self.tg_channel.currentText()
            self.tg_channel.clear()
            for cid, title in res:
                self.tg_channel.addItem(title, cid)
            if cur and self.tg_channel.findText(cur) < 0 and not res:
                self.tg_channel.setEditText(cur)
            self.tg_note(f"Найдено каналов: {len(res)}. Выбери нужный и нажми «Сохранить и подключиться»." if res
                         else "Не нашёл каналов, где ты админ. Создай канал в Telegram и нажми ещё раз.")

        self.bridge.run(self.sup.tg.list_channels, done)

    # --- страница «Журнал» ------------------------------------------------
    def _page_log(self) -> QWidget:
        w = QWidget()
        w.setObjectName("page")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(32, 26, 32, 26)
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
            ("buffer_minutes", "Сколько последних минут хранить", "Запись идёт по кругу: старое стирается само. "
             "6 минут в лучшем качестве занимают около 350 МБ на стрим.", "int", (4, 30)),
            ("max_streams", "Максимум стримов одновременно", "", "int", (1, 12)),
            ("twitch_oauth", "Токен Twitch (необязательно)", "Если у аккаунта есть Turbo или подписка на канал, с токеном "
             "не будет рекламных пауз. Без токена реклама просто вырезается из записи.", "secret", None),
        ]),
        ("Клипы", [
            ("clips_dir", "Папка для клипов", "", "folder", None),
            ("sensitivity", "Чувствительность", "1: только самые мощные моменты. 5: брать почти любую реакцию чата.", "slider", (1, 5)),
            ("clip_min", "Минимальная длина клипа, секунд", "", "int", (10, 120)),
            ("clip_max", "Максимальная длина клипа, секунд", "", "int", (15, 180)),
            ("lead_in", "Секунд до реакции чата", "Чат реагирует с опозданием. Столько секунд перед его реакцией попадёт "
             "в клип, чтобы был виден сам момент и подводка к нему.", "int", (5, 45)),
            ("manual_len", "Длина клипа по кнопке «Клип сейчас», секунд", "", "int", (15, 150)),
            ("min_score", "Не сохранять клипы с оценкой ниже", "0: сохранять всё, что поймано.", "int", (0, 95)),
            ("vertical", "Делать ещё и вертикальную версию 9:16", "Для TikTok, Shorts и Reels. Нагружает процессор на время "
             "монтажа клипа.", "bool", None),
            ("max_clips_gb", "Лимит папки клипов, ГБ", "Когда папка больше, самые старые отправленные клипы удаляются.", "int", (1, 2000)),
            ("min_free_gb", "Беречь свободное место на диске, ГБ", "Если на диске остаётся меньше, новые клипы не "
             "сохраняются, а тебе приходит уведомление.", "int", (1, 500)),
        ]),
        ("Описание клипов", [
            ("whisper", "Распознавать речь стримера", "Работает на этом компьютере, бесплатно. В описании клипа появится "
             "фраза, которую сказал стример. При первом клипе скачает модель (около 500 МБ).", "bool", None),
            ("whisper_model", "Модель распознавания", "small: хороший баланс. medium точнее, но медленнее и тяжелее.",
             "choice", [("base", "base (быстрая)"), ("small", "small"), ("medium", "medium (точная)")]),
            ("ai_enabled", "Описание и оценка от ИИ", "Claude читает расшифровку и чат, пишет заголовок и описание, "
             "уточняет границы клипа. Один короткий запрос на клип, доли цента.", "bool", None),
            ("ai_api_key", "Ключ Anthropic API", "Берётся на console.anthropic.com. Хранится только на этом компьютере.", "secret", None),
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
            elif t == "update":
                self.update_label.setText(f"Вышла новая версия {ev['version']}")
                self.update_btn.clicked.connect(lambda _=False, u=ev["url"]: webbrowser.open(u))
                self.update_bar.show()
        if not self.isVisible():
            return
        self.refresh_streams()
        if clips_changed and self.stack.currentIndex() == 1:
            self.refresh_clips()
        tg = self.sup.tg
        q = tg.queue.counts().get("pending", 0) if self.sup.cfg.get("tg_enabled") else 0
        self.tg_chip.setText(tg.status_text + (f" · в очереди {q}" if q else ""))
        restyle(self.tg_chip, {"ok": "chip", "off": "chipGray", "connecting": "chipGray"}.get(tg.status, "chipWarn"))
        lines = [f"Telegram: {tg.status_text}" + (f", в очереди {q}" if q else "")]
        if tg.progress:
            lines.append("Отправка " + tg.progress[-28:])
        if self.sup.editing:
            lines.append("Монтирую клип…")
        lines.append(f"Свободно на диске: {self.sup.free_gb:.0f} ГБ")
        if self.sup.cpu:
            lines.append(f"Процессор {self.sup.cpu:.0f}% · память {self.sup.ram:.0f}%")
        self.side_status.setText("\n".join(lines))

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
        self.sup.shutdown()
        e.accept()
        QApplication.quit()


def main(argv: list[str]) -> int:
    util.setup_logging("app")
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    T.load_fonts()
    app.setStyleSheet(T.stylesheet(str(config.data_dir())))
    if not system.single_instance():
        QMessageBox.information(None, APP_NAME, "Программа уже запущена. Ищи её значок возле часов.")
        return 0
    sup = Supervisor()
    sup.start()
    win = MainWindow(sup)
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
