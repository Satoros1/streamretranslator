"""Проверка кнопки «Обновить» на настоящей Windows (GitHub Actions, после сборки установщика).

1. Ставит программу только что собранным установщиком, как это сделал бы человек.
2. Поднимает на этом же компьютере «GitHub», где лежит «новая версия» v99.0.0 (тот же установщик).
3. Запускает поставленную программу. Она должна сама найти новую версию, скачать установщик, проверить его,
   запустить и закрыться. Установщик должен дождаться этого, поставить программу заново и открыть её.
4. Проверяет, что старая программа закрылась, новая работает и написала в журнал «Программа обновлена».

Чтобы убедиться, что установщик действительно переписал папку программы, перед обновлением из неё убирается
один файл: после обновления он должен вернуться.
"""
import glob
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import psutil

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DONE = "Программа обновлена до версии"       # эту строку программа пишет в журнал после обновления
WAIT = 420                                   # секунд на всё обновление
T0 = time.time()


def say(text: str) -> None:
    print("%6.1f  %s" % (time.time() - T0, text), flush=True)


def ours(name: str, flag: str = "") -> list[psutil.Process]:
    out = []
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if (p.info["name"] or "").lower() == name.lower() and (not flag or flag in (p.info["cmdline"] or [])):
                out.append(p)
        except (psutil.Error, OSError):
            pass
    return out


def serve(setup: str) -> tuple[http.server.ThreadingHTTPServer, str, list]:
    size = os.path.getsize(setup)
    sha = hashlib.sha256()
    with open(setup, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha.update(chunk)
    hits: list[tuple[float, str]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append((time.time() - T0, self.path))
            base = "http://127.0.0.1:%d/" % self.server.server_address[1]
            if self.path == "/latest.json":
                body = json.dumps({"tag_name": "v99.0.0", "html_url": base, "assets": [
                    {"name": "StreamClipper-Setup.exe", "browser_download_url": base + "StreamClipper-Setup.exe",
                     "size": size, "digest": "sha256:" + sha.hexdigest()}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/StreamClipper-Setup.exe":
                self.send_response(200)
                self.send_header("Content-Length", str(size))
                self.end_headers()
                with open(setup, "rb") as f:
                    shutil.copyfileobj(f, self.wfile, 1 << 20)
                hits.append((time.time() - T0, "установщик отдан целиком"))
            else:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/latest.json" % srv.server_address[1], hits


def tail(path: str, n: int = 60) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError as e:
        return "(нет файла: %s)" % e


def main() -> int:
    setup = os.path.abspath(sys.argv[1])
    app_dir = os.path.join(os.environ["LOCALAPPDATA"], "Programs", "StreamClipper")
    app = os.path.join(app_dir, "StreamClipper.exe")
    canary = os.path.join(app_dir, "bin", "ffprobe.exe")
    home = tempfile.mkdtemp(prefix="sc_update_")
    log = os.path.join(home, "logs", "app.log")

    say("ставлю программу установщиком: %s (%.0f МБ)" % (setup, os.path.getsize(setup) / 1e6))
    r = subprocess.run([setup, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=900)
    if r.returncode or not os.path.exists(app) or not glob.glob(os.path.join(app_dir, "unins*.exe")):
        say("установщик не поставил программу: код %s" % r.returncode)
        print("UPDATE SMOKE: FAILED")
        return 1
    say("поставлено в %s" % app_dir)
    os.remove(canary)                                    # после обновления этот файл должен вернуться

    srv, api, hits = serve(setup)
    env = dict(os.environ, STREAMCLIPPER_HOME=home, STREAMCLIPPER_UPDATE_TEST=api)
    old = subprocess.Popen([app], env=env)
    say("программа запущена (процесс %d), жду: она сама найдёт v99.0.0 и обновится" % old.pid)

    closed = reopened = written = restored = False
    end = time.time() + WAIT
    while time.time() < end and not (closed and reopened and written and restored):
        time.sleep(1)
        if not closed and old.poll() is not None:
            closed = True
            say("старая программа закрылась сама (код %s)" % old.returncode)
        if closed and not reopened and ours("StreamClipper.exe", "--updated"):
            reopened = True
            say("установщик открыл программу заново")
        if not written and DONE in tail(log, 400):
            written = True
            say("в журнале: «%s…»" % DONE)
        if closed and not restored and os.path.exists(canary):
            restored = True
            say("установщик переписал папку программы (убранный файл вернулся)")

    print("\n--- что спрашивали у «GitHub» ---")
    for t, path in hits:
        print("%6.1f  %s" % (t, path))
    print("\n--- журнал программы ---")
    print(tail(log, 80))
    ok = closed and reopened and written and restored
    if not ok:
        say("не дождался: закрылась=%s, открылась заново=%s, строка в журнале=%s, папка переписана=%s"
            % (closed, reopened, written, restored))
        logs = sorted(glob.glob(os.path.join(tempfile.gettempdir(), "Setup Log*.txt")), key=os.path.getmtime)
        if logs:
            print("\n--- журнал установщика: %s ---" % logs[-1])
            print(tail(logs[-1], 120))
        print("\n--- процессы ---")
        for p in psutil.process_iter(["name", "cmdline"]):
            if any(k in (p.info["name"] or "").lower() for k in ("streamclipper", "sc-worker", "setup", "is-")):
                print(p.pid, p.info["name"], p.info["cmdline"])

    srv.shutdown()
    for name in ("StreamClipper.exe", "sc-worker.exe"):  # за собой убираем: программа осталась бы работать
        for p in ours(name):
            try:
                p.kill()
            except (psutil.Error, OSError):
                pass
    if old.poll() is None:
        old.kill()
    print("UPDATE SMOKE:", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
