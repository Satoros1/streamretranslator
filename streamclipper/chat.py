"""Чтение чата Twitch без логина (анонимный IRC). Сам переподключается."""
from __future__ import annotations

import logging
import random
import socket
import ssl
import threading
import time
from typing import Callable

log = logging.getLogger("chat")


class ChatClient(threading.Thread):
    HOST, PORT = "irc.chat.twitch.tv", 6697

    def __init__(self, channel: str, on_message: Callable[[float, str, str], None]):
        super().__init__(daemon=True, name=f"chat-{channel}")
        self.channel = channel.lower()
        self.on_message = on_message
        self.connected = False
        self.last_rx = 0.0
        self._stop = threading.Event()
        self._sock: socket.socket | None = None

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass

    def run(self) -> None:
        delay = 2.0
        while not self._stop.is_set():
            try:
                self._session()
                delay = 2.0
            except Exception as e:  # сеть моргнула: просто пробуем ещё раз
                log.warning("чат %s: %s", self.channel, e)
            self.connected = False
            if self._stop.wait(delay + random.random()):
                break
            delay = min(delay * 1.7, 60.0)

    def _session(self) -> None:
        raw = socket.create_connection((self.HOST, self.PORT), timeout=20)
        sock = ssl.create_default_context().wrap_socket(raw, server_hostname=self.HOST)
        self._sock = sock
        sock.settimeout(30)
        nick = f"justinfan{random.randint(10000, 99999)}"
        sock.sendall(f"NICK {nick}\r\nJOIN #{self.channel}\r\n".encode())
        buf = b""
        self.last_rx = time.time()
        last_ping = time.time()
        while not self._stop.is_set():
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
                    self._handle(sock, line.decode("utf-8", "replace"), now)
            if now - last_ping > 120:
                sock.sendall(b"PING :sc\r\n")
                last_ping = now
            if now - self.last_rx > 300:
                raise ConnectionError("чат молчит 5 минут, переподключаюсь")

    def _handle(self, sock, line: str, now: float) -> None:
        if line.startswith("PING"):
            sock.sendall(("PONG" + line[4:] + "\r\n").encode())
            return
        if line.startswith("@"):
            line = line.split(" ", 1)[1] if " " in line else line
        parts = line.split(" ", 3)
        if len(parts) >= 2 and parts[1] in ("366", "JOIN"):
            self.connected = True
        if len(parts) == 4 and parts[1] == "PRIVMSG":
            user = parts[0][1:].split("!", 1)[0]
            text = parts[3][1:] if parts[3].startswith(":") else parts[3]
            if text.startswith("\x01ACTION "):
                text = text[8:].rstrip("\x01")
            self.connected = True
            try:
                self.on_message(now, user, text)
            except Exception:
                log.exception("on_message")
        elif len(parts) >= 2 and parts[1] == "RECONNECT":
            raise ConnectionError("Twitch попросил переподключиться")
