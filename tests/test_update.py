"""Кнопка «Обновить»: программа сама находит новую версию, скачивает установщик, проверяет его и запускает."""
import hashlib
import http.server
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from streamclipper import __version__, config, updater, util
from streamclipper.updater import Update, UpdateError

REPO = "https://github.com/Satoros1/streamretranslator"
SETUP = b"MZ" + bytes(range(256)) * 2000              # «установщик» на полмегабайта


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STREAMCLIPPER_HOME", str(tmp_path / "home"))
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    return tmp_path / "home"


def _release(tag="v99.0.0", link=None, body=SETUP, **asset):
    a = {"name": "StreamClipper-Setup.exe", "size": len(body), "digest": "sha256:" + hashlib.sha256(body).hexdigest(),
         "browser_download_url": link or f"{REPO}/releases/download/{tag}/StreamClipper-Setup.exe"}
    a.update(asset)
    return {"tag_name": tag, "html_url": f"{REPO}/releases/tag/{tag}",
            "assets": [{"name": "StreamClipper-portable.zip", "browser_download_url": "x", "size": 1}, a]}


class _GitHub:
    """«GitHub» на этом же компьютере: ответ о последнем выпуске и сам установщик."""

    def __init__(self, body=SETUP, status=200, cut=0, **asset):
        self.hits: list[str] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits.append(self.path)
                if self.path == "/latest.json":
                    data = json.dumps(outer.release).encode()
                    self.send_response(status)
                elif self.path == "/StreamClipper-Setup.exe":
                    data = body
                    self.send_response(200)
                else:
                    data = b""
                    self.send_response(404)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data[:len(data) - cut] if self.path.endswith(".exe") else data)

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = "http://127.0.0.1:%d/" % self.srv.server_address[1]
        self.api = self.base + "latest.json"
        self.release = _release(link=self.base + "StreamClipper-Setup.exe", body=body, **asset)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def update(self) -> Update:
        return updater.parse_release(self.release, self.base)

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def github():
    made = []

    def make(**kw):
        made.append(_GitHub(**kw))
        return made[-1]
    yield make
    for g in made:
        g.close()


# --- что считается новой версией ---------------------------------------------------------------------------
def test_newer_release_with_installer_is_an_update():
    upd = updater.parse_release(_release("v99.0.0"))
    assert upd == Update("v99.0.0", f"{REPO}/releases/tag/v99.0.0",
                         f"{REPO}/releases/download/v99.0.0/StreamClipper-Setup.exe", len(SETUP),
                         hashlib.sha256(SETUP).hexdigest())


def test_same_or_older_release_is_not_an_update():
    assert updater.parse_release(_release("v" + __version__)) is None
    assert updater.parse_release(_release("v0.0.1")) is None
    assert updater.parse_release({}) is None and updater.parse_release({"tag_name": ""}) is None


def test_installer_is_taken_only_from_our_releases():
    """Адрес установщика приходит из ответа сервера. Если он ведёт не в выпуски программы, качать его нельзя:
    тогда остаётся только ссылка на страницу, которую человек откроет сам."""
    upd = updater.parse_release(_release(link="https://evil.example/StreamClipper-Setup.exe"))
    assert upd.version == "v99.0.0" and upd.url == "" and upd.page.startswith(REPO)
    other = updater.parse_release(_release(link="https://github.com/someone/else/releases/download/v9/StreamClipper-Setup.exe"))
    assert other.url == ""
    page = dict(_release(), html_url="https://evil.example/page")
    assert updater.parse_release(page).page == updater.PAGE          # и чужую страницу в браузере не откроем
    assert updater.parse_release(dict(_release(), assets=[])).url == ""           # выпуск без установщика
    assert updater.parse_release(_release(digest="")).sha256 == ""                # GitHub не назвал контрольную сумму


