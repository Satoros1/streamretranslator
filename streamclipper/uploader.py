"""Отправка клипов в Telegram-канал ФАЙЛОМ (документом), чтобы Telegram не пережимал видео.

Работает через «настоящий» протокол Telegram (MTProto, библиотека Telethon), поэтому
лимит на файл 2 ГБ, а не 50 МБ как у обычных ботов. Можно войти своим аккаунтом или ботом.
Очередь лежит в базе на диске: если пропал интернет или программу закрыли,
после запуска она дошлёт всё, что не успела.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import sqlite3
import threading
import time
from collections import deque
from typing import Callable

from . import config, util

log = logging.getLogger("telegram")
MAX_ATTEMPTS = 15
ALERT_TTL = 2 * 3600        # уведомление, которое не удалось отправить за это время, уже никому не нужно


class UploadQueue:
    def __init__(self):
        self.path = str(config.data_dir() / "uploads.db")
        self.lock = threading.Lock()
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS uploads(
                id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT, caption TEXT, status TEXT DEFAULT 'pending',
                attempts INTEGER DEFAULT 0, next_try REAL DEFAULT 0, error TEXT DEFAULT '', created REAL, sent_at REAL)""")
            cols = {r[1] for r in db.execute("PRAGMA table_info(uploads)")}
            if "msg_id" not in cols:
                db.execute("ALTER TABLE uploads ADD COLUMN msg_id INTEGER")
                db.execute("ALTER TABLE uploads ADD COLUMN fb INTEGER")
            if "chat" not in cols:
                db.execute("ALTER TABLE uploads ADD COLUMN chat TEXT")
        self._counts: tuple[float, dict] = (0.0, {})

    def set_msg(self, id_: int, msg_id: int, chat: str = "") -> None:
        with self.lock, self._db() as db:
            db.execute("UPDATE uploads SET msg_id=?, chat=? WHERE id=?", (msg_id, chat, id_))

    def set_fb(self, id_: int, fb: int | None) -> None:
        with self.lock, self._db() as db:
            db.execute("UPDATE uploads SET fb=? WHERE id=?", (fb, id_))

    def recent_sent(self, chat: str, days: float = 3):
        """Недавно отправленные посты именно в этот канал. Номера постов из другого канала здесь значили бы
        чужие сообщения (или «пост удалён»), и программа училась бы на ложных оценках."""
        with self.lock, self._db() as db:
            return db.execute("SELECT id, path, msg_id, fb FROM uploads WHERE status='sent' AND msg_id IS NOT NULL "
                              "AND chat=? AND sent_at>? ORDER BY id DESC LIMIT 200",
                              (chat, time.time() - days * 86400)).fetchall()

    def _db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def add(self, path: str, caption: str) -> None:
        with self.lock, self._db() as db:
            db.execute("INSERT INTO uploads(path, caption, created) VALUES(?,?,?)", (path, caption, time.time()))

    def next(self):
        with self.lock, self._db() as db:
            return db.execute("SELECT * FROM uploads WHERE status='pending' AND next_try<=? ORDER BY id LIMIT 1",
                              (time.time(),)).fetchone()

    def mark(self, id_: int, status: str, error: str = "", delay: float = 0) -> None:
        with self.lock, self._db() as db:
            db.execute("UPDATE uploads SET status=?, error=?, attempts=attempts+1, next_try=?, sent_at=? WHERE id=?",
                       (status, error[:300], time.time() + delay, time.time() if status == "sent" else None, id_))

    def postpone(self, id_: int, delay: float) -> None:
        with self.lock, self._db() as db:
            db.execute("UPDATE uploads SET next_try=? WHERE id=?", (time.time() + delay, id_))

    def counts(self, max_age: float = 0.0) -> dict:
        """max_age: окно программы спрашивает счётчики дважды в секунду, ему хватит ответа секундной давности."""
        ts, cached = self._counts
        if max_age and time.time() - ts < max_age:
            return cached
        with self.lock, self._db() as db:
            rows = db.execute("SELECT status, COUNT(*) c FROM uploads GROUP BY status").fetchall()
        out = {r["status"]: r["c"] for r in rows}
        self._counts = (time.time(), out)
        return out

    def is_sent(self, path: str) -> bool:
        with self.lock, self._db() as db:
            return db.execute("SELECT 1 FROM uploads WHERE path=? AND status='sent'", (path,)).fetchone() is not None

    def has_pending(self, path: str) -> bool:
        with self.lock, self._db() as db:
            return db.execute("SELECT 1 FROM uploads WHERE path=? AND status='pending'", (path,)).fetchone() is not None

    def retry_failed(self) -> None:
        with self.lock, self._db() as db:
            db.execute("UPDATE uploads SET status='pending', attempts=0, next_try=0 WHERE status='failed'")

    def cleanup(self) -> None:
        with self.lock, self._db() as db:
            db.execute("DELETE FROM uploads WHERE status='sent' AND sent_at<?", (time.time() - 30 * 86400,))


