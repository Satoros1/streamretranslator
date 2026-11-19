"""Обновления.

Twitch иногда меняет что-то у себя, и запись ломается, пока не обновится библиотека streamlink.
Чтобы не ждать новой версии программы, свежий streamlink скачивается с pypi.org в папку данных,
проверяется отдельным процессом и только потом включается. Если проверка не прошла, остаётся встроенный.

Сама программа обновляется кнопкой «Обновить»: установщик новой версии скачивается из выпусков на GitHub,
проверяется (размер и контрольная сумма, которые называет GitHub) и запускается без вопросов. Программа
закрывается, установщик ждёт этого, ставит новую версию поверх старой и открывает её.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Callable, NamedTuple

from . import GITHUB_REPO, __version__, config, util

log = logging.getLogger("updater")

INSTALLER = "StreamClipper-Setup.exe"
API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
ASSETS = f"https://github.com/{GITHUB_REPO}/releases/download/"       # установщик берётся только отсюда
PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
NEED_GB = 1.5         # столько свободного места нужно на скачивание и установку
PENDING_DAYS = 7      # дольше о неудавшемся обновлении не напоминаем


class UpdateError(Exception):
    """Обновиться не вышло. Текст показывается человеку как есть."""


class Update(NamedTuple):
    version: str      # «v0.1.3»
    page: str         # страница выпуска: её можно открыть в браузере и скачать установщик руками
    url: str          # сам установщик; пусто, если в выпуске его нет
    size: int         # сколько он весит, байт
    sha256: str       # контрольная сумма от GitHub; пусто, если GitHub её не назвал


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


def test_api() -> str:
    """Только для проверки сборки: «GitHub» на этом же компьютере. Чужой адрес сюда подставить нельзя."""
    u = os.environ.get("STREAMCLIPPER_UPDATE_TEST", "")
    return u if u.startswith("http://127.0.0.1:") else ""


def parse_release(d: dict, assets_at: str = ASSETS) -> Update | None:
    """Ответ GitHub о последнем выпуске -> обновление, если тот новее этой программы."""
    tag = str(d.get("tag_name") or "")
    if not tag or _ver(tag) <= _ver(__version__):
        return None
    url, size, sha = "", 0, ""
    for a in d.get("assets") or []:
        link = str(a.get("browser_download_url") or "")
        if a.get("name") == INSTALLER and link.startswith(assets_at):
            url, size = link, int(a.get("size") or 0)
            digest = str(a.get("digest") or "")
            sha = digest[7:].lower() if digest.startswith("sha256:") else ""
    page = str(d.get("html_url") or "")
    return Update(tag, page if page.startswith(f"https://github.com/{GITHUB_REPO}/") else PAGE, url, size, sha)


def fetch_update() -> Update | None:
    """Обновление, если на GitHub вышла версия новее; None, если эта последняя. Нет связи: UpdateError."""
    import requests

    test = test_api()
    try:
        r = requests.get(test or API, timeout=15,
                         headers={"Accept": "application/vnd.github+json", "User-Agent": "StreamClipper"})
    except requests.RequestException as e:
        raise UpdateError("нет связи с GitHub") from e
    if r.status_code in (403, 429):
        raise UpdateError("GitHub просит подождать: слишком много запросов с этого адреса. Попробуй через час")
    if r.status_code != 200:
        raise UpdateError(f"GitHub ответил ошибкой {r.status_code}")
    try:
        d = r.json()
    except ValueError as e:
        raise UpdateError("GitHub ответил непонятно") from e
    if not isinstance(d, dict):
        raise UpdateError("GitHub ответил непонятно")
    return parse_release(d, test.rsplit("/", 1)[0] + "/" if test else ASSETS)


def check_app_update() -> Update | None:
    """То же для фоновой проверки: когда связи нет, просто молчит до следующего раза."""
    try:
        return fetch_update()
    except Exception as e:
        log.info("проверка новой версии: %s", e)
        return None


def can_self_update() -> bool:
    """Умеет ли эта копия программы ставить обновление сама. Только та, что поставлена установщиком:
    у версии из архива (portable) и у запуска из исходников своего установщика нет."""
    if sys.platform != "win32" or not util.frozen():
        return False
    try:
        return any(util.app_dir().glob("unins*.exe"))
    except OSError:
        return False


def _upd_files() -> Path:
    return config.subdir("updates")


def _pending() -> Path:
    return config.data_dir() / "update_pending.json"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _good(path: Path, upd: Update) -> str:
    """Пустая строка, если скачанный файл и есть установщик этой версии. Иначе что с ним не так."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            head = f.read(2)
    except OSError as e:
        return f"файл не читается ({e})"
    if upd.size and size != upd.size:
        return f"файл скачался не целиком ({size / 1e6:.0f} из {upd.size / 1e6:.0f} МБ)"
    if head != b"MZ":
        return "скачалось что-то другое, а не установщик"
    if upd.sha256 and _sha256(path) != upd.sha256:
        return "файл повредился при скачивании"
    return ""


