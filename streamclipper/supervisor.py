"""Главный диспетчер: следит за процессами записи, очередью монтажа, диском и Telegram.

Правило: ничто не должно ронять программу целиком. Упал процесс стрима, он перезапускается.
Завис (молчит 90 секунд), его убивают и запускают заново. Не смонтировался клип, пробуем ещё раз.
"""
from __future__ import annotations

import glob
import heapq
import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import config, segments, system, updater, util
from .learning import Learner
from .uploader import Telegram

log = logging.getLogger("supervisor")

HANG_SECONDS = 90
OFFLINE_CONFIRM = 90  # эфир считается законченным, только если не вернулся за столько секунд
EDIT_BACKLOG = 12     # столько моментов может ждать монтажа; когда их больше, самые слабые пропускаются
STATE_RU = {
    "starting": "запускается", "offline": "не в эфире", "live": "идёт запись", "ad": "реклама, пропускаю",
    "pause": "нет видео от Twitch, жду", "error": "ошибка", "paused": "пауза", "restarting": "перезапуск",
    "stopped": "остановлен",
}


class WorkerHandle:
    def __init__(self, name: str):
        self.name = name
        self.proc: subprocess.Popen | None = None
        self.status: dict = {"state": "starting"}
        self.last_msg = 0.0
        self.next_start = 0.0
        self.backoff = 3.0
        self.started = 0.0
        self.restarts = 0
        self.clips = 0
        self.error_since = 0.0
        self.live_since = 0.0
        self.online = False         # об эфире уже сказано человеку
        self.off_at = 0.0           # когда запись сообщила о конце эфира; 0 = не сообщала


class EditQueue:
    """Очередь монтажа. Монтаж идёт по одному клипу, а моменты приходят сразу с нескольких стримов,
    и вечером, когда чат бурлит везде, их бывает больше, чем компьютер успевает смонтировать.

    Поэтому порядок такой: сначала клипы, которые попросили вручную, потом самые сильные моменты.
    И очередь не растёт без конца: когда ждёт больше `limit` заготовок, самая слабая пропускается.
    Иначе клипы приходили бы с опозданием на часы, а заготовки копились бы на диске.
    """

    def __init__(self, limit: int = EDIT_BACKLOG):
        self.limit = limit
        self._cv = threading.Condition()
        self._heap: list[tuple[int, int, int, str]] = []    # (0 ручной / 1 авто, минус оценка, номер по порядку, файл)
        self._seq = 0
        self._active = 0

    def put(self, job: str, score: int = 0, manual: bool = False) -> list[tuple[str, int]]:
        """Ставит заготовку в очередь. Возвращает вытесненные из очереди: [(файл, оценка)]."""
        dropped = []
        with self._cv:
            self._seq += 1
            heapq.heappush(self._heap, (0 if manual else 1, -int(score), self._seq, job))
            while len(self._heap) > self.limit:
                auto = [it for it in self._heap if it[0] == 1]
                if not auto:
                    break                                   # ручные клипы не выбрасываем никогда
                worst = max(auto, key=lambda it: (it[1], -it[2]))   # ниже всех оценка, из равных самый старый
                self._heap.remove(worst)
                heapq.heapify(self._heap)
                dropped.append((worst[3], -worst[1]))
            self._cv.notify()
        return dropped

    def get(self, timeout: float) -> tuple[str, int, bool]:
        """Лучшая заготовка: (файл, оценка, ручной ли клип). После монтажа обязательно вызвать done()."""
        with self._cv:
            if not self._heap:
                self._cv.wait(timeout)
            if not self._heap:
                raise queue.Empty
            manual, neg, _n, job = heapq.heappop(self._heap)
            self._active += 1
            return job, -neg, manual == 0

    def done(self) -> None:
        with self._cv:
            self._active = max(0, self._active - 1)

    def qsize(self) -> int:
        with self._cv:
            return len(self._heap)

    def empty(self) -> bool:
        return self.qsize() == 0

    def active(self) -> bool:
        """Монтируется ли клип прямо сейчас."""
        with self._cv:
            return self._active > 0

    def busy(self) -> bool:
        """Есть ли ещё работа: заготовки в очереди или клип, который монтируется прямо сейчас."""
        with self._cv:
            return bool(self._heap) or self._active > 0