def test_check_talks_to_the_test_server_only_on_this_computer(home, github, monkeypatch):
    g = github()
    monkeypatch.setenv("STREAMCLIPPER_UPDATE_TEST", g.api)
    upd = updater.fetch_update()
    assert upd.version == "v99.0.0" and upd.url == g.base + "StreamClipper-Setup.exe" and upd.size == len(SETUP)
    assert updater.check_app_update() == upd

    monkeypatch.setenv("STREAMCLIPPER_UPDATE_TEST", "http://evil.example/latest.json")
    assert updater.test_api() == ""                                  # чужой адрес проверочным не станет


def test_check_says_what_went_wrong(home, github, monkeypatch):
    g = github(status=403)
    monkeypatch.setenv("STREAMCLIPPER_UPDATE_TEST", g.api)
    with pytest.raises(UpdateError, match="просит подождать"):
        updater.fetch_update()
    assert updater.check_app_update() is None                        # фоновая проверка молча ждёт следующего раза

    dead = github()
    dead.close()
    monkeypatch.setenv("STREAMCLIPPER_UPDATE_TEST", dead.api)
    with pytest.raises(UpdateError, match="нет связи"):
        updater.fetch_update()

    g500 = github(status=500)
    monkeypatch.setenv("STREAMCLIPPER_UPDATE_TEST", g500.api)
    with pytest.raises(UpdateError, match="ошибкой 500"):
        updater.fetch_update()


# --- скачивание установщика ---------------------------------------------------------------------------------
def test_download_checks_the_file_and_shows_progress(home, github):
    g = github()
    folder = config.subdir("updates")
    (folder / "StreamClipper-Setup-v0.0.9.exe").write_bytes(b"old")          # остался с прошлого раза
    seen = []
    path = updater.download(g.update(), seen.append)
    assert path == folder / "StreamClipper-Setup-v99.0.0.exe" and path.read_bytes() == SETUP
    assert seen[-1] == 100 and seen == sorted(seen) and len(seen) > 1
    assert [p.name for p in folder.iterdir()] == ["StreamClipper-Setup-v99.0.0.exe"]

    n = len(g.hits)
    assert updater.download(g.update()) == path                      # уже скачан и цел: второй раз не качаем
    assert len(g.hits) == n


@pytest.mark.parametrize("server, asset, why", [
    (dict(cut=1000), {}, "связь оборвалась"),                        # сервер оборвал передачу на середине
    ({}, {"size": len(SETUP) + 1000}, "не целиком"),                 # файл короче, чем обещал GitHub
    ({}, {"digest": "sha256:" + "0" * 64}, "повредился"),            # размер тот, содержимое не то
    (dict(body=b"<html>" + SETUP[6:]), {}, "не установщик"),         # вместо программы пришла страница
])
def test_broken_download_is_never_kept(home, github, server, asset, why):
    g = github(**server, **asset)
    with pytest.raises(UpdateError, match=why):
        updater.download(g.update())
    assert list(config.subdir("updates").iterdir()) == []            # ни установщика, ни недокачанного куска


def test_download_refuses_without_installer_or_disk_space(home, github, monkeypatch):
    with pytest.raises(UpdateError, match="нет установщика"):
        updater.download(Update("v99.0.0", updater.PAGE, "", 0, ""))
    g = github()
    monkeypatch.setattr(util, "free_gb", lambda p: 0.4)
    with pytest.raises(UpdateError, match="свободного места"):
        updater.download(g.update())
    assert g.hits == []

    dead = github()
    upd = dead.update()
    dead.close()
    monkeypatch.setattr(util, "free_gb", lambda p: 50.0)
    with pytest.raises(UpdateError, match="связь оборвалась"):
        updater.download(upd)


# --- запуск установщика ---------------------------------------------------------------------------------------
def test_installer_is_started_silently_and_told_to_reopen_the_program(home, monkeypatch):
    calls = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))
    setup = config.subdir("updates") / "StreamClipper-Setup-v99.0.0.exe"
    updater.launch_installer(setup, "v99.0.0")
    (cmd, kw), = calls
    assert cmd == [str(setup), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOCANCEL", "/NORESTARTAPPLICATIONS",
                   "/UPDATE=1"]
    assert kw["cwd"] == str(setup.parent) and kw["stdin"] == subprocess.DEVNULL
    note = json.loads((home / "update_pending.json").read_text(encoding="utf-8"))
    assert note["to"] == "v99.0.0" and note["from"] == __version__


