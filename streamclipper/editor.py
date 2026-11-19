"""Монтаж одного клипа из заготовки. Запускается отдельным процессом на каждый момент.

Вход: заготовка (до ~3 минут видео вокруг реакции чата) и её описание в JSON.
Выход: один непрерывный клип без склеек, файл с описанием и строка JSON в stdout.
Видео не перекодируется (копируется как есть), поэтому качество не теряется и процессор почти не занят.
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

from . import config, describe, util
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


def cut(raw: str, start: float, dur: float, out: str, vertical: bool = False) -> bool:
    ff = util.find_tool("ffmpeg")
    base = [ff, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-i", raw, "-t", f"{dur:.3f}"]
    if vertical:
        # 9:16: исходное видео целиком по центру, сверху и снизу размытый фон из него же
        vf = ("split[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
              "boxblur=24:2[bg];[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p")
        cmd = base + ["-filter_complex", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                      "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out]
    else:
        cmd = base + ["-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-avoid_negative_ts", "make_zero",
                      "-movflags", "+faststart", out]
    r = util.run(cmd, timeout=900)
    if r.returncode != 0:
        log.warning("ffmpeg: %s", r.stderr.decode("utf-8", "replace")[-500:])
    return r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 50_000


def probe_duration(path: str) -> float:
    try:
        r = util.run([util.find_tool("ffprobe"), "-v", "error", "-show_entries", "format=duration",
                      "-of", "csv=p=0", path], timeout=60)
        return float(r.stdout.decode().strip() or 0)
    except Exception:
        return 0.0


def process(job_path: str) -> dict:
    cfg = config.load()
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
    if end - start < 8:
        return {"ok": True, "skipped": "слишком короткий кусок (пересекается с прошлым клипом или рекламой)"}

    transcript = describe.transcribe(raw, max(lo, start - 12), min(hi, end + 6), cfg)
    title, quote = describe.heuristic(job, transcript)
    desc, score = "", int(job.get("score", 0))
    if loud and job.get("kind") != "manual":           # громкий смех/крик в кадре добавляет очков
        seg = loud[int(start / FRAME): int(end / FRAME)] or [0]
        med = sorted(loud)[len(loud) // 2] or 1.0
        if max(seg) > med * 4:
            score = min(100, score + 6)

    verdict = describe.ai_verdict(job, transcript, lo, hi, (start, end), cfg) if job.get("kind") != "manual" else None
    if verdict:
        if not verdict["keep"] and cfg.get("ai_filter"):
            return {"ok": True, "skipped": "ИИ решил, что момент не смешной"}
        # оценка ИИ и выученная оценка усредняются: ни одна не решает в одиночку
        title, desc, score = verdict["title"] or title, verdict["description"], round((verdict["score"] + score) / 2)
        s, e = max(lo, verdict["start"]), min(hi, verdict["end"])
        if cfg["clip_min"] * 0.7 <= e - s <= cfg["clip_max"] * 1.2 and s < job["peak"] < e + 5:
            start, end = s, e
    if job.get("kind") != "manual" and score < int(cfg.get("min_score", 0)):
        return {"ok": True, "skipped": f"оценка {score} ниже порога"}

    if job.get("kind") != "manual":
        start = max(lo, snap(start, loud))
        end = min(hi, snap(end, loud))
    dur = end - start

    when = time.localtime(job["raw_start"] + start)
    folder = Path(cfg["clips_dir"]) / job["channel"] / time.strftime("%Y-%m-%d", when)
    folder.mkdir(parents=True, exist_ok=True)
    safe_title = re.sub(r'[\\/:*?"<>|\r\n]+', " ", title).strip()[:50]
    name = f"{job['channel']}_{time.strftime('%Y-%m-%d_%H-%M-%S', when)}_{score:03d}_{safe_title}".rstrip(" ._")
    out = str(folder / (name + ".mp4"))
    if not cut(raw, start, dur, out):
        return {"ok": False, "error": "ffmpeg не смог вырезать клип"}
    real_dur = probe_duration(out) or dur
    vert = None
    if cfg.get("vertical"):
        v = str(folder / (name + "_9x16.mp4"))
        if cut(raw, start, dur, v, vertical=True):
            vert = v
    text = describe.caption(job, title, desc, quote, score, real_dur, time.strftime("%d.%m %H:%M", when))
    try:
        with open(str(folder / (name + ".txt")), "w", encoding="utf-8") as f:
            f.write(text + "\n")
            if transcript:
                f.write("\n--- что говорил стример ---\n" + "\n".join(t for _a, _b, t in transcript) + "\n")
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
    return {"ok": True, "clip": out, "vertical": vert, "caption": text, "score": score, "channel": job["channel"],
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
