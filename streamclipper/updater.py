"""Обновления.

Twitch иногда меняет что-то у себя, и запись ломается, пока не обновится библиотека streamlink.
Чтобы не ждать новой версии программы, свежий streamlink скачивается с pypi.org в папку данных,
проверяется отдельным процессом и только потом включается. Если проверка не прошла, остаётся встроенный.
"""
from __future__ import annotations

import io
import logging
import shutil
import subprocess
import sys
import zipfile

from . import GITHUB_REPO, __version__, config, util

log = logging.getLogger("updater")


def _ver(s: str) -> tuple:
    out = []
    for part in s.strip().lstrip("v").split("."):
        num = "".join(c for c in part if c.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out)


def _upd_dir():
    return config.subdir("streamlink_update")


def activate_streamlink() -> None:
    """Вызывается в рабочем процессе до import streamlink."""
    try:
        cur = (_upd_dir() / "current.txt").read_text(encoding="utf-8").strip()
        if cur and (_upd_dir() / cur / "streamlink").is_dir():
            sys.path.insert(0, str(_upd_dir() / cur))
    except OSError:
        pass


def streamlink_check(path: str) -> int:
    """Отдельный процесс: работает ли скачанный streamlink вместе с тем, что есть в сборке."""
    sys.path.insert(0, path)
    import streamlink
    from streamlink import Streamlink
    import streamlink.plugins.twitch  # noqa: F401
    Streamlink().resolve_url("https://www.twitch.tv/twitch")
    import os
    if not os.path.abspath(streamlink.__file__).startswith(os.path.abspath(path)):
        print("NOT_OVERRIDDEN")
        return 2
    print("OK " + streamlink.__version__)
    return 0


def bundled_streamlink_version() -> str:
    try:
        r = util.run(util.worker_cmd("sl-version"), timeout=60)
        return r.stdout.decode().strip().split()[-1]
    except Exception:
        return "0"


def update_streamlink() -> str | None:
    """Возвращает новую версию, если обновились."""
    import requests

    info = requests.get("https://pypi.org/pypi/streamlink/json", timeout=20).json()
    latest = info["info"]["version"]
    have = bundled_streamlink_version()
    if _ver(latest) <= _ver(have):
        return None
    wheel = next((f for f in info["urls"] if f["filename"].endswith("py3-none-any.whl")), None)
    if not wheel:
        return None
    target = _upd_dir() / latest
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    data = requests.get(wheel["url"], timeout=120).content
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(target)
    r = subprocess.run(util.worker_cmd("sl-check", str(target)), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=120, creationflags=util.NO_WINDOW)
    if r.returncode != 0 or b"OK" not in r.stdout:
        log.warning("новый streamlink %s не подошёл к этой сборке, остаюсь на %s", latest, have)
        shutil.rmtree(target, ignore_errors=True)
        return None
    (_upd_dir() / "current.txt").write_text(latest, encoding="utf-8")
    for d in _upd_dir().iterdir():
        if d.is_dir() and d.name != latest:
            shutil.rmtree(d, ignore_errors=True)
    log.info("streamlink обновлён: %s -> %s", have, latest)
    return latest


def check_app_update() -> tuple[str, str] | None:
    """(версия, ссылка), если на GitHub вышла версия новее."""
    import requests

    try:
        r = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest", timeout=15,
                         headers={"Accept": "application/vnd.github+json", "User-Agent": "StreamClipper"})
        if r.status_code != 200:
            return None
        d = r.json()
        tag = d.get("tag_name", "")
        if tag and _ver(tag) > _ver(__version__):
            return tag, d.get("html_url", f"https://github.com/{GITHUB_REPO}/releases/latest")
    except Exception:
        pass
    return None
