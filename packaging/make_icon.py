"""Рисует значок программы в .ico (тот же логотип, что в окне)."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
from streamclipper.ui import theme  # noqa: E402

out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "streamclipper.ico")
ok = theme.draw_logo(256).save(out, "ICO")
print("icon", out, ok)
sys.exit(0 if ok else 1)
