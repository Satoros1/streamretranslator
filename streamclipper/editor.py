"""Монтаж одного клипа из заготовки. Запускается отдельным процессом на каждый момент.

Вход: заготовка (до ~3 минут видео вокруг реакции чата) и её описание в JSON.
Выход: один непрерывный клип без склеек, файл с описанием и строка JSON в stdout.
Обычный клип 16:9 не перекодируется (копируется как есть): качество не теряется, процессор почти не занят.
Вертикальный клип для TikTok собирается заново в 1080x1920, при желании с чатом стрима.
"""
from __future__ import annotations

import array
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

from . import chatrender, config, describe, util
from .learning import Learner

log = logging.getLogger("editor")
FRAME = 0.25


def loudness(raw: str) -> list[float]:
    """Громкость по четвертям секунды. Нужна, чтобы не резать на полуслове."""
    try:
        r = util.run([util.find_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", raw, "-vn", "-ac", "1",
                      "-ar", "8000", "-f", "s16le", "pipe:1"], timeout=180)
        pcm = array.array("h")
        pcm.frombytes(r.stdout[: len(r.stdout) // 2 * 2])
        if sys.byteorder == "big":
            pcm.byteswap()
        n = int(8000 * FRAME)
        out = []
        for i in range(0, len(pcm) - n + 1, n):
            chunk = pcm[i:i + n]
            out.append((sum(x * x for x in chunk) / n) ** 0.5)
        return out
    except Exception as e:
        log.warning("не смог измерить громкость: %s", e)
        return []


def snap(t: float, loud: list[float], reach: float = 2.0) -> float:
    """Сдвигает точку реза в ближайшую паузу в речи (в пределах reach секунд)."""
    if not loud:
        return t
    lo = max(0, int((t - reach) / FRAME))
    hi = min(len(loud) - 1, int((t + reach) / FRAME))
    if hi <= lo:
        return t
    best = min(range(lo, hi + 1), key=lambda i: loud[i] + abs(i * FRAME - t) * 15)
    return best * FRAME + FRAME / 2


def choose_window(job: dict, cfg: dict) -> tuple[float, float]:
    """Один непрерывный отрезок вокруг пика: подводка, момент, реакция. Без склеек."""
    lo = max(job.get("win_start", 0.0), job.get("not_before", 0.0))
    hi = job.get("win_end", job["raw_dur"])
    cmin, cmax = float(cfg["clip_min"]), float(max(cfg["clip_max"], cfg["clip_min"]))
    if job.get("kind") == "manual":
        return max(lo, hi - float(cfg.get("manual_len", 45))), hi
    start = job["onset"] - float(cfg["lead_in"])
    end = job["end"] + float(cfg["tail"])
    if end - start > cmax:
        end = start + cmax
        if job["peak"] > end - 5:                 # пик не влез: двигаем окно так, чтобы он был на 2/3
            start = job["peak"] - cmax * 0.65
            end = start + cmax
    if end - start < cmin:
        add = cmin - (end - start)
        start -= add * 0.6
        end += add * 0.4
    if start < lo:
        end += lo - start
        start = lo
    if end > hi:
        start = max(lo, start - (end - hi))
        end = hi
    return start, end


def min_length(cfg: dict, manual: bool) -> float:
    """Короче этого клип не сохраняется. Клип по кнопке человек попросил сам, с ним строгость ни к чему."""
    return 8.0 if manual else max(12.0, 0.6 * float(cfg["clip_min"]))


def cut(raw: str, start: float, dur: float, out: str) -> bool:
    """Клип как на стриме: без перекодирования."""
    cmd = [util.find_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-i", raw,
           "-t", f"{dur:.3f}", "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-avoid_negative_ts", "make_zero",
           "-movflags", "+faststart", out]
    r = util.run(cmd, timeout=900)
    if r.returncode != 0:
        log.warning("ffmpeg: %s", r.stderr.decode("utf-8", "replace")[-500:])
    return r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 50_000


def cut_vertical(raw: str, start: float, dur: float, out: str, job: dict, cfg: dict, headline: str = "") -> bool:
    """Вертикальный клип 1080x1920. Чат и заголовок рисуются в отдельном процессе: если с ними что-то не так,
    клип выйдет без них."""
    from . import vrender
    spec = {"raw": raw, "start": start, "dur": dur, "out": out, "layout": cfg.get("tt_layout", "big"),
            "style": cfg.get("chat_style", "soft"), "size": cfg.get("chat_size", "m"),
            "room_id": job.get("room_id", ""), "chat": [], "headline": headline}
    if spec["style"] != "off":
        spec["chat"] = chatrender.select(job.get("chatlog") or [], start, dur, chatrender.hide_list(cfg.get("chat_hide", "")))
    if spec["chat"] or spec["headline"]:
        path = raw[:-3] + ".vr.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(spec, f, ensure_ascii=False)
            r = util.run(util.worker_cmd("vrender", path), timeout=600)
            if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 50_000:
                return True
            log.warning("чат и заголовок поверх клипа не получились (код %s), делаю клип без них: %s", r.returncode,
                        r.stderr.decode("utf-8", "replace")[-300:])
        except Exception as e:
            log.warning("чат и заголовок поверх клипа не получились, делаю клип без них: %s", e)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        spec["chat"], spec["headline"] = [], ""
    return vrender.render(spec)


def probe_duration(path: str) -> float:
    try:
        r = util.run([util.find_tool("ffprobe"), "-v", "error", "-show_entries", "format=duration",
                      "-of", "csv=p=0", path], timeout=60)
        return float(r.stdout.decode().strip() or 0)
    except Exception:
        return 0.0


def clip_folder(cfg: dict, channel: str, day: str) -> Path:
    """Папка для клипа. Если выбранная в настройках недоступна (вынули диск), момент не теряем:
    клип ложится в папку по умолчанию."""
    last: OSError | None = None
    for root in dict.fromkeys((cfg["clips_dir"], config.default_clips_dir())):
        folder = Path(root) / channel / day
        try:
            folder.mkdir(parents=True, exist_ok=True)
            return folder
        except OSError as e:
            last = e
            log.warning("папка клипов %s недоступна: %s", root, e)
    raise last or OSError("нет папки для клипов")


def process(job_path: str) -> dict:
    cfg = config.load()
    if os.environ.get("STREAMCLIPPER_SAFE_EDIT"):
        # вторая попытка после сбоя: без распознавания речи и без ИИ, лишь бы клип вышел
        cfg = dict(cfg, whisper=False, ai_enabled=False)
        log.info("повторный монтаж в щадящем режиме: без распознавания речи и ИИ")
    with open(job_path, "r", encoding="utf-8") as f:
        job = json.load(f)
    raw = job["raw"]
    if not os.path.exists(raw):
        return {"ok": False, "error": "нет файла заготовки", "fatal": True}
    real = probe_duration(raw)
    if real > 1:
        job["raw_dur"] = min(job["raw_dur"], real) if job["raw_dur"] > 1 else real
        job["win_end"] = min(job.get("win_end", real), real)

    loud = loudness(raw)
    start, end = choose_window(job, cfg)
    lo = max(job.get("win_start", 0.0), job.get("not_before", 0.0))
    hi = job.get("win_end", job["raw_dur"])
    manual = job.get("kind") == "manual"
    shortest = min_length(cfg, manual)
    if end - start < shortest:
        return {"ok": True, "skipped": "слишком короткий кусок (рядом прошлый клип, реклама или дыра в записи)"}
    if not manual and not (start <= job["peak"] <= end + 1):
        return {"ok": True, "skipped": "сам момент не попал в запись"}

    transcript = describe.transcribe(raw, max(lo, start - 12), min(hi, end + 6), cfg)
    title, quote = describe.heuristic(job, transcript)
    desc, score = "", int(job.get("score", 0))
    if loud and job.get("kind") != "manual":           # громкий смех/крик в кадре добавляет очков
        seg = loud[int(start / FRAME): int(end / FRAME)] or [0]
        med = sorted(loud)[len(loud) // 2] or 1.0
        if max(seg) > med * 4:
            score = min(100, score + 6)

    verdict = describe.ai_verdict(job, transcript, lo, hi, (start, end), cfg)
    if verdict and manual:
        # клип вырезан кнопкой: его границы и судьбу решил человек, от ИИ берём только слова
        title, desc = verdict["title"] or title, verdict["description"]
    elif verdict:
        if not verdict["keep"] and cfg.get("ai_filter"):
            return {"ok": True, "skipped": "ИИ решил, что момент не смешной"}
        # оценка ИИ и выученная оценка усредняются: ни одна не решает в одиночку
        title, desc, score = verdict["title"] or title, verdict["description"], round((verdict["score"] + score) / 2)
        s, e = max(lo, verdict["start"]), min(hi, verdict["end"])
        if cfg["clip_min"] * 0.7 <= e - s <= cfg["clip_max"] * 1.2 and s < job["peak"] < e + 5:
            start, end = s, e
    head = describe.headline(job, title, bool(verdict and verdict["title"]), cfg)
    if job.get("kind") != "manual" and score < int(cfg.get("min_score", 0)):
        return {"ok": True, "skipped": f"оценка {score} ниже порога"}

    if job.get("kind") != "manual":
        start = max(lo, snap(start, loud))
        end = min(hi, snap(end, loud))
    dur = end - start

    when = time.localtime(job["raw_start"] + start)
    folder = clip_folder(cfg, job["channel"], time.strftime("%Y-%m-%d", when))
    safe_title = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", title).strip()[:50]      # всё, что Windows не терпит в именах
    name = f"{job['channel']}_{time.strftime('%Y-%m-%d_%H-%M-%S', when)}_{score:03d}_{safe_title}".rstrip(" ._")
    fmt = cfg.get("clip_format", "tiktok")
    wide, tall = str(folder / (name + ".mp4")), str(folder / (name + "_9x16.mp4"))
    made_wide = fmt in ("original", "both") and cut(raw, start, dur, wide)
    made_tall = fmt != "original" and cut_vertical(raw, start, dur, tall, job, cfg, head)
    if not made_tall and not made_wide:                  # вертикальный не собрался: момент всё равно не теряем
        made_wide = cut(raw, start, dur, wide)
    if not made_tall and not made_wide:
        return {"ok": False, "error": "ffmpeg не смог вырезать клип"}
    out = tall if made_tall else wide                    # главный файл клипа; второй (если есть) идёт довеском
    extra = wide if made_tall and made_wide else None
    real_dur = probe_duration(out) or dur
    if real_dur < min(shortest, dur * 0.6):
        # В заготовке оказалось меньше видео, чем обещано: запись отставала от эфира. Обрубок в 2 секунды
        # никому не нужен.
        for p in (out, extra):
            if p:
                try:
                    os.remove(p)
                except OSError:
                    pass
        return {"ok": True, "skipped": f"в записи оказалось всего {real_dur:.0f} с видео вместо {dur:.0f}"}
    text = describe.caption(job, title, desc, quote, score, real_dur, time.strftime("%d.%m %H:%M", when))
    try:
        with open(str(folder / (name + ".txt")), "w", encoding="utf-8") as f:
            f.write(text + "\n")
            if transcript:
                f.write("\n--- что говорил стример ---\n" + "\n".join(t for _a, _b, t in transcript) + "\n")
            chat = chatrender.chat_text(job.get("chatlog") or [], start, dur)
            if chat:
                f.write("\n--- чат в этот момент ---\n" + chat + "\n")
    except OSError:
        pass
    try:
        Learner().set_clip(job["id"], out, title, desc or quote, score)
    except Exception:
        log.exception("не записал клип в базу обучения")
    for p in (raw, job_path):
        try:
            os.remove(p)
        except OSError:
            pass
    return {"ok": True, "clip": out, "extra": extra, "caption": text, "score": score, "channel": job["channel"],
            "dur": round(real_dur, 1), "title": title, "kind": job.get("kind")}


def main(job_path: str) -> int:
    util.setup_logging("editor")
    try:
        res = process(job_path)
        if res.get("skipped"):
            try:
                with open(job_path, "r", encoding="utf-8") as f:
                    Learner().drop(json.load(f)["id"])
            except Exception:
                pass
    except Exception as e:
        log.exception("монтаж упал")
        res = {"ok": False, "error": str(e)[:300]}
    sys.stdout.write(json.dumps(res) + "\n")
    sys.stdout.flush()
    return 0 if res.get("ok") else 1