def test_installer_that_does_not_start_is_reported(home, monkeypatch):
    def boom(cmd, **kw):
        raise OSError("отказано в доступе")
    monkeypatch.setattr(updater.subprocess, "Popen", boom)
    with pytest.raises(UpdateError, match="не запустился"):
        updater.launch_installer(home / "x.exe", "v99.0.0")
    assert not (home / "update_pending.json").exists()               # обновление не началось: напоминать не о чем


def test_installer_leaves_the_windows_job_when_allowed(home, monkeypatch):
    """В Windows установщик должен пережить программу. Если выйти из «задания» нельзя, запускаем как обычно."""
    flags = []

    def popen(cmd, creationflags=0, **kw):
        flags.append(creationflags)
        if creationflags & 0x01000000:
            raise OSError("breakaway запрещён")
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater.subprocess, "Popen", popen)
    updater.launch_installer(home / "x.exe", "v99.0.0")
    assert flags == [0x00000008 | 0x00000200 | 0x01000000, 0x00000008 | 0x00000200]


def test_installer_script_matches_the_program():
    """Установщик и программа договариваются через ключ /UPDATE=1 и имя «замка» одного экземпляра.
    Если поменять их только с одной стороны, обновление молча перестанет работать."""
    root = Path(__file__).resolve().parent.parent
    iss = (root / "packaging" / "installer.iss").read_text(encoding="utf-8")
    assert "/UPDATE=1" in updater.install_cmd("x") and "{param:UPDATE|0}" in iss
    assert 'Parameters: "--updated"; Flags: nowait; Check: IsUpdate' in iss
    mutex = "StreamClipper_single_instance"
    assert f"CheckForMutexes('{mutex}')" in iss
    assert f'"{mutex}"' in (root / "streamclipper" / "system.py").read_text(encoding="utf-8")
    assert "CloseApplications=force" in iss                          # кто не закрылся сам, того закроет установщик


# --- что программа говорит после обновления -------------------------------------------------------------------
def test_after_update_the_program_says_so_and_cleans_up(home):
    folder = config.subdir("updates")
    (folder / "StreamClipper-Setup-v99.0.0.exe").write_bytes(SETUP)
    (home / "update_pending.json").write_text(json.dumps({"to": "v99.0.0", "at": time.time()}), encoding="utf-8")
    assert updater.finish_update(["--updated"]) == ("ok", __version__)
    assert list(folder.iterdir()) == [] and not (home / "update_pending.json").exists()
    assert updater.finish_update([]) == ("", "")                     # обычный запуск: обновления не было


def test_failed_update_is_noticed_on_next_start(home):
    note = home / "update_pending.json"
    home.mkdir(parents=True, exist_ok=True)
    note.write_text(json.dumps({"to": "v99.0.0", "at": time.time()}), encoding="utf-8")
    assert updater.finish_update([]) == ("failed", "v99.0.0")        # версия прежняя: установщик не справился
    assert not note.exists() and updater.finish_update([]) == ("", "")           # напоминаем один раз

    note.write_text(json.dumps({"to": "v" + __version__, "at": time.time()}), encoding="utf-8")
    assert updater.finish_update([]) == ("ok", __version__)          # установщик справился, но открыли программу руками

    note.write_text(json.dumps({"to": "v99.0.0", "at": time.time() - 30 * 86400}), encoding="utf-8")
    assert updater.finish_update([]) == ("", "")                     # месяц спустя об этом уже не вспоминаем
    note.write_text("не json", encoding="utf-8")
    assert updater.finish_update([]) == ("", "") and not note.exists()


def test_only_installed_copy_updates_itself(tmp_path, monkeypatch):
    assert updater.can_self_update() is False                        # запуск из исходников
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(util, "frozen", lambda: True)
    monkeypatch.setattr(util, "app_dir", lambda: tmp_path)
    assert updater.can_self_update() is False                        # версия из архива: установщика рядом нет
    (tmp_path / "unins000.exe").write_bytes(b"MZ")
    assert updater.can_self_update() is True