def job_rank(job: str) -> tuple[int, bool]:
    """Оценка и вид заготовки из её файла: (оценка, ручной ли клип). Нужна для заготовок с прошлого запуска."""
    try:
        with open(job, "r", encoding="utf-8") as f:
            d = json.load(f)
        return int(d.get("score") or 0), d.get("kind") == "manual"
    except (OSError, ValueError, TypeError, AttributeError):
        return 0, False


class Supervisor:
    def __init__(self):
        self.cfg = config.load()
        self.events: queue.Queue = queue.Queue()        # всё, что надо показать в окне
        self.workers: dict[str, WorkerHandle] = {}
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.edit_q = EditQueue()
        self.edit_fail: dict[str, int] = {}
        self.editing = ""
        self.recording_off = False                      # запись остановлена, идёт только монтаж пойманного
        self._drop_hint = 0.0
        self.tg = Telegram(self._on_tg)
        self.learner = Learner()
        self.stats = self._load_stats()
        self.low_disk = False
        self.cpu = 0.0
        self.ram = 0.0
        self.free_gb = util.free_gb(self.cfg["clips_dir"])
        self.update: tuple[str, str] | None = None
        self._cpu_hot = 0

    # --- события для окна --------------------------------------------------
    def say(self, text: str, level: str = "info") -> None:
        log.info(text)
        self.events.put({"t": "log", "text": text, "level": level, "time": time.strftime("%H:%M:%S")})

    def _on_tg(self, ev: dict) -> None:
        if ev["t"] == "uploaded":
            self.stats["sent"] = self.stats.get("sent", 0) + 1
            self._save_stats()
            self.say(f"Отправлено в Telegram: {os.path.basename(ev['path'])} ({ev['size'] / 1e6:.0f} МБ)")
        elif ev["t"] == "upload_failed":
            self.say(f"Не удалось отправить {os.path.basename(ev['path'])}: {ev['error']}", "error")
        elif ev["t"] == "reaction":
            if self.learner.label_path(ev["path"], ev["good"], "telegram"):
                self.say(f"Оценка из Telegram: {'👍' if ev['good'] else '👎'} {os.path.basename(ev['path'])}")
                self._learning_changed()
        elif ev["t"] == "tg":
            self.say(f"Telegram: {ev['text']}", "error" if ev["status"] == "error" else "info")

    def label_clip(self, clip_id: str, good: bool | None) -> None:
        """Оценка клипа из окна программы. После неё всё переобучается и сразу применяется."""
        if self.learner.label(clip_id, good, "app"):
            self._learning_changed()

    def _learning_changed(self) -> None:
        with self.lock:
            for h in self.workers.values():
                self._send(h, cmd="reload")
        self.events.put({"t": "learned"})

    # --- статистика за день (для вечерней сводки) -----------------------
    def _stats_path(self) -> Path:
        return config.data_dir() / "stats.json"

    def _load_stats(self) -> dict:
        try:
            with open(self._stats_path(), "r", encoding="utf-8") as f:
                s = json.load(f)
        except Exception:
            s = {}
        if s.get("date") != time.strftime("%Y-%m-%d"):
            s = {"date": time.strftime("%Y-%m-%d"), "made": {}, "sent": 0, "summary_sent": False}
        return s

    def _save_stats(self) -> None:
        try:
            with open(self._stats_path(), "w", encoding="utf-8") as f:
                json.dump(self.stats, f, ensure_ascii=False)
        except OSError:
            pass

    # --- запуск/остановка -----------------------------------------------
    def _check_clips_dir(self, cfg: dict) -> bool:
        """Папка клипов может оказаться недоступной (вынули внешний диск). Программа из-за этого падать не должна."""
        try:
            os.makedirs(cfg["clips_dir"], exist_ok=True)
            return True
        except OSError as e:
            self.say(f"Папка для клипов недоступна ({cfg['clips_dir']}): {e}. Пока она не вернётся, клипы "
                     f"сохраняются в {config.default_clips_dir()}", "error")
            return False

    def _clean_buffers(self) -> None:
        """Убирает записи каналов, которых уже нет в списке: они больше никому не нужны, а место занимают."""
        try:
            for d in config.subdir("buffer").iterdir():
                if d.is_dir() and d.name not in self.workers:
                    shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass

    def start(self) -> None:
        self._check_clips_dir(self.cfg)
        for name in self.cfg["channels"]:
            self.workers[name] = WorkerHandle(name)
        self._clean_buffers()
        for job in sorted(glob.glob(str(config.subdir("candidates") / "*.json"))):
            if job.endswith(".vr.json"):                # служебный файл недоделанного монтажа, а не заготовка клипа
                try:
                    os.remove(job)
                except OSError:
                    pass
                continue
            self._enqueue(job, *job_rank(job))          # недоделанное с прошлого запуска
        self.tg.start(self.cfg)
        system.prevent_sleep(bool(self.cfg.get("prevent_sleep")))
        for target, name in ((self._monitor_loop, "monitor"), (self._edit_loop, "editor"),
                             (self._guard_loop, "guard"), (self._update_loop, "updates")):
            threading.Thread(target=target, daemon=True, name=name).start()
        self.say(f"Запущено. Каналов: {len(self.workers)}")

    def shutdown(self) -> None:
        self.stop.set()
        with self.lock:
            names = list(self.workers)
            self._stop_all(list(self.workers.values()))
        self.tg.stop()
        system.prevent_sleep(False)
        # После выхода запись в буфере уже не пригодится, а это до 350 МБ на каждый стрим.
        # ffmpeg дописывает последний кусок ещё мгновение после остановки, поэтому две попытки.
        for attempt in range(2):
            for name in names:
                segments.wipe(config.subdir("buffer") / name)
            if attempt == 0:
                time.sleep(0.5)

    def _spawn(self, h: WorkerHandle) -> None:
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        try:
            h.proc = subprocess.Popen(util.worker_cmd("worker", h.name), stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                                      text=True, encoding="utf-8", errors="replace", bufsize=1,
                                      creationflags=util.NO_WINDOW)
        except OSError as e:
            h.status = {"state": "error", "detail": f"не запускается: {e}"}
            h.next_start = time.time() + 30
            return
        h.started = h.last_msg = time.time()
        h.status = {"state": "starting"}
        threading.Thread(target=self._read_worker, args=(h, h.proc), daemon=True, name=f"rd-{h.name}").start()

    def _read_worker(self, h: WorkerHandle, proc: subprocess.Popen) -> None:
        try:
            for line in proc.stdout:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                h.last_msg = time.time()
                t = msg.get("t")
                if t == "status":
                    h.status = msg
                    if msg.get("state") == "live":
                        h.backoff = 3.0
                elif t == "candidate":
                    self.say(f"{h.name}: пойман момент" + (f" (оценка {msg['score']})" if msg.get("score") else "")
                             + ", монтирую…")
                    self._enqueue(msg["job"], msg.get("score") or 0, msg.get("kind") == "manual")
                elif t == "event":
                    k = msg.get("kind")
                    if k == "online":
                        back = h.off_at
                        h.off_at = 0.0
                        if not h.online:
                            h.online = True
                            h.live_since = time.time()
                            self.say(f"{h.name}: стрим в эфире, качество {msg.get('quality', '?')}")
                        elif back:                      # короткий обрыв: эфир не кончался, человека не дёргаем
                            log.info("%s: видео прерывалось на %d с и вернулось", h.name, time.time() - back)
                    elif k == "offline":
                        # Обрыв связи выглядит так же, как конец эфира. Поэтому сначала ждём: вернётся ли стрим.
                        if h.online and not h.off_at:
                            h.off_at = time.time()
                    elif k == "slow_net":
                        if msg.get("new"):
                            self.say(f"{h.name}: интернет не успевает за качеством {msg.get('old', '?')}, дальше "
                                     f"записываю в {msg['new']}", "error")
                        else:
                            self.say(f"{h.name}: интернет не успевает даже за самым низким качеством. "
                                     "Убери часть стримов", "error")
                        self.tg.alert("📶 Интернет не успевает записывать все стримы сразу. Программа сама снижает "
                                      "качество записи. Если так бывает часто, поставь в настройках качество пониже "
                                      "или убери часть стримов.", "slow-net", 3600)
                    elif k == "no_video":
                        self.say(f"{h.name}: клип не сделан, в буфере пока нет видео", "error")
                    elif k == "disk_full":
                        self.say("Диск почти заполнен, клип пропущен", "error")
        except Exception:
            log.exception("чтение вывода %s", h.name)

    def _stop_worker(self, h: WorkerHandle, wait: bool = True) -> subprocess.Popen | None:
        p, h.proc = h.proc, None
        if not p:
            return None
        try:
            p.stdin.write('{"cmd":"stop"}\n')
            p.stdin.flush()
            p.stdin.close()
        except (OSError, ValueError):
            pass
        if wait:
            self._reap(p, 6.0)
        return p

    @staticmethod
    def _reap(p: subprocess.Popen, timeout: float) -> None:
        try:
            p.wait(timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            try:
                p.wait(3)
            except subprocess.TimeoutExpired:
                pass

    def _stop_all(self, handles: list[WorkerHandle]) -> None:
        """Останавливает несколько процессов записи разом: всем команда, потом общее ожидание.
        По очереди выход из программы с шестью стримами занимал бы до полуминуты."""
        procs = [p for p in (self._stop_worker(h, wait=False) for h in handles) if p]
        deadline = time.time() + 6
        for p in procs:
            self._reap(p, max(0.2, deadline - time.time()))

    def _send(self, h: WorkerHandle, **cmd) -> bool:
        try:
            h.proc.stdin.write(json.dumps(cmd) + "\n")
            h.proc.stdin.flush()
            return True
        except (OSError, ValueError, AttributeError):
            return False

    def _monitor_loop(self) -> None:
        while not self.stop.wait(1.0):
            now = time.time()
            with self.lock:
                for h in list(self.workers.values()):
                    try:
                        self._check(h, now)
                    except Exception:
                        log.exception("monitor %s", h.name)

    def stop_recording(self) -> None:
        """Останавливает запись всех каналов, а монтаж уже пойманных моментов продолжается.
        Нужно проверочному запуску: иначе новые моменты приходят быстрее, чем кончается очередь."""
        self.recording_off = True
        with self.lock:
            handles = list(self.workers.values())
            self._stop_all(handles)
            for h in handles:
                h.status = {"state": "stopped"}

    def _check(self, h: WorkerHandle, now: float) -> None:
        if self.stop.is_set() or self.recording_off:
            return                                      # программа закрывается: никого не перезапускаем
        if h.off_at and now - h.off_at >= OFFLINE_CONFIRM:
            h.off_at, h.online = 0.0, False             # эфир так и не вернулся: теперь это точно конец
            self.say(f"{h.name}: стрим закончился")
            self.tg.alert(f"📴 {h.name}: стрим закончился. Жду следующего.", f"off-{h.name}", 600)
        if h.proc is None:
            if now >= h.next_start:
                self._spawn(h)
            return
        code = h.proc.poll()
        hung = now - h.last_msg > HANG_SECONDS
        if code is None and not hung:
            st = h.status.get("state")
            if st == "error":
                h.error_since = h.error_since or now
                if now - h.error_since > 300:
                    self.tg.alert(f"⚠️ {h.name}: уже 5 минут не получается открыть стрим.\n"
                                  f"{h.status.get('detail', '')}", f"err-{h.name}", 3600)
            else:
                h.error_since = 0.0
            return
        if hung and code is None:
            self.say(f"{h.name}: процесс завис, перезапускаю", "error")
            h.proc.kill()
            self._reap(h.proc, 3.0)
        else:
            self.say(f"{h.name}: процесс неожиданно завершился (код {code}), перезапускаю", "error")
        h.proc = None
        h.restarts += 1
        h.status = {"state": "restarting"}
        h.next_start = now + h.backoff
        h.backoff = min(h.backoff * 2, 60.0)
        if h.restarts % 5 == 0:
            self.tg.alert(f"⚠️ {h.name}: запись перезапускалась уже {h.restarts} раз. Загляни в программу.",
                          f"restart-{h.name}", 3600)

    # --- команды из окна --------------------------------------------------
    def add_channel(self, text: str) -> tuple[bool, str]:
        name = config.parse_channel(text)
        if not name:
            return False, "Не похоже на ссылку Twitch. Пример: https://www.twitch.tv/имя_канала"
        with self.lock:
            if name in self.workers:
                return False, "Этот канал уже в списке"
            if len(self.workers) >= int(self.cfg.get("max_streams", 6)):
                return False, f"Лимит {self.cfg['max_streams']} стримов одновременно (меняется в настройках)"
            self.workers[name] = WorkerHandle(name)
            self.cfg["channels"] = list(self.workers)
            config.save(self.cfg)
        self.say(f"Добавлен канал {name}")
        return True, name

    def remove_channel(self, name: str) -> None:
        with self.lock:
            h = self.workers.pop(name, None)
            self.cfg["channels"] = list(self.workers)
            config.save(self.cfg)
        if h:
            self._stop_worker(h)
            segments.wipe(config.subdir("buffer") / name)
            self.say(f"Канал {name} удалён")

    def clip_now(self, name: str) -> bool:
        with self.lock:
            h = self.workers.get(name)
            ok = bool(h and h.proc and self._send(h, cmd="clip_now"))
        self.say(f"{name}: делаю клип последних {self.cfg['manual_len']} секунд" if ok
                 else f"{name}: сейчас не записывается", "info" if ok else "error")
        return ok

    def apply_config(self, cfg: dict) -> None:
        with self.lock:
            cfg["channels"] = list(self.workers)
            restart = any(self.cfg.get(k) != cfg.get(k) for k in ("quality", "twitch_oauth"))
            self.cfg = cfg
            config.save(cfg)
            self._check_clips_dir(cfg)
            if restart:
                self._stop_all(list(self.workers.values()))
            for h in self.workers.values():
                if restart:
                    h.next_start = 0
                else:
                    self._send(h, cmd="reload")
        self.tg.reconfigure(cfg)
        system.prevent_sleep(bool(cfg.get("prevent_sleep")))
        system.set_autostart(bool(cfg.get("autostart")))
        self.say("Настройки сохранены")

    def set_paused(self, paused: bool) -> None:
        cfg = dict(self.cfg, paused=paused)
        self.apply_config(cfg)
        self.say("Запись на паузе" if paused else "Запись возобновлена")

    def snapshot(self) -> list[dict]:
        out = []
        with self.lock:
            for h in self.workers.values():
                s = h.status
                state = s.get("state", "starting")
                text = STATE_RU.get(state, state)
                if state == "error" and s.get("detail"):
                    text += ": " + s["detail"][:60]
                slow = bool(s.get("slow")) and state in ("live", "pause")
                if slow:
                    text = "интернет не успевает"
                elif state == "live" and s.get("hot"):
                    text = "🔥 движ в чате!"
                out.append({"name": h.name, "state": state, "text": text, "slow": slow, "rate": s.get("rate", 0),
                            "level": s.get("level", 0), "buf_mb": s.get("buf_mb", 0), "clips": h.clips,
                            "quality": s.get("quality", ""), "title": s.get("title", ""),
                            "chat": s.get("chat", False), "bar": s.get("bar", 0), "skips": s.get("skips") or {}})
        return out

    # --- монтаж -----------------------------------------------------------
    def _enqueue(self, job: str, score: int = 0, manual: bool = False) -> None:
        """Ставит заготовку на монтаж. Если очередь переполнена, самые слабые моменты из неё пропускаются."""
        try:
            score = int(score or 0)
        except (TypeError, ValueError):
            score = 0
        for lost, lost_score in self.edit_q.put(job, score, manual):
            self._drop_job(lost)
            self.edit_fail.pop(lost, None)
            ch = os.path.basename(lost).rsplit("_", 3)[0]
            text = f"Монтаж не успевает за стримами: самый слабый момент пропущен ({ch}, оценка {lost_score})"
            now = time.time()
            if now - self._drop_hint > 1800:            # совет показываем изредка, чтобы не засорять журнал
                self._drop_hint = now
                text += ". Если так бывает часто, понизь чувствительность в настройках"
            self.say(text)

    def _edit_loop(self) -> None:
        while not self.stop.is_set():
            try:
                job, score, manual = self.edit_q.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self._edit_one(job, score, manual)
            except Exception:
                log.exception("монтаж %s", job)
            finally:
                self.editing = ""
                self.edit_q.done()

    def _edit_one(self, job: str, score: int, manual: bool) -> None:
        if not os.path.exists(job):
            return
        if self.low_disk:
            self.say("Мало места на диске: клип пропущен", "error")
            self._drop_job(job)
            return
        self.editing = os.path.basename(job)
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        if self.edit_fail.get(job):
            # Первая попытка не удалась. Чаще всего виновато распознавание речи (нет памяти, зависла загрузка
            # модели, сбой библиотеки), поэтому вторая идёт без него и без ИИ: клип важнее описания.
            env["STREAMCLIPPER_SAFE_EDIT"] = "1"
        try:
            r = subprocess.run(util.worker_cmd("edit", job), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, timeout=1500,
                               creationflags=util.NO_WINDOW | util.LOW_PRIORITY, env=env)
            lines = r.stdout.decode("utf-8", "replace").strip().splitlines()
            res = json.loads(lines[-1]) if lines else {"ok": False, "error": "монтажёр ничего не ответил"}
        except subprocess.TimeoutExpired:
            res = {"ok": False, "error": "монтаж шёл дольше 25 минут"}
        except Exception as e:
            res = {"ok": False, "error": str(e)[:200]}
        self.editing = ""
        self._edit_done(job, res, score, manual)

    def _drop_job(self, job: str) -> None:
        for p in (job, job[:-5] + ".ts"):
            try:
                os.remove(p)
            except OSError:
                pass
        try:                                            # момент не стал клипом: в списке клипов он не нужен
            self.learner.drop("_".join(os.path.basename(job)[:-5].rsplit("_", 3)[1:]))
        except Exception:
            log.exception("не убрал пропущенный момент из базы обучения")

    def _edit_done(self, job: str, res: dict, score: int = 0, manual: bool = False) -> None:
        if not res.get("ok"):
            n = self.edit_fail.get(job, 0) + 1
            self.edit_fail[job] = n
            if n < 2 and not res.get("fatal"):
                self.say(f"Монтаж не удался ({res.get('error')}), пробую ещё раз", "error")
                self._enqueue(job, score, manual)
            else:
                self.say(f"Клип не получился: {res.get('error')}", "error")
                self._drop_job(job)
                self.edit_fail.pop(job, None)
            return
        self.edit_fail.pop(job, None)
        if res.get("skipped"):
            self.say(f"Момент пропущен: {res['skipped']}")
            self._drop_job(job)
            return
        ch = res["channel"]
        with self.lock:
            if ch in self.workers:
                self.workers[ch].clips += 1
        if self.stats.get("date") != time.strftime("%Y-%m-%d"):
            self.stats = self._load_stats()
        self.stats["made"][ch] = self.stats["made"].get(ch, 0) + 1
        self._save_stats()
        self.say(f"✂️ {ch}: клип готов, {res['dur']:.0f} с, оценка {res['score']}: {os.path.basename(res['clip'])}")
        self.events.put({"t": "clip", **res})
        if self.cfg.get("tg_enabled"):
            self.tg.enqueue(res["clip"], res["caption"])
            if res.get("extra"):
                self.tg.enqueue(res["extra"], res["caption"].split("\n")[0] + "\n🖥 обычная версия 16:9, как на стриме")

    # --- охрана ресурсов --------------------------------------------------
    def _guard_loop(self) -> None:
        try:
            import psutil
            psutil.cpu_percent(None)
        except Exception:
            psutil = None
        n = 0
        while not self.stop.wait(10):
            n += 1
            try:
                cfg = self.cfg
                self.free_gb = min(util.free_gb(cfg["clips_dir"]), util.free_gb(config.data_dir()))
                low = self.free_gb < float(cfg.get("min_free_gb", 5))
                if low and not self.low_disk:
                    self.say(f"На диске осталось {self.free_gb:.1f} ГБ. Новые клипы не сохраняю, пока не освободится место",
                             "error")
                    self.tg.alert(f"💽 На диске осталось {self.free_gb:.1f} ГБ. Клипы не сохраняются, освободи место.",
                                  "disk", 3600)
                self.low_disk = low
                if psutil:
                    self.cpu = psutil.cpu_percent(None)
                    self.ram = psutil.virtual_memory().percent
                    self._cpu_hot = self._cpu_hot + 1 if (self.cpu > 93 or self.ram > 93) else 0
                    if self._cpu_hot >= 18:                         # 3 минуты подряд
                        self._cpu_hot = 0
                        self.say("Компьютер перегружен. Убери один стрим или понизь качество записи", "error")
                        self.tg.alert(f"🥵 Компьютер перегружен (процессор {self.cpu:.0f}%, память {self.ram:.0f}%). "
                                      "Убери один стрим или понизь качество записи.", "cpu", 3600)
                if n % 30 == 1:
                    self._trim_clips()
                    self._clean_candidates()
                self._daily_summary()
            except Exception:
                log.exception("guard")

    def _trim_clips(self) -> None:
        """Папка клипов не растёт бесконечно: сверх лимита удаляются самые старые уже отправленные."""
        cap = float(self.cfg.get("max_clips_gb", 30)) * 1e9
        if cap <= 0:
            return
        files = []
        for root, _d, names in os.walk(self.cfg["clips_dir"]):
            for nme in names:
                p = os.path.join(root, nme)
                if util.own_clip(p):                        # чужие видео в этой папке не считаем и не трогаем
                    try:
                        st = os.stat(p)
                        files.append((st.st_mtime, st.st_size, p))
                    except OSError:
                        pass
        total = sum(f[1] for f in files)
        if total <= cap:
            return
        tg_on = bool(self.cfg.get("tg_enabled"))
        removed = 0
        for _mt, size, p in sorted(files):
            if total <= cap * 0.9:
                break
            if tg_on and self.tg.queue.has_pending(p):
                continue                                    # ещё не отправлен: не трогаем
            if util.remove_clip(p):
                total -= size
                removed += 1
        if removed:
            self.say(f"Папка клипов превысила лимит: удалил самые старые ({removed})")

    def _clean_candidates(self) -> None:
        d = config.subdir("candidates")
        now = time.time()
        for p in d.iterdir():
            try:
                if now - p.stat().st_mtime < 3600:
                    continue
                orphan_raw = p.suffix == ".ts" and not p.with_suffix(".json").exists()
                if orphan_raw or p.name.endswith((".vr.json", ".tmp")):     # остатки прерванного монтажа
                    p.unlink()
            except OSError:
                pass

    def _daily_summary(self) -> None:
        if self.stats.get("date") != time.strftime("%Y-%m-%d"):
            self.stats = self._load_stats()
        if self.stats.get("summary_sent") or time.localtime().tm_hour < 23:
            return
        self.stats["summary_sent"] = True
        self._save_stats()
        made = self.stats.get("made", {})
        if not made:
            return
        lines = [f"• {ch}: {n}" for ch, n in sorted(made.items(), key=lambda x: -x[1])]
        c = self.tg.queue.counts()
        self.tg.alert("📊 Итоги дня\nКлипов сделано: " + str(sum(made.values())) + "\n" + "\n".join(lines)
                      + f"\nОтправлено: {self.stats.get('sent', 0)}, в очереди: {c.get('pending', 0)}"
                      + f"\nСвободно на диске: {self.free_gb:.0f} ГБ")

    def _update_loop(self) -> None:
        if self.stop.wait(20):
            return
        while True:
            try:
                self.update = updater.check_app_update()
                if self.update:
                    self.events.put({"t": "update", "version": self.update[0], "url": self.update[1]})
                if self.cfg.get("auto_update_streamlink", True):
                    new = updater.update_streamlink()
                    if new:
                        self.say(f"Модуль записи Twitch обновлён до {new}. Применится при следующем запуске стрима")
            except Exception as e:
                log.info("проверка обновлений: %s", e)
            if self.stop.wait(12 * 3600):
                return
