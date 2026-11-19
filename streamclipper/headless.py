"""Запуск без окна: для проверки и для серверов. python -m streamclipper headless [секунд] [канал...]"""
from __future__ import annotations

import queue
import time

from . import config, util
from .supervisor import Supervisor

DRAIN = 420           # сколько секунд после остановки записи ждём, пока домонтируются пойманные моменты


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
    try:
        time.sleep(14)
        # Запись останавливаем до того, как ждать монтаж: в разгар вечера новые моменты приходят быстрее,
        # чем монтируются старые, и такое ожидание не кончалось бы никогда.
        sup.stop_recording()
        drain_until = time.time() + DRAIN
        while sup.edit_q.busy() and time.time() < drain_until:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    if sup.edit_q.busy():
        print("UNFINISHED EDITS: %d" % (sup.edit_q.qsize() + (1 if sup.edit_q.active() else 0)), flush=True)
    sup.shutdown()
    try:
        time.sleep(1.5)
        wait_until = time.time() + 240
        while sup.edit_q.active() and time.time() < wait_until:    # начатый клип даём домонтировать, новых уже не будет
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    while True:
        try:
            print(sup.events.get_nowait(), flush=True)
        except queue.Empty:
            break
    return 0