def download(upd: Update, progress: Callable[[int], None] | None = None) -> Path:
    """Скачивает установщик новой версии и проверяет, что он дошёл целиком. Возвращает путь к файлу."""
    import requests

    if not upd.url:
        raise UpdateError("в этом выпуске нет установщика")
    folder = _upd_files()
    final = folder / f"StreamClipper-Setup-{''.join(c for c in upd.version if c.isalnum() or c in '.-_')}.exe"
    for old in folder.iterdir():                         # прошлые установщики больше не нужны
        if old != final:
            try:
                old.unlink()
            except OSError:
                pass
    if final.exists() and not _good(final, upd):
        return final                                     # уже скачан (например, установка в прошлый раз не началась)
    if util.free_gb(folder) < NEED_GB:
        raise UpdateError("на диске меньше полутора гигабайт свободного места")
    part = final.with_suffix(".part")
    got, shown = 0, -1
    try:
        with requests.get(upd.url, stream=True, timeout=(15, 60), headers={"User-Agent": "StreamClipper"}) as r:
            if r.status_code != 200:
                raise UpdateError(f"GitHub не отдал установщик (ошибка {r.status_code})")
            total = upd.size or int(r.headers.get("Content-Length") or 0)
            with open(part, "wb") as f:
                for chunk in r.iter_content(1 << 18):
                    f.write(chunk)
                    got += len(chunk)
                    pct = min(99, got * 100 // total) if total else 0
                    if progress and pct != shown:
                        shown = pct
                        progress(pct)
        bad = _good(part, upd)
        if bad:
            raise UpdateError(bad)
        os.replace(part, final)
    except requests.RequestException as e:
        raise UpdateError("связь оборвалась во время скачивания") from e
    except OSError as e:
        raise UpdateError(f"не получилось сохранить файл ({e})") from e
    finally:
        try:
            part.unlink()                                # недокачанное и непрошедшее проверку не храним
        except OSError:
            pass
    if progress:
        progress(100)
    log.info("установщик %s скачан: %s (%.0f МБ)", upd.version, final, got / 1e6)
    return final


def install_cmd(path: str | os.PathLike) -> list[str]:
    """Установщик без вопросов: показывает только полоску установки, ждёт, пока программа закроется,
    и сам открывает новую версию (это делает ключ /UPDATE=1, см. packaging/installer.iss)."""
    return [str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOCANCEL", "/NORESTARTAPPLICATIONS",
            "/UPDATE=1"]


def launch_installer(path: str | os.PathLike, version: str) -> None:
    """Запускает установщик отдельно от программы. После этого программа должна закрыться сама."""
    try:
        _pending().write_text(json.dumps({"to": version, "from": __version__, "at": time.time()}), encoding="utf-8")
    except OSError:
        pass
    kw: dict = dict(cwd=str(Path(path).parent), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, close_fds=True)
    detached = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0      # DETACHED_PROCESS | NEW_PROCESS_GROUP
    try:
        try:
            # Если программу держат в «задании» Windows, установщик должен выйти из него, иначе его закроют
            # вместе с ней. Не везде это разрешено: тогда запускаем как обычно.
            subprocess.Popen(install_cmd(path), creationflags=detached | (0x01000000 if detached else 0), **kw)
        except OSError:
            if not detached:
                raise
            subprocess.Popen(install_cmd(path), creationflags=detached, **kw)
    except OSError as e:
        try:
            _pending().unlink()
        except OSError:
            pass
        raise UpdateError(f"установщик не запустился ({e})") from e
    log.info("установщик %s запущен, программа закрывается", version)


def finish_update(argv: list[str]) -> tuple[str, str]:
    """При запуске: чем кончилось обновление, начатое в прошлый раз.
    ("ok", версия): программа обновлена. ("failed", версия): установщик не справился. ("", ""): обновления не было."""
    try:
        for old in _upd_files().iterdir():               # установщик своё дело сделал
            try:
                old.unlink()
            except OSError:
                pass
    except OSError:
        pass
    try:
        info = json.loads(_pending().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info = None
    try:
        _pending().unlink()
    except OSError:
        pass
    if "--updated" in argv:
        return "ok", __version__
    if not isinstance(info, dict) or not info.get("to"):
        return "", ""
    to = str(info["to"])
    if _ver(to) <= _ver(__version__):
        return "ok", __version__
    try:
        fresh = time.time() - float(info.get("at") or 0) < PENDING_DAYS * 86400
    except (TypeError, ValueError):
        fresh = False
    return ("failed", to) if fresh else ("", "")
