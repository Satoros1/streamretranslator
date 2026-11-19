"""Смайлы чата картинками: родные Twitch и сторонние 7TV, BetterTTV, FrankerFaceZ.

Всё здесь необязательное: если сервис смайлов не ответил за отведённое время, смайл просто
останется словом, а клип выйдет вовремя.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

from . import config

log = logging.getLogger("emotes")
HEADERS = {"User-Agent": "StreamClipper"}
SET_TTL = 6 * 3600          # как часто перечитывать список смайлов канала
MAX_IMAGES = 4000           # сколько картинок держать на диске


def twitch_url(emote_id: str) -> str:
    return f"https://static-cdn.jtvnw.net/emoticons/v2/{emote_id}/static/dark/2.0"


def _json(url: str, timeout: float = 5.0):
    import requests
    r = requests.get(url, timeout=timeout, headers=HEADERS)
    r.raise_for_status()
    return r.json()


def _https(url: str) -> str:
    return "https:" + url if url.startswith("//") else url


def parse_7tv(emotes) -> dict[str, str]:
    out: dict[str, str] = {}
    for e in emotes or []:
        try:
            data = e.get("data") or {}
            url = _https((data.get("host") or {}).get("url") or "")
            if not url or int(data.get("flags") or 0) & 256:      # 256: смайл-накладка поверх другого, пропускаем
                continue
            out[str(e["name"])] = url + "/2x.webp"
        except Exception:
            continue
    return out


def parse_bttv(emotes) -> dict[str, str]:
    out: dict[str, str] = {}
    for e in emotes or []:
        try:
            out[str(e["code"])] = f"https://cdn.betterttv.net/emote/{e['id']}/2x"
        except Exception:
            continue
    return out


def parse_ffz(data, only_sets=None) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for sid, st in (data.get("sets") or {}).items():
            if only_sets is not None and str(sid) not in only_sets:
                continue
            for e in st.get("emoticons") or []:
                urls = e.get("urls") or {}
                url = urls.get("2") or urls.get("1")
                if url:
                    out[str(e["name"])] = _https(url)
    except Exception:
        pass
    return out


def _sources(room_id: str) -> list:
    """Список (название, функция) по убыванию важности: смайлы канала главнее общих."""
    src = []
    if room_id:
        src += [
            ("7TV канала", lambda: parse_7tv((_json(f"https://7tv.io/v3/users/twitch/{room_id}").get("emote_set") or {}).get("emotes"))),
            ("BTTV канала", lambda: (lambda d: parse_bttv((d.get("channelEmotes") or []) + (d.get("sharedEmotes") or [])))(
                _json(f"https://api.betterttv.net/3/cached/users/twitch/{room_id}"))),
            ("FFZ канала", lambda: parse_ffz(_json(f"https://api.frankerfacez.com/v1/room/id/{room_id}"))),
        ]
    src += [
        ("7TV общие", lambda: parse_7tv(_json("https://7tv.io/v3/emote-sets/global").get("emotes"))),
        ("BTTV общие", lambda: parse_bttv(_json("https://api.betterttv.net/3/cached/emotes/global"))),
        ("FFZ общие", lambda: (lambda d: parse_ffz(d, {str(x) for x in d.get("default_sets") or []}))(
            _json("https://api.frankerfacez.com/v1/set/global"))),
    ]
    return src


def load_map(room_id: str, budget: float = 7.0) -> dict[str, str]:
    """Слово -> адрес картинки для сторонних смайлов канала. Хранится на диске и обновляется раз в несколько часов."""
    cache = config.subdir("emotes") / f"set_{room_id or 'global'}.json"
    old: dict = {}
    try:
        with open(cache, "r", encoding="utf-8") as f:
            old = json.load(f)
        if time.time() - float(old.get("ts", 0)) < SET_TTL:
            return dict(old.get("map") or {})
    except (OSError, ValueError):
        old = {}
    src = _sources(room_id)
    pool = ThreadPoolExecutor(max_workers=len(src))
    futs = [pool.submit(fn) for _name, fn in src]
    wait(futs, timeout=budget)
    merged: dict[str, str] = {}
    okay = 0
    for (name, _fn), fut in reversed(list(zip(src, futs))):      # с конца: важные источники перезаписывают общие
        if fut.done() and not fut.exception():
            merged.update(fut.result())
            okay += 1
        else:
            log.info("смайлы %s недоступны: %s", name, fut.exception() if fut.done() else "не успели ответить")
    pool.shutdown(wait=False, cancel_futures=True)
    if okay == 0:
        return dict(old.get("map") or {})                         # сеть недоступна: берём прошлый список, если был
    if okay == len(src):                                           # неполный список на диск не кладём, спросим ещё раз
        try:
            tmp = str(cache) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"ts": time.time(), "map": merged}, f, ensure_ascii=False)
            os.replace(tmp, cache)
        except OSError:
            pass
    log.info("смайлы канала: %d штук из %d источников", len(merged), okay)
    return merged


def _img_path(url: str) -> Path:
    return config.subdir("emotes") / "img" / hashlib.sha1(url.encode()).hexdigest()


def _download(url: str) -> bool:
    import requests
    path = _img_path(url)
    r = requests.get(url, timeout=5, headers=HEADERS)
    r.raise_for_status()
    if not r.content or len(r.content) > 3_000_000:
        return False
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "wb") as f:
        f.write(r.content)
    os.replace(tmp, path)
    return True


def fetch_images(urls, budget: float = 9.0) -> dict[str, str]:
    """Скачивает недостающие картинки смайлов. Возвращает адрес -> файл для тех, что есть на диске."""
    urls = list(dict.fromkeys(urls))
    folder = config.subdir("emotes") / "img"
    folder.mkdir(parents=True, exist_ok=True)
    missing = [u for u in urls if not _img_path(u).exists()]
    if missing:
        pool = ThreadPoolExecutor(max_workers=8)
        futs = [pool.submit(_download, u) for u in missing]
        wait(futs, timeout=budget)
        failed = sum(1 for f in futs if not f.done() or f.exception())
        pool.shutdown(wait=False, cancel_futures=True)
        if failed:
            log.info("картинки смайлов: не скачалось %d из %d", failed, len(missing))
        _prune(folder)
    return {u: str(_img_path(u)) for u in urls if _img_path(u).exists()}


def _prune(folder: Path) -> None:
    try:
        files = [p for p in folder.iterdir() if p.is_file()]
        if len(files) <= MAX_IMAGES:
            return
        files.sort(key=lambda p: p.stat().st_mtime)
        for p in files[: len(files) - MAX_IMAGES + 500]:
            p.unlink(missing_ok=True)
    except OSError:
        pass
