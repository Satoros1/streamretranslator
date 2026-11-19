"""Windows-специфика: не давать компьютеру уснуть, автозапуск, один экземпляр программы."""
from __future__ import annotations

import os
import sys

from . import APP_NAME, config, util

_mutex = None
_lockfile = None


def prevent_sleep(on: bool) -> None:
    """Пока идёт запись, Windows не уходит в сон (экран гаснуть может)."""
    if sys.platform != "win32":
        return
    import ctypes
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))


def set_autostart(on: bool) -> bool:
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                            winreg.KEY_SET_VALUE) as k:
            if on:
                if util.frozen():
                    cmd = f'"{sys.executable}" --minimized'
                else:
                    cmd = f'"{sys.executable}" -m streamclipper --minimized'
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def single_instance() -> bool:
    """True, если мы первый запущенный экземпляр. Два экземпляра писали бы одни и те же стримы."""
    global _mutex, _lockfile
    if sys.platform == "win32":
        import ctypes
        _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "StreamClipper_single_instance")
        return ctypes.windll.kernel32.GetLastError() != 183      # ERROR_ALREADY_EXISTS
    try:
        import fcntl
        _lockfile = open(config.data_dir() / "lock", "w")
        fcntl.flock(_lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def open_folder(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        import subprocess
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
