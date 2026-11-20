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
        # Код ошибки нужно забирать тем же вызовом: отдельный GetLastError() через ctypes может вернуть чужое
        # значение, и тогда второй экземпляр программы решит, что он первый.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        ctypes.set_last_error(0)
        _mutex = kernel32.CreateMutexW(None, 0, "StreamClipper_single_instance")
        return ctypes.get_last_error() != 183                     # ERROR_ALREADY_EXISTS
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


def dark_title_bar(hwnd: int) -> None:
    """Тёмная полоса заголовка окна в Windows 10 и 11, в цвет программы. В других системах ничего не делает."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        dwm = ctypes.windll.dwmapi
        on = ctypes.c_int(1)
        for attr in (20, 19):               # DWMWA_USE_IMMERSIVE_DARK_MODE: 20 в новых Windows, 19 в старых
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), ctypes.sizeof(on)) == 0:
                break
        caption = ctypes.c_int(0x00100F0F)  # DWMWA_CAPTION_COLOR (Windows 11), цвет бокового меню #0F0F10
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))
    except Exception:
        pass
