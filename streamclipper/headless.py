"""Запуск без окна: для проверки и для серверов. python -m streamclipper headless [секунд] [канал...]"""
from __future__ import annotations

import queue
import time

from . import config, util
from .supervisor import Supervisor


def main(argv: list[str]) -> int:
    util.setup_logging("app", console=True)
    seconds = float(argv[0]) if argv else 0
    cfg = config.load()
    for ch in argv[1:]:
        name = config.parse_channel(ch)
        if name and name not in cfg["channels"]:
            cfg["channels"].append(name)
    config.save(cfg)
    sup = Supervisor()
    sup.start()
    clip_at = time.time() + seconds * 0.7 if seconds else None
    end = time.time() + seconds if seconds else None
    try:
        while end is None or time.time() < end:
            try:
                ev = sup.events.get(timeout=1)
                print(ev, flush=True)
            except queue.Empty:
                pass
            if clip_at and time.time() > clip_at:
                clip_at = None
                for s in sup.snapshot():
                    print("STATUS", s, flush=True)
                    sup.clip_now(s["name"])
    except KeyboardInterrupt:
        pass
    time.sleep(14)
    while not sup.edit_q.empty() or sup.editing:
        time.sleep(1)
    while True:
        try:
            print(sup.events.get_nowait(), flush=True)
        except queue.Empty:
            break
    sup.shutdown()
    return 0
