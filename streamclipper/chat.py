"""Чтение чата Twitch без логина (анонимный IRC). Сам переподключается."""
from __future__ import annotations

import logging
import random
import re
import socket
import ssl
import threading
import time
from typing import Callable

log = logging.getLogger("chat")


class ChatClient(threading.Thread):
    HOST, PORT = "irc.chat.twitch.tv", 6697

    def __init__(self, channel: str, on_message: Callable[[float, str, str, dict], None],
                 on_delete: Callable[[str, str], None] | None = None):
        super().__init__(daemon=True, name=f"chat-{channel}")
        self.channel = channel.lower()
        self.on_message = on_message
        self.on_delete = on_delete          # (ник, id сообщения): модератор удалил сообщение или забанил зрителя
        self.room_id = ""                   # номер канала в Twitch, нужен для смайлов 7TV/BTTV/FFZ
        self.connected = False
        self.last_rx = 0.0
        self._quit = threading.Event()
        self._sock: socket.socket | None = None

    def stop(self) -> None:
        self._quit.set()
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass

    def run(self) -> None:
        delay = 2.0
        while not self._quit.is_set():
            try:
                self._session()
                delay = 2.0
            except Exception as e:  # сеть моргнула: просто пробуем ещё раз
                log.warning("чат %s: %s", self.channel, e)
            self.connected = False
            if self._quit.wait(delay + random.random()):
                break
            delay = min(delay * 1.7, 60.0)

    def _session(self) -> None:
        raw = socket.create_connection((self.HOST, self.PORT), timeout=20)
        sock = ssl.create_default_context().wrap_socket(raw, server_hostname=self.HOST)
        self._sock = sock
        sock.settimeout(30)
        nick = f"justinfan{random.randint(10000, 99999)}"
        # tags: цвет ника и смайлы в каждом сообщении; commands: удаления и баны от модераторов
        sock.sendall(f"CAP REQ :twitch.tv/tags twitch.tv/commands\r\nNICK {nick}\r\nJOIN #{self.channel}\r\n".encode())
        buf = b""
        self.last_rx = time.time()
        last_ping = time.time()
        while not self._quit.is_set():
            try:
                data = sock.recv(16384)
            except socket.timeout:
                data = None
            now = time.time()
            if data == b"":
                raise ConnectionError("сервер закрыл соединение")
            if data:
                self.last_rx = now
                buf += data
                *lines, buf = buf.split(b"\r\n")
                for line in lines:
                    self._on_line(sock, line.decode("utf-8", "replace"), now)
            if now - last_ping > 120:
                sock.sendall(b"PING :sc\r\n")
                last_ping = now
            if now - self.last_rx > 300:
                raise ConnectionError("чат молчит 5 минут, переподключаюсь")

    def _on_line(self, sock, line: str, now: float) -> None:
        if line.startswith("PING"):
            sock.sendall(("PONG" + line[4:] + "\r\n").encode())
            return
        tags: dict[str, str] = {}
        if line.startswith("@"):
            raw_tags, _, line = line.partition(" ")
            tags = parse_tags(raw_tags)
        parts = line.split(" ", 3)
        cmd = parts[1] if len(parts) >= 2 else ""
        if cmd in ("366", "JOIN"):
            self.connected = True
        if tags.get("room-id"):
            self.room_id = tags["room-id"]
        if len(parts) == 4 and cmd == "PRIVMSG":
            user = parts[0][1:].split("!", 1)[0]
            text = parts[3][1:] if parts[3].startswith(":") else parts[3]
            if text.startswith("\x01ACTION "):     # сообщение через /me; позиции смайлов Twitch считает уже без этой обёртки
                text = text[8:].rstrip("\x01")
            self.connected = True
            meta = {"name": tags.get("display-name") or user, "color": tags.get("color", ""),
                    "emotes": parse_emotes(tags.get("emotes", "")), "id": tags.get("id", "")}
            try:
                self.on_message(now, user, text, meta)
            except Exception:
                log.exception("on_message")
        elif cmd == "CLEARCHAT" and self.on_delete:          # бан или тайм-аут: убрать все сообщения зрителя
            who = parts[3][1:] if len(parts) == 4 else ""
            self._deleted(who.strip().lower() or "*", "")
        elif cmd == "CLEARMSG" and self.on_delete:           # удалено одно сообщение
            self._deleted(tags.get("login", ""), tags.get("target-msg-id", ""))
        elif cmd == "RECONNECT":
            raise ConnectionError("Twitch попросил переподключиться")

    def _deleted(self, user: str, msg_id: str) -> None:
        try:
            self.on_delete(user, msg_id)                     # type: ignore[misc]
        except Exception:
            log.exception("on_delete")


_UNESC = {"\\s": " ", "\\:": ";", "\\\\": "\\", "\\r": "", "\\n": ""}


def parse_tags(raw: str) -> dict[str, str]:
    """Служебные поля сообщения Twitch: «@color=#FF0000;display-name=Вася;emotes=...»."""
    out: dict[str, str] = {}
    for kv in raw.lstrip("@").split(";"):
        k, _, v = kv.partition("=")
        if "\\" in v:
            v = re.sub(r"\\[s:\\rn]", lambda m: _UNESC[m.group(0)], v)
        out[k] = v
    return out


def parse_emotes(raw: str) -> list[list]:
    """«25:0-4,12-16/1902:6-10» -> [[начало, конец, id], ...] по возрастанию позиции."""
    out: list[list] = []
    for part in raw.split("/"):
        eid, _, spans = part.partition(":")
        if not eid or not spans:
            continue
        for span in spans.split(","):
            a, _, b = span.partition("-")
            if a.isdigit() and b.isdigit():
                out.append([int(a), int(b), eid])
    out.sort()
    return out
