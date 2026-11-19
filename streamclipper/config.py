"""Настройки и пути. Всё хранится в одной папке данных, чтобы переживать перезапуски."""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path

from . import APP_NAME

_lock = threading.Lock()


def data_dir() -> Path:
    env = os.environ.get("STREAMCLIPPER_HOME")
    if env:
        p = Path(env)
    elif sys.platform == "win32":
        p = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / APP_NAME
    else:
        p = Path.home() / ".streamclipper"
    p.mkdir(parents=True, exist_ok=True)
    return p


def subdir(name: str) -> Path:
    p = data_dir() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_clips_dir() -> str:
    return str(Path.home() / "Videos" / APP_NAME)


DEFAULTS: dict = {
    "channels": [],
    "paused": False,
    # запись
    "quality": "best",          # best | 1080 | 720 | 480
    "quality_floor": "720",     # когда интернет не успевает, запись сама снижает качество, но не ниже этого; 0 = как угодно
    "buffer_minutes": 6,        # сколько последних минут держим на диске на каждый стрим
    "twitch_oauth": "",         # необязательно: токен аккаунта с Turbo/подпиской (без рекламы)
    "max_streams": 6,
    # клипы
    "clips_dir": "",
    "clip_min": 25,
    "clip_max": 60,
    "lead_in": 20,              # сколько секунд ДО реакции чата берём (сам момент + подводка)
    "tail": 4,
    "manual_len": 45,           # длина клипа по кнопке «Клип сейчас»
    "sensitivity": 3,           # 1 (только самый жир) .. 5 (берём почти всё)
    "min_score": 0,             # не сохранять клипы с оценкой ниже
    "clips_per_hour": 12,       # не больше стольких автоматических клипов в час со всех стримов; 0 = без предела
    "clip_format": "tiktok",    # tiktok: вертикальный 1080x1920; original: как на стриме; both: оба файла
    "tt_layout": "big",         # big: крупно, как в TikTok-нарезках; blur: видео целиком, фон размыт; crop: на весь экран
    "tt_title": True,           # крупная надпись над видео
    "tt_title_text": "",        # своя надпись на каждый клип; пусто = программа придумывает сама
    "look": 0,                  # какой вид клипа уже предложен этому компьютеру (см. load)
    "chat_style": "soft",       # чат в вертикальном клипе: off, text, soft, solid
    "chat_size": "m",           # s, m, l
    "chat_hide": "",            # слова через запятую: сообщения с ними в клип не попадают
    "max_clips_gb": 30,         # потолок папки клипов, старые отправленные удаляются
    "min_free_gb": 5,           # если на диске меньше, запись клипов ставится на паузу
    # распознавание речи и ИИ
    "whisper": True,
    "whisper_model": "small",
    "ai_enabled": False,
    "ai_api_key": "",
    "ai_model": "claude-haiku-5-5",
    "ai_frames": True,          # показывать ИИ кадры из клипа: заголовок получается о том, что на экране
    "ai_filter": False,         # позволить ИИ отбраковывать несмешное
    # telegram
    "tg_enabled": False,
    "tg_mode": "user",          # user (аккаунт) | bot
    "tg_api_id": "",
    "tg_api_hash": "",
    "tg_bot_token": "",
    "tg_phone": "",
    "tg_channel": "",           # @username, ссылка t.me/... или числовой id
    "tg_channel_title": "",     # название канала для показа в окне (если он выбран из списка «Мои каналы»)
    "tg_alerts_chat": "",       # куда слать служебные уведомления; пусто = «Избранное»/канал
    "tg_alerts": True,
    "delete_after_upload": False,
    # система
    "autostart": False,
    "prevent_sleep": True,
    "auto_update_streamlink": True,
}


def config_path() -> Path:
    return data_dir() / "config.json"


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            cfg.update({k: v for k, v in raw.items() if k in DEFAULTS})
    except FileNotFoundError:
        pass
    except Exception:
        # битый файл: не падаем, а берём резервную копию
        try:
            with open(str(config_path()) + ".bak", "r", encoding="utf-8") as f:
                cfg.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
        except Exception:
            pass
    if not cfg["clips_dir"]:
        cfg["clips_dir"] = default_clips_dir()
    if int(cfg.get("look") or 0) < 1:
        # Вид «крупно, с заголовком» стал основным. У кого стоял прежний основной вид, тот получает новый;
        # после этого выбор в настройках больше не трогаем.
        if cfg.get("tt_layout") == "blur":
            cfg["tt_layout"] = "big"
        cfg["look"] = 1
    return cfg


def save(cfg: dict) -> None:
    with _lock:
        path = config_path()
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({k: cfg.get(k, v) for k, v in DEFAULTS.items()}, f, ensure_ascii=False, indent=2)
        if path.exists():
            try:
                shutil.copyfile(path, str(path) + ".bak")      # копия, а не переименование: файл настроек
            except OSError:                                    # ни на миг не пропадает с диска
                pass
        for attempt in range(8):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:                            # Windows: файл в этот миг читает другой процесс
                if attempt == 7:
                    raise
                time.sleep(0.05 * (attempt + 1))


_CHANNEL_RE = re.compile(r"(?:https?://)?(?:www\.|m\.)?twitch\.tv/([A-Za-z0-9_]{2,30})", re.I)


def parse_channel(text: str) -> str | None:
    """Принимает ссылку на стрим или просто ник. Возвращает ник в нижнем регистре."""
    text = (text or "").strip()
    m = _CHANNEL_RE.search(text)
    if m:
        name = m.group(1)
    elif re.fullmatch(r"@?[A-Za-z0-9_]{2,30}", text):
        name = text.lstrip("@")
    else:
        return None
    name = name.lower()
    if name in {"videos", "directory", "settings", "downloads", "p", "popout"}:
        return None
    return name