TG_KEYS = ("tg_enabled", "tg_mode", "tg_api_id", "tg_api_hash", "tg_bot_token", "tg_phone", "tg_channel",
           "tg_alerts", "tg_alerts_chat")


def pick_channel(text: str, items: list[tuple[str, object]], index: int = -1) -> tuple[str, str]:
    """Что записать в настройки по тому, что стоит в поле канала: (канал, его название для показа).

    Канал, выбранный из списка «Мои каналы», запоминается по номеру: название могут поменять, а приватный
    канал по названию не найти. Текст, набранный руками (@имя или ссылка), берётся как есть.
    """
    text = text.strip()
    if 0 <= index < len(items) and items[index][0] == text and items[index][1]:
        return str(items[index][1]), text
    same = [str(cid) for title, cid in items if cid and title.strip().lower() == text.lower()]
    if text and len(set(same)) == 1:                    # название набрано руками, а выбрать из списка забыли
        return same[0], next(t for t, c in items if str(c) == same[0])
    return text, ""


def summarize_test(res: list[tuple[str, str]], alerts_on: bool = True) -> tuple[str, bool]:
    """Текст для человека по итогам проверочного сообщения: (текст, есть ли ошибки)."""
    lines = [f"{where}: " + (f"не получилось ❌ {why}" if why else "отправлено ✅") for where, why in res]
    if not alerts_on:
        lines.append("Уведомления выключены галочкой выше, поэтому в чат для уведомлений ничего не отправлялось.")
    return "\n".join(lines), any(why for _where, why in res)


