"""Мелкие общие вещи: логи, поиск ffmpeg, запуск процессов без чёрных окон."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import config

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    if frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def setup_logging(name: str, console: bool = False) -> logging.Logger:
    root = logging.getLogger()
    if getattr(root, "_sc_ready", False):
        return logging.getLogger(name)
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    try:
        fh = RotatingFileHandler(config.subdir("logs") / f"{safe}.log", maxBytes=2_000_000,
                                 backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass
    if console and sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)
    for noisy in ("telethon", "urllib3", "httpx", "httpx2", "httpcore", "anthropic", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._sc_ready = True  # type: ignore[attr-defined]
    return logging.getLogger(name)


def find_tool(name: str) -> str:
    """ffmpeg/ffprobe: сначала рядом с программой, потом в PATH."""
    exe = name + (".exe" if sys.platform == "win32" else "")
    env = os.environ.get("STREAMCLIPPER_" + name.upper())
    if env and Path(env).exists():
        return env
    for base in (app_dir(), app_dir() / "bin", app_dir() / "_internal"):
        cand = base / exe
        if cand.exists():
            return str(cand)
    found = shutil.which(name)
    return found or exe


def run(cmd: list[str], timeout: float = 600, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=timeout, creationflags=NO_WINDOW, **kw)


def worker_cmd(*args: str) -> list[str]:
    """Команда запуска служебного процесса (запись стрима, монтаж клипа)."""
    if frozen():
        exe = app_dir() / ("sc-worker.exe" if sys.platform == "win32" else "sc-worker")
        return [str(exe), *args]
    return [sys.executable, "-m", "streamclipper", *args]


def free_gb(path: str | os.PathLike) -> float:
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        return shutil.disk_usage(p).free / 1e9
    except OSError:
        return 999.0


def dir_size(path: str | os.PathLike) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def fmt_dur(sec: float) -> str:
    sec = int(max(0, sec))
    return f"{sec // 60}:{sec % 60:02d}"