# --- программа: проверка, скачивание, прерванный монтаж -------------------------------------------------------
def _events(sup, until, timeout=10.0):
    out, end = [], time.time() + timeout
    while time.time() < end:
        try:
            ev = sup.events.get(timeout=0.1)
        except Exception:
            continue
        out.append(ev)
        if ev.get("t") in until:
            return out
    raise AssertionError(f"не дождался {until}: {out}")


def test_check_button_reports_every_outcome(home, monkeypatch):
    from streamclipper.supervisor import Supervisor
    sup = Supervisor()
    upd = Update("v99.0.0", updater.PAGE, REPO + "/releases/download/v99.0.0/StreamClipper-Setup.exe", 5, "")

    monkeypatch.setattr(updater, "fetch_update", lambda: None)
    sup.check_update_now()
    assert _events(sup, {"no_update"})[-1] == {"t": "no_update"}

    def offline():
        raise UpdateError("нет связи с GitHub")
    monkeypatch.setattr(updater, "fetch_update", offline)
    sup.check_update_now()
    assert _events(sup, {"update_error"})[-1] == {"t": "update_error", "error": "нет связи с GitHub"}

    monkeypatch.setattr(updater, "fetch_update", lambda: upd)
    sup.check_update_now()
    ev = _events(sup, {"update"})[-1]
    assert ev == {"t": "update", "version": "v99.0.0", "url": updater.PAGE, "auto": False}   # не Windows: только ссылка
    sup.self_update = True
    sup.check_update_now()
    assert _events(sup, {"update"})[-1]["auto"] is True and sup.update == upd
    sup._found_update(upd._replace(url=""))
    assert _events(sup, {"update"})[-1]["auto"] is False             # в выпуске нет установщика: тоже только ссылка


def test_download_button_ends_with_ready_or_failed(home, github, monkeypatch):
    from streamclipper.supervisor import Supervisor
    sup = Supervisor()
    assert sup.download_update() is False                            # обновления нет: качать нечего
    g = github()
    sup.update = g.update()
    assert sup.download_update() is True
    evs = _events(sup, {"update_ready", "update_failed"})
    ready = evs[-1]
    assert ready["t"] == "update_ready" and ready["version"] == "v99.0.0"
    assert Path(ready["path"]).read_bytes() == SETUP
    assert [e["pct"] for e in evs if e["t"] == "update_progress"][-1] == 100
    assert any(e["t"] == "log" and "Скачиваю обновление v99.0.0" in e["text"] for e in evs)

    bad = github(cut=500)
    sup.update = bad.update()._replace(version="v99.0.1")
    for _ in range(50):
        if sup.download_update():
            break
        time.sleep(0.05)                                             # прошлое скачивание ещё закрывается
    evs = _events(sup, {"update_ready", "update_failed"})
    assert evs[-1]["t"] == "update_failed" and "связь оборвалась" in evs[-1]["error"] and evs[-1]["url"]
    assert any(e["t"] == "log" and e["level"] == "error" and "не скачалось" in e["text"] for e in evs)


def test_second_click_while_downloading_is_ignored(home, monkeypatch):
    from streamclipper.supervisor import Supervisor
    sup = Supervisor()
    sup.update = Update("v99.0.0", updater.PAGE, "http://127.0.0.1:9/x.exe", 5, "")
    gate = threading.Event()

    def slow(upd, progress=None):
        gate.wait(5)
        raise UpdateError("нет связи")
    monkeypatch.setattr(updater, "download", slow)
    assert sup.download_update() is True
    assert sup.download_update() is False
    gate.set()
    assert _events(sup, {"update_failed"})[-1]["error"] == "нет связи"