class Telegram:
    """Живёт в своём потоке. Всё наружу потокобезопасно."""

    def __init__(self, on_event: Callable[[dict], None]):
        self.on_event = on_event
        self.queue = UploadQueue()
        self.status = "off"          # off | connecting | need_login | no_channel | ok | error
        self.status_text = "выключен"
        self.me = ""
        self.progress = ""
        self.votes = ""              # реакции под клипами в канале: on | off | "" (неизвестно)
        self._alerts: deque[tuple[float, str]] = deque(maxlen=30)
        self._alert_seen: dict[str, float] = {}
        self._cfg: dict = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client = None
        self._target = None
        self._restart = threading.Event()
        self._stop = threading.Event()
        self._phone_hash = None
        self._need_password = False      # код уже принят, Telegram ждёт только облачный пароль
        self._chat_key = ""              # канал, в который сейчас уходят клипы
        self._last_fb = 0.0
        self._thread = threading.Thread(target=self._run, daemon=True, name="telegram")

    # --- снаружи ----------------------------------------------------------
    def start(self, cfg: dict) -> None:
        self._cfg = dict(cfg)
        if not self._thread.is_alive():
            self._thread.start()

    def reconfigure(self, cfg: dict) -> None:
        keys = ("tg_enabled", "tg_mode", "tg_api_id", "tg_api_hash", "tg_bot_token", "tg_phone", "tg_channel")
        changed = any(self._cfg.get(k) != cfg.get(k) for k in keys)
        self._cfg = dict(cfg)
        if changed:
            self._restart.set()

    def stop(self) -> None:
        self._stop.set()
        self._restart.set()

    def enqueue(self, path: str, caption: str) -> None:
        self.queue.add(path, caption)

    def alert(self, text: str, key: str | None = None, every: float = 1800) -> None:
        """Служебное уведомление. Одно и то же (по key) не чаще, чем раз в every секунд."""
        if not self._cfg.get("tg_alerts", True) or not self._cfg.get("tg_enabled"):
            return                       # Telegram выключен: не копим уведомления, которые некуда слать
        now = time.time()
        if key:
            if now - self._alert_seen.get(key, 0) < every:
                return
            self._alert_seen[key] = now
        self._alerts.append((now, text))

    def call(self, coro_fn, timeout: float = 60):
        """Выполнить корутину в потоке Telegram и дождаться результата (для окна настроек)."""
        if not self._loop:
            raise RuntimeError("Telegram ещё не запущен")
        fut = asyncio.run_coroutine_threadsafe(coro_fn(), self._loop)
        try:
            return fut.result(timeout)
        except concurrent.futures.TimeoutError:
            fut.cancel()
            raise RuntimeError("Telegram не ответил вовремя. Проверь интернет и попробуй ещё раз") from None

    # --- вход по коду (режим «свой аккаунт») ------------------------------
    def request_code(self, phone: str) -> str:
        async def go():
            if not self._client:
                raise RuntimeError("Сначала сохрани api_id и api_hash")
            sent = await self._client.send_code_request(phone)
            self._phone_hash = (phone, sent.phone_code_hash)
            self._need_password = False
            return "Код отправлен в Telegram"
        return self.call(go)

    def submit_code(self, code: str, password: str = "") -> str:
        async def go():
            from telethon import errors
            if not self._client:
                raise RuntimeError("Сначала нажми «Получить код»")
            phone, h = self._phone_hash or (self._cfg.get("tg_phone", ""), None)
            if not self._need_password:          # код уже принят: второй раз его слать нельзя, он одноразовый
                try:
                    await self._client.sign_in(phone=phone, code=code.strip(), phone_code_hash=h)
                except errors.SessionPasswordNeededError:
                    self._need_password = True
            if self._need_password:
                if not password:
                    return "2fa"
                await self._client.sign_in(password=password)
                self._need_password = False
            self._restart.set()
            return "ok"
        return self.call(go)

    def list_channels(self) -> list[tuple[str, str]]:
        """Каналы и группы, куда можно писать. Для выпадающего списка в настройках."""
        async def go():
            out = []
            async for d in self._client.iter_dialogs(limit=400):
                e = d.entity
                if d.is_channel and (getattr(e, "creator", False) or getattr(e, "admin_rights", None)
                                     or getattr(e, "megagroup", False)):
                    out.append((str(d.id), d.name or str(d.id)))
            return out
        return self.call(go, 90)

    def send_test(self) -> list[tuple[str, str]]:
        """Проверочное сообщение туда же, куда пойдут клипы и уведомления (кнопка в настройках).
        Возвращает [(куда, ошибка)]: пустая ошибка значит, что сообщение дошло."""
        return self.call(self._send_test, 60)

    async def _send_test(self) -> list[tuple[str, str]]:
        client, cfg = self._client, self._cfg
        if client is None:
            raise RuntimeError("Telegram ещё не подключён")
        bot = cfg.get("tg_mode") == "bot"
        out = []
        # 1) канал, куда уходят клипы: человеку важно увидеть именно его
        if self._target is None:
            why = self.status_text if self.status == "no_channel" else "канал не выбран"
            out.append(("Канал для клипов", why))
        else:
            out.append(("Канал для клипов", await self._try_send(
                client, self._target, "🛠 StreamClipper\nПроверка связи: сюда будут приходить клипы ✅\n"
                                      "Чтобы оценить клип, зажми сообщение с ним и поставь 👍 или 👎. Программа "
                                      "учится на оценках всех подписчиков канала.\n"
                                      "Это сообщение можно удалить.")))
            self.votes = await self._reactions_state()
        # 2) чат для служебных уведомлений
        if cfg.get("tg_alerts", True):
            dest = str(cfg.get("tg_alerts_chat", "")).strip()
            where = f"Уведомления ({dest})" if dest else "Уведомления («Избранное»)"
            if not dest and bot:
                out.append(("Уведомления", "боту нужно указать чат для уведомлений в поле выше"))
            else:
                try:
                    target = await self._resolve(client, dest, bot) if dest else "me"
                except Exception as e:
                    out.append((where, f"чат не найден: {str(e)[:100]}"))
                else:
                    out.append((where, await self._try_send(
                        client, target, "🛠 StreamClipper\nПроверка связи: сюда будут приходить уведомления ✅")))
        return out

    @staticmethod
    async def _try_send(client, target, text: str) -> str:
        """Отправляет сообщение и возвращает понятную причину, если не вышло (пустая строка = дошло)."""
        try:
            await client.send_message(target, text)
            return ""
        except Exception as e:
            kind = type(e).__name__
            if kind in ("ChatWriteForbiddenError", "ChatAdminRequiredError", "UserBannedInChannelError"):
                return "нет прав писать сюда: сделай аккаунт или бота админом с правом публикации"
            if kind == "ChannelPrivateError":
                return "нет доступа: аккаунт или бот не состоит в этом канале"
            if kind == "FloodWaitError":
                return f"Telegram просит подождать {getattr(e, 'seconds', '?')} с, попробуй позже"
            return str(e)[:120] or kind

    # --- оценки под клипами (реакции в канале) ------------------------------
    VOTES = ("👍", "👎")

    async def _reactions_state(self) -> str:
        """Можно ли в канале ставить реакции под клипами: "on", "off" или "" (узнать не удалось)."""
        client = self._client
        if client is None or self._target is None:
            return ""
        try:
            from telethon.tl import functions, types
            full = await client(functions.channels.GetFullChannelRequest(self._target))
            allowed = getattr(full.full_chat, "available_reactions", None)
            if allowed is None or isinstance(allowed, types.ChatReactionsNone):
                return "off"
            if isinstance(allowed, types.ChatReactionsSome) and not allowed.reactions:
                return "off"
            return "on"
        except Exception as e:
            log.info("не удалось узнать, включены ли реакции в канале: %s", e)
            return ""

    def enable_reactions(self) -> str:
        """Включает в канале реакции 👍 и 👎 (кнопка в настройках). Возвращает причину, если не вышло."""
        return self.call(self._enable_reactions, 40)

    async def _enable_reactions(self) -> str:
        client = self._client
        if client is None or self._target is None:
            return "Telegram ещё не подключён или канал не выбран"
        try:
            from telethon.tl import functions, types
            await client(functions.messages.SetChatAvailableReactionsRequest(
                peer=self._target,
                available_reactions=types.ChatReactionsSome(
                    reactions=[types.ReactionEmoji(emoticon=e) for e in self.VOTES])))
        except Exception as e:
            kind = type(e).__name__
            if kind != "ChatNotModifiedError":           # «уже так и есть» ошибкой не считаем
                if kind in ("ChatAdminRequiredError", "ChatWriteForbiddenError", "RightForbiddenError"):
                    return ("нет прав менять настройки канала. Включи сам: канал → Изменить → Реакции, "
                            "или дай этому аккаунту право менять информацию о канале")
                if kind == "FloodWaitError":
                    return f"Telegram просит подождать {getattr(e, 'seconds', '?')} с, попробуй позже"
                return str(e)[:120] or kind
        self.votes = await self._reactions_state() or "on"
        return ""

    def logout(self) -> None:
        async def go():
            await self._client.log_out()
        try:
            self.call(go, 30)
        finally:
            self._restart.set()

    # --- поток ------------------------------------------------------------
    def _set(self, status: str, text: str) -> None:
        if (status, text) != (self.status, self.status_text):
            self.status, self.status_text = status, text
            self.on_event({"t": "tg", "status": status, "text": text})

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        while not self._stop.is_set():
            self._restart.clear()
            try:
                self._loop.run_until_complete(self._session())
            except Exception as e:
                log.exception("сессия Telegram упала")
                self._set("error", f"ошибка: {str(e)[:120]}")
            if self._stop.is_set():
                break
            # перезапуск сразу, если поменяли настройки, иначе пауза
            self._restart.wait(0.5 if self._restart.is_set() else 20)

    async def _session(self) -> None:
        cfg = self._cfg
        self._client = None
        self.votes = ""
        if not cfg.get("tg_enabled"):
            self._set("off", "выключен")
            await self._wait_restart()
            return
        try:
            api_id = int(str(cfg.get("tg_api_id", "")).strip())
            api_hash = str(cfg.get("tg_api_hash", "")).strip()
            assert api_hash
        except Exception:
            self._set("error", "не заполнены api_id и api_hash")
            await self._wait_restart()
            return
        from telethon import TelegramClient, errors

        bot = cfg.get("tg_mode") == "bot"
        self._set("connecting", "подключаюсь…")
        session = str(config.data_dir() / ("telegram_bot" if bot else "telegram_user"))
        client = TelegramClient(session, api_id, api_hash, flood_sleep_threshold=180,
                                connection_retries=10, retry_delay=3, auto_reconnect=True,
                                device_model="StreamClipper", app_version="1.0")
        self._client = client
        try:
            await client.connect()
            if not await client.is_user_authorized():
                if bot:
                    token = str(cfg.get("tg_bot_token", "")).strip()
                    if not token:
                        self._set("error", "не указан токен бота")
                        await self._wait_restart()
                        return
                    await client.sign_in(bot_token=token)
                else:
                    self._set("need_login", "нужно войти в аккаунт (Настройки → Telegram)")
                    await self._wait_restart()
                    return
            me = await client.get_me()
            self.me = getattr(me, "username", None) or getattr(me, "first_name", "") or ""
            self._target = None
            self._chat_key = ""
            spec = str(cfg.get("tg_channel", "")).strip()
            if spec:
                try:
                    self._target = await self._resolve(client, spec, bot)
                    self._chat_key = self._peer_key(self._target, spec)
                except Exception as e:
                    self._set("no_channel", f"канал не найден: {str(e)[:80]}")
            else:
                self._set("no_channel", "не выбран канал")
            if self._target is not None:
                self._set("ok", f"подключён ({self.me})")
                self.votes = await self._reactions_state()
            await self._work(client, errors, bot)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    async def _wait_restart(self) -> None:
        while not self._restart.is_set():
            await asyncio.sleep(0.5)

    @staticmethod
    async def _resolve(client, spec: str, bot: bool):
        s = spec.strip()
        for p in ("https://", "http://"):
            if s.startswith(p):
                s = s[len(p):]
        if s.startswith("t.me/") and not s.startswith(("t.me/+", "t.me/joinchat")):
            s = "@" + s[5:].split("/")[0].split("?")[0]
        if s.lstrip("-").isdigit():
            n = int(s)
            if not bot:
                await client.get_dialogs(limit=400)        # чтобы Telegram «узнал» канал по номеру
            return await client.get_input_entity(n)
        if s.startswith("t.me/"):
            s = "https://" + s
        return await client.get_input_entity(s)

    @staticmethod
    def _peer_key(target, spec: str) -> str:
        """Постоянная метка канала: по ней отличаем посты этого канала от постов прежнего."""
        try:
            from telethon import utils
            return str(utils.get_peer_id(target))
        except Exception:
            return spec.strip().lower()

    GOOD = set("👍❤🔥😁🤣😂👏💯⚡🏆😆🥰🤩😍")
    BAD = set("👎💩🤮😴🥱😐🤡")

    async def _read_feedback(self, client) -> None:
        """Оценки прямо в канале: 👍 под клипом = хороший, 👎 или удалённый пост = плохой."""
        rows = self.queue.recent_sent(self._chat_key)
        for i in range(0, len(rows), 90):
            part = rows[i:i + 90]
            msgs = await client.get_messages(self._target, ids=[r["msg_id"] for r in part])
            for r, m in zip(part, msgs):
                if m is None:
                    fb = 0                                   # пост удалили: клип не понравился
                else:
                    good = bad = 0
                    for rc in (getattr(getattr(m, "reactions", None), "results", None) or []):
                        emo = getattr(rc.reaction, "emoticon", "")
                        if emo in self.GOOD:
                            good += rc.count
                        elif emo in self.BAD:
                            bad += rc.count
                    fb = 1 if good > bad else 0 if bad > good else None
                if fb != r["fb"]:
                    self.queue.set_fb(r["id"], fb)
                    if fb is not None:
                        self.on_event({"t": "reaction", "path": r["path"], "good": bool(fb)})

    async def _work(self, client, errors, bot: bool) -> None:
        last_cleanup = 0.0
        while not self._restart.is_set():
            if not client.is_connected():
                raise ConnectionError("соединение с Telegram потеряно")
            # 1) служебные уведомления
            while self._alerts:
                born, text = self._alerts.popleft()
                if time.time() - born > ALERT_TTL:
                    continue                                # связи не было слишком долго: новость устарела
                try:
                    dest = str(self._cfg.get("tg_alerts_chat", "")).strip()
                    if dest:
                        target = await self._resolve(client, dest, bot)
                    elif not bot:
                        target = "me"                       # «Избранное» владельца
                    else:
                        target = None                       # бот без чата для уведомлений молчит
                    if target is not None:
                        await client.send_message(target, "🛠 StreamClipper\n" + text)
                except Exception as e:
                    log.warning("не смог отправить уведомление: %s", e)
            # 2) клипы
            row = self.queue.next() if self._target is not None else None
            if row is None and self._target is not None and time.time() - self._last_fb > 300:
                self._last_fb = time.time()
                try:
                    await self._read_feedback(client)
                except Exception as e:
                    log.info("не смог прочитать реакции: %s", e)
            if row is None:
                if time.time() - last_cleanup > 3600:
                    self.queue.cleanup()
                    last_cleanup = time.time()
                await asyncio.sleep(1.5)
                continue
            path = row["path"]
            if not os.path.exists(path):
                self.queue.mark(row["id"], "missing", "файл удалён")
                continue
            name = os.path.basename(path)
            try:
                size = os.path.getsize(path)
            except OSError:
                self.queue.mark(row["id"], "missing", "файл удалён")
                continue

            def prog(sent, total, name=name):
                self.progress = f"{name}: {100 * sent // max(total, 1)}%"

            try:
                msg = await client.send_file(self._target, path, caption=row["caption"] or "", force_document=True,
                                             parse_mode=None, progress_callback=prog, part_size_kb=512)
                self.queue.mark(row["id"], "sent")
                try:
                    self.queue.set_msg(row["id"], int(msg.id), self._chat_key)
                except Exception:
                    pass
                self.progress = ""
                self._set("ok", f"подключён ({self.me})")
                self.on_event({"t": "uploaded", "path": path, "size": size})
                if self._cfg.get("delete_after_upload"):
                    util.remove_clip(path)
            except errors.FloodWaitError as e:
                log.warning("Telegram просит подождать %s с", e.seconds)
                self._set("ok", f"Telegram просит подождать {e.seconds} с")
                self.queue.postpone(row["id"], e.seconds + 3)
                await asyncio.sleep(min(e.seconds + 3, 30))
            except (errors.ChatWriteForbiddenError, errors.ChatAdminRequiredError, errors.ChannelPrivateError) as e:
                self._set("no_channel", "нет прав писать в канал: сделай аккаунт/бота админом")
                self.queue.postpone(row["id"], 120)
                log.warning("нет прав на канал: %s", e)
                await asyncio.sleep(10)
            except (ConnectionError, OSError, asyncio.TimeoutError) as e:
                self.queue.postpone(row["id"], 20)
                raise ConnectionError(str(e))
            except Exception as e:
                attempts = row["attempts"] + 1
                log.warning("отправка %s не удалась (%s/%s): %s", name, attempts, MAX_ATTEMPTS, e)
                if attempts >= MAX_ATTEMPTS:
                    self.queue.mark(row["id"], "failed", str(e))
                    self.on_event({"t": "upload_failed", "path": path, "error": str(e)[:200]})
                else:
                    self.queue.mark(row["id"], "pending", str(e), delay=min(30 * 2 ** attempts, 900))
                await asyncio.sleep(2)
