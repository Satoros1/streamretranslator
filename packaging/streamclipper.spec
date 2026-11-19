# -*- mode: python ; coding: utf-8 -*-
# Сборка: pyinstaller packaging/streamclipper.spec --noconfirm
# Получается папка dist/StreamClipper с двумя программами:
#   StreamClipper.exe  окно (без чёрной консоли)
#   sc-worker.exe      служебный процесс: запись стрима, монтаж клипа (его запускает окно)
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

root = os.path.abspath(os.path.join(SPECPATH, ".."))
datas, binaries, hidden = [], [], []
for pkg in ("streamlink", "pycountry", "faster_whisper", "ctranslate2", "onnxruntime", "av", "tokenizers",
            "huggingface_hub", "certifi"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hidden += h
    except Exception as e:
        print("skip", pkg, e)
for pkg in ("telethon", "streamclipper", "anthropic"):
    hidden += collect_submodules(pkg)
hidden += ["cryptg", "PySide6.QtSvg", "sqlite3"]
for pkg in ("streamlink", "anthropic"):
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass

a = Analysis([os.path.join(root, "packaging", "entry.py")], pathex=[root], binaries=binaries, datas=datas,
             hiddenimports=hidden, excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6", "torch"], noarchive=False)
pyz = PYZ(a.pure)
icon = os.path.join(root, "packaging", "streamclipper.ico")
icon = icon if os.path.exists(icon) else None
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="StreamClipper", console=False, icon=icon)
worker = EXE(pyz, a.scripts, [], exclude_binaries=True, name="sc-worker", console=True, icon=icon)
coll = COLLECT(gui, worker, a.binaries, a.datas, name="StreamClipper")