def test_edit_in_progress_is_stopped_for_update_and_kept(home, monkeypatch):
    """Монтаж клипа держит файлы программы, и установщик не смог бы их заменить. Перед обновлением он
    прерывается вместе со своим ffmpeg, а заготовка остаётся: клип будет смонтирован после запуска."""
    from streamclipper.supervisor import Supervisor
    sup = Supervisor()
    job = config.subdir("candidates") / "chan_1_2_3.json"
    job.write_text("{}", encoding="utf-8")
    child_pid = home / "child.pid"
    code = ("import subprocess, sys, time; p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
            f"open(r'{child_pid}', 'w').write(str(p.pid)); time.sleep(120)")
    monkeypatch.setattr(util, "worker_cmd", lambda *a: [sys.executable, "-c", code])
    t = threading.Thread(target=sup._edit_one, args=(str(job), 80, False))
    t.start()
    for _ in range(100):
        if child_pid.exists() and child_pid.read_text():
            break
        time.sleep(0.1)
    pid = int(child_pid.read_text())
    started = time.time()
    sup.shutdown(for_update=True)
    t.join(10)
    assert not t.is_alive() and time.time() - started < 15
    assert job.exists()                                              # заготовка цела
    logs = []
    while not sup.events.empty():
        logs.append(sup.events.get_nowait())
    assert not any("Монтаж не удался" in e.get("text", "") or "не получился" in e.get("text", "") for e in logs)
    import psutil
    for _ in range(50):
        if not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
            break
        time.sleep(0.1)
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE    # и его ffmpeg тоже


def test_plain_shutdown_lets_the_edit_finish(home, monkeypatch):
    from streamclipper.supervisor import Supervisor
    sup = Supervisor()
    killed = []
    monkeypatch.setattr(sup, "kill_edit", lambda: killed.append(1))
    sup.shutdown()
    assert killed == []
    sup.shutdown(for_update=True)
    assert killed == [1]


