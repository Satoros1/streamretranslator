"""Точка входа. Без аргументов открывает окно; с аргументами работает как служебный процесс."""
from __future__ import annotations

import sys


def selftest() -> int:
    """Проверка сборки: всё ли нужное попало в программу. Запускается в GitHub Actions на Windows."""
    import importlib
    from . import util
    ok = True
    for mod in ("streamlink", "streamlink.plugins.twitch", "telethon", "anthropic", "pydantic", "psutil",
                "requests", "sqlite3", "ssl", "PySide6.QtWidgets", "PySide6.QtSvg", "faster_whisper", "cryptg"):
        try:
            importlib.import_module(mod)
            print(f"OK   {mod}")
        except Exception as e:
            optional = mod in ("faster_whisper", "cryptg")
            print(f"{'WARN' if optional else 'FAIL'} {mod}: {e}")
            ok = ok and optional
    for tool in ("ffmpeg", "ffprobe"):
        try:
            r = util.run([util.find_tool(tool), "-version"], timeout=30)
            print(f"OK   {tool}: {r.stdout.decode(errors='replace').splitlines()[0]}")
        except Exception as e:
            print(f"FAIL {tool}: {e}")
            ok = False
    print("SELFTEST", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


def cli(argv: list[str]) -> int:
    cmd = argv[0] if argv else "gui"
    # В Windows вывод по умолчанию не в UTF-8, и русские буквы в нём роняли бы служебный процесс
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if cmd == "worker":
        from . import worker
        return worker.main(argv[1])
    if cmd == "edit":
        from . import editor
        return editor.main(argv[1])
    if cmd == "selftest":
        return selftest()
    if cmd == "sl-version":
        from . import updater
        updater.activate_streamlink()
        import streamlink
        print(streamlink.__version__)
        return 0
    if cmd == "sl-check":
        from . import updater
        return updater.streamlink_check(argv[1])
    if cmd == "headless":
        from . import headless
        return headless.main(argv[1:])
    from . import gui
    return gui.main(argv)


def main() -> None:
    sys.exit(cli(sys.argv[1:]))


if __name__ == "__main__":
    main()