# --- окно ---------------------------------------------------------------------------------------------------
@pytest.fixture
def window(home, monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from streamclipper import gui
    from streamclipper.supervisor import Supervisor
    sup = Supervisor()
    win = gui.MainWindow(sup)
    opened = []
    monkeypatch.setattr(gui.webbrowser, "open", opened.append)
    win.opened = opened
    win.show()
    app.processEvents()

    def feed(**ev):
        sup.events.put(ev)
        win.tick()
        app.processEvents()
    win.feed = feed
    yield win
    win.really_quit = True
    win.close()
    sup.shutdown()


def test_bar_offers_manual_download_when_program_cannot_update_itself(window):
    win = window
    assert win.update_bar.isHidden()
    win.feed(t="update", version="v99.0.0", url=REPO + "/releases/tag/v99.0.0", auto=False)
    assert win.update_bar.isVisible() and win.update_label.text() == "Вышла новая версия v99.0.0"
    assert win.update_btn.text() == "Скачать" and win.update_alt.isHidden()
    win.update_btn.click()
    assert win.opened == [REPO + "/releases/tag/v99.0.0"]


def test_update_button_downloads_installs_and_closes_the_program(window, monkeypatch):
    from streamclipper import gui
    win = window
    asked, started, launched, shut = [], [], [], []
    monkeypatch.setattr(win, "confirm_update", lambda: asked.append(1) or answer[0])
    monkeypatch.setattr(win.sup, "download_update", lambda: started.append(1) or True)
    monkeypatch.setattr(updater, "launch_installer", lambda path, ver: launched.append((path, ver)))
    monkeypatch.setattr(win.sup, "shutdown", lambda for_update=False: shut.append(for_update))
    monkeypatch.setattr(gui.QTimer, "singleShot", staticmethod(lambda ms, fn: fn()))
    answer = [False]

    win.feed(t="update", version="v99.0.0", url=REPO, auto=True)
    assert win.update_btn.text() == "Обновить"
    win.update_btn.click()
    assert asked == [1] and started == []                            # «Позже»: ничего не происходит
    answer[0] = True
    win.update_btn.click()
    assert started == [1] and not win.update_btn.isEnabled()
    assert win.update_label.text() == "Скачиваю обновление v99.0.0: 0%"
    win.feed(t="update_progress", pct=45)
    assert win.update_label.text() == "Скачиваю обновление v99.0.0: 45%"
    win.feed(t="update", version="v99.0.0", url=REPO, auto=True)     # фоновая проверка посреди скачивания
    assert win.update_label.text() == "Скачиваю обновление v99.0.0: 45%" and not win.update_btn.isEnabled()

    win.feed(t="update_ready", path="C:/x/Setup.exe", version="v99.0.0", url=REPO)
    assert launched == [("C:/x/Setup.exe", "v99.0.0")]
    assert "Ставлю версию v99.0.0" in win.update_label.text()
    assert shut == [True] and win.for_update and win.opened == []    # программа закрылась ради установщика


def test_failed_download_can_be_retried_or_done_by_hand(window, monkeypatch):
    win = window
    started = []
    monkeypatch.setattr(win, "confirm_update", lambda: True)
    monkeypatch.setattr(win.sup, "download_update", lambda: started.append(1) or True)
    win.feed(t="update", version="v99.0.0", url=REPO + "/releases/tag/v99.0.0", auto=True)
    win.update_btn.click()
    win.feed(t="update_failed", error="связь оборвалась во время скачивания", url=REPO)
    assert "не скачалось: связь оборвалась" in win.update_label.text()
    assert win.update_btn.text() == "Повторить" and win.update_btn.isEnabled() and win.update_alt.isVisible()
    win.update_alt.click()
    assert win.opened == [REPO + "/releases/tag/v99.0.0"]
    win.update_btn.click()
    assert started == [1, 1] and win.update_alt.isHidden()


def test_installer_that_does_not_start_keeps_the_program_running(window, monkeypatch):
    win = window
    shut = []

    def boom(path, ver):
        raise UpdateError("установщик не запустился (отказано в доступе)")
    monkeypatch.setattr(updater, "launch_installer", boom)
    monkeypatch.setattr(win.sup, "shutdown", lambda for_update=False: shut.append(for_update))
    monkeypatch.setattr(win, "confirm_update", lambda: True)
    monkeypatch.setattr(win.sup, "download_update", lambda: True)
    win.feed(t="update", version="v99.0.0", url=REPO, auto=True)
    win.update_btn.click()
    win.feed(t="update_ready", path="x.exe", version="v99.0.0", url=REPO)
    assert shut == [] and not win.for_update
    assert "не началось: установщик не запустился" in win.update_label.text() and win.update_alt.isVisible()


def test_check_updates_button_in_settings(window, monkeypatch):
    from PySide6.QtWidgets import QLabel
    win = window
    asked = []
    monkeypatch.setattr(win.sup, "check_update_now", lambda: asked.append(1))
    win.go(5)
    shown = " ".join(w.text() for w in win.stack.currentWidget().findChildren(QLabel))
    assert f"Установлена версия {__version__}" in shown and "кнопкой «Скачать»" in shown
    win.check_btn.click()
    assert asked == [1] and not win.check_btn.isEnabled() and win.check_label.text() == "Проверяю…"
    win.feed(t="no_update")
    assert win.check_btn.isEnabled() and win.check_label.text() == "У тебя последняя версия."
    win.check_btn.click()
    win.feed(t="update_error", error="нет связи с GitHub")
    assert win.check_label.text() == "Не получилось проверить: нет связи с GitHub."
    win.check_btn.click()
    win.feed(t="update", version="v99.0.0", url=REPO, auto=False)
    assert "Вышла новая версия v99.0.0" in win.check_label.text() and win.update_bar.isVisible()


def test_after_failed_install_the_bar_offers_only_manual_download(window):
    win = window
    win.update_manual = True                                         # так окно стартует после неудавшейся установки
    win.feed(t="update", version="v99.0.0", url=REPO, auto=True)
    assert win.update_btn.text() == "Скачать"
    win.update_btn.click()
    assert win.opened == [REPO]


def test_build_check_updates_without_a_question(window, monkeypatch):
    win = window
    started = []
    monkeypatch.setattr(win, "confirm_update", lambda: pytest.fail("в проверке сборки вопросов быть не должно"))
    monkeypatch.setattr(win.sup, "download_update", lambda: started.append(1) or True)
    win.update_unattended = True
    win.feed(t="update", version="v99.0.0", url=REPO, auto=True)
    assert started == [1] and win.upd["state"] == "downloading"


def test_build_check_looks_for_the_line_the_program_writes():
    """После обновления программа пишет в журнал одну строку. По ней проверка сборки на Windows понимает,
    что новая версия открылась. Если поменять текст только в одном месте, проверка перестанет это видеть."""
    root = Path(__file__).resolve().parent.parent
    said = 'sup.say(f"Программа обновлена до версии {ver}")'
    assert said in (root / "streamclipper" / "gui.py").read_text(encoding="utf-8")
    smoke = (root / "packaging" / "update_smoke.py").read_text(encoding="utf-8")
    assert '"Программа обновлена до версии"' in smoke and "STREAMCLIPPER_UPDATE_TEST" in smoke
    assert "update_smoke.py" in (root / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")


# --- всё вместе: настоящее окно, настоящее скачивание, вместо установщика его подмена -------------------------
_RUN = """
import sys
from streamclipper import gui, updater
from streamclipper.supervisor import Supervisor
updater.can_self_update = lambda: True
updater.install_cmd = lambda path: [sys.executable, "-c",
    "import sys; open(sys.argv[1], 'w').write(' '.join(sys.argv[2:]))", {marker!r}, str(path), "/SILENT", "/UPDATE=1"]
Supervisor._update_loop = lambda self: self._found_update(updater.check_app_update())
sys.exit(gui.main({argv!r}))
"""


def _app(home, argv, marker, **env):
    full = dict(os.environ, STREAMCLIPPER_HOME=str(home), QT_QPA_PLATFORM="offscreen", NO_PROXY="127.0.0.1", **env)
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        full.pop(var, None)
    return subprocess.run([sys.executable, "-c", _RUN.format(marker=str(marker), argv=argv)], env=full,
                          cwd=str(Path(__file__).resolve().parent.parent), timeout=120,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def test_program_updates_itself_from_start_to_finish(home, github, tmp_path):
    """Программа запускается, сама находит новую версию, скачивает установщик, запускает его и закрывается.
    Следующий запуск (его делает установщик) сообщает в журнале, что программа обновлена."""
    pytest.importorskip("PySide6")
    g = github()
    marker = tmp_path / "installer_ran.txt"
    r = _app(home, [], marker, STREAMCLIPPER_UPDATE_TEST=g.api)
    assert r.returncode == 0, r.stdout.decode("utf-8", "replace")[-2000:]          # закрылась сама
    for _ in range(50):
        if marker.exists() and marker.read_text():
            break
        time.sleep(0.1)
    ran = marker.read_text()
    assert ran.endswith("StreamClipper-Setup-v99.0.0.exe /SILENT /UPDATE=1")       # запущен скачанный установщик
    assert Path(ran.split(" /SILENT")[0]).read_bytes() == SETUP
    assert g.hits == ["/latest.json", "/StreamClipper-Setup.exe"]
    log = (home / "logs" / "app.log").read_text(encoding="utf-8")
    assert "Скачиваю обновление v99.0.0" in log and "Ставлю версию v99.0.0" in log
    assert json.loads((home / "update_pending.json").read_text(encoding="utf-8"))["to"] == "v99.0.0"

    again = _app(home, ["--updated"], marker, STREAMCLIPPER_UPDATE_TEST=g.api,
                 STREAMCLIPPER_SCREENSHOT=str(tmp_path / "page"), STREAMCLIPPER_SCREENSHOT_MS="150")
    assert again.returncode == 0, again.stdout.decode("utf-8", "replace")[-2000:]
    log = (home / "logs" / "app.log").read_text(encoding="utf-8")
    assert f"Программа обновлена до версии {__version__}" in log
    assert not (home / "update_pending.json").exists()
    assert log.count("Скачиваю обновление") == 1                     # после обновления по второму кругу не идёт
    assert list(config.subdir("updates").iterdir()) == []            # установщик убран
