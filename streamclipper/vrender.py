"""Вертикальный клип 9:16 для TikTok, Shorts и Reels: 1080x1920, H.264 + AAC.

Три вида кадра:
  big   как в TikTok-нарезках: видео во всю ширину и крупно (края по бокам обрезаются до 4:3), фон размыт,
        заголовок над видео, чат под видео;
  blur  видео целиком по ширине, сверху и снизу размытый фон из него же, чат под видео;
  crop  видео на весь экран (края по бокам обрезаются), чат полупрозрачно поверх, слева внизу.
Чат рисуется отдельной прозрачной дорожкой, заголовок отдельной картинкой. Их накладывает ffmpeg,
само видео кодируется один раз.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading

from . import util

log = logging.getLogger("vrender")
W, H = 1080, 1920
ENCODE = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-maxrate", "12M", "-bufsize", "24M",
          "-profile:v", "high", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
          "-movflags", "+faststart"]


def probe_video(path: str) -> tuple[int, int, float]:
    """Ширина, высота и частота кадров исходника. Если узнать не вышло, считаем обычным 1080p."""
    try:
        r = util.run([util.find_tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                      "stream=width,height,avg_frame_rate,r_frame_rate", "-of", "json", path], timeout=60)
        st = json.loads(r.stdout.decode("utf-8", "replace"))["streams"][0]
        fps = 0.0
        for key in ("avg_frame_rate", "r_frame_rate"):
            a, _, b = str(st.get(key) or "0/1").partition("/")
            val = float(a) / float(b or 1) if float(b or 1) else 0.0
            if 5 <= val <= 240:
                fps = val
                break
        return int(st["width"]), int(st["height"]), fps or 30.0
    except Exception as e:
        log.warning("не смог узнать размер видео: %s", e)
        return 1920, 1080, 30.0


BIG_H = 810                                           # высота видео в виде «крупно»: кадр 4:3 во всю ширину
TITLE_TOP = 430                                       # с заголовком видео не поднимается выше: над ним до трёх строк
TITLE_GAP = 14


def geometry(layout: str, src_w: int, src_h: int, chat: bool, title: bool = False) -> dict:
    """Где в кадре 1080x1920 стоят видео, заголовок и чат. Самый верх и низ кадра свободны: там кнопки и подписи
    самого TikTok. title: ("bottom", y) = низ заголовка на этой высоте, ("top", y) = верх."""
    fg_h = int(round(W * src_h / max(1, src_w) / 2)) * 2
    if layout == "crop" or fg_h > 1200:              # на весь экран; сюда же попадают стримы, снятые вертикально
        return {"mode": "crop" if layout == "crop" else "fit", "chat": (40, 1010, 780, 480),
                "title": ("top", 170) if title else None}
    big = layout == "big"
    if big:
        fg_h = max(fg_h, BIG_H)                      # шире, чем 4:3: приближаем, края по бокам уходят за кадр
    chat_h = 520 if not big else 380 if title else 480
    top = TITLE_TOP if title else 200
    if chat:
        vy = max(top, (H - (fg_h + 28 + chat_h)) // 2 - 70)
        box = (44, vy + fg_h + 28, 886, chat_h)
    else:
        vy = max(top, (H - fg_h) // 2 - (60 if title else 0)) if title or big else (H - fg_h) // 2
        box = None
    return {"mode": "blur", "fg_h": fg_h, "vy": vy, "chat": box, "title": ("bottom", vy - TITLE_GAP) if title else None}


def filter_graph(geo: dict, fps: float, with_chat: bool, title: tuple[int, int] | None = None) -> str:
    """title: (ширина, высота) картинки заголовка. Она идёт входом сразу за чатом (или за видео, если чата нет)."""
    blur_bg = (f"scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,crop={W // 4}:{H // 4},"
               f"boxblur=10:2,scale={W}:{H}:flags=bicubic,lutyuv=y=val*0.58")      # фон темнее: видео и чат выделяются
    if geo["mode"] == "crop":
        g = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H}[base]"
    elif geo["mode"] == "fit":
        g = (f"[0:v]split=2[a][b];[a]{blur_bg}[bg];"
             f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
             f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base]")
    else:
        g = (f"[0:v]split=2[a][b];[a]{blur_bg}[bg];"
             f"[b]scale={W}:{geo['fg_h']}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{geo['fg_h']}[fg];"
             f"[bg][fg]overlay=0:{geo['vy']}[base]")
    cur = "base"
    if title and geo.get("title"):
        tw, th = title
        side, y = geo["title"]
        g += f";[{cur}][{2 if with_chat else 1}:v]overlay={(W - tw) // 2}:{y - th if side == 'bottom' else y}[titled]"
        cur = "titled"
    tail = "fps=60," if fps > 61 else ""
    if with_chat:
        x, y = geo["chat"][0], geo["chat"][1]
        g += f";[{cur}][1:v]overlay={x}:{y}:alpha=premultiplied:eof_action=pass,{tail}format=yuv420p[v]"
    else:
        g += f";[{cur}]{tail}format=yuv420p[v]"
    return g


def _qt_app():
    """Qt для рисования чата и заголовка. Работает только в отдельном процессе рендера."""
    if sys.platform != "win32" and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication
    return QGuiApplication.instance() or QGuiApplication(["vrender"])


def _title_image(spec: dict) -> tuple[str, tuple[int, int]] | None:
    """Рисует заголовок во временный файл. None: заголовка нет или нарисовать не вышло (клип выйдет без него)."""
    text = str(spec.get("headline") or "").strip()
    if not text:
        return None
    path = ""
    try:
        from . import titlerender
        _qt_app()
        fd, path = tempfile.mkstemp(prefix="sc_title_", suffix=".png")
        os.close(fd)
        size = titlerender.render(text, path)
        if size:
            return path, size
    except Exception as e:
        log.warning("заголовок над видео не нарисовался, клип выйдет без него: %s", e)
    if path:
        try:
            os.remove(path)
        except OSError:
            pass
    return None


def _chat_frames(spec: dict, box: tuple[int, int, int, int]):
    """Готовит чат и возвращает поток его кадров. Работает только в отдельном процессе (нужен Qt)."""
    app = _qt_app()
    from . import chatrender, emotes
    msgs = spec["chat"]
    third: dict[str, str] = {}
    if spec.get("emotes", True):
        try:
            third = emotes.load_map(str(spec.get("room_id") or ""))
        except Exception as e:
            log.info("список смайлов не загрузился: %s", e)
    toks = [chatrender.tokenize(m["text"], m.get("emotes") or [], third, emotes.twitch_url) for m in msgs]
    urls = [t[1] for tk in toks for t in tk if t[0] == "emote"]
    images: dict[str, str] = {}
    if urls and spec.get("emotes", True):
        try:
            images = emotes.fetch_images(urls)
        except Exception as e:
            log.info("картинки смайлов не загрузились: %s", e)
    painter = chatrender.ChatPainter(box[2], box[3], spec.get("size", "m"), spec.get("style", "soft"))
    for m, tk in zip(msgs, toks):
        painter.add(float(m["t"]), m["name"], m.get("color", ""), tk, images)
    log.info("чат в клипе: сообщений %d, смайлов картинками %d из %d", len(msgs), len(images), len(set(urls)))
    return app, painter.frames(float(spec["dur"])), painter.FPS


def render(spec: dict) -> bool:
    """Делает вертикальный клип. Без spec["chat"] и spec["headline"]: без Qt, одним ffmpeg-ом."""
    raw, out = spec["raw"], spec["out"]
    start, dur = float(spec["start"]), float(spec["dur"])
    src_w, src_h, fps = probe_video(raw)
    with_chat = bool(spec.get("chat"))
    title = _title_image(spec)
    geo = geometry(spec.get("layout", "blur"), src_w, src_h, with_chat, bool(title))
    cmd = [util.find_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
           "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", raw]
    frames = app = None
    if with_chat:
        app, frames, cfps = _chat_frames(spec, geo["chat"])
        cmd += ["-f", "rawvideo", "-pix_fmt", "bgra", "-video_size", f"{geo['chat'][2]}x{geo['chat'][3]}",
                "-framerate", str(cfps), "-i", "pipe:0"]
    if title:
        cmd += ["-i", title[0]]
    cmd += ["-filter_complex", filter_graph(geo, fps, with_chat, title[1] if title else None), "-map", "[v]",
            "-map", "0:a:0?", *ENCODE, "-t", f"{dur:.3f}", out]
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if with_chat else subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=err, creationflags=util.NO_WINDOW)
        guard = threading.Timer(540, proc.kill)          # страховка от зависшего ffmpeg
        guard.daemon = True
        guard.start()
        try:
            if with_chat:
                try:
                    for frame in frames:
                        proc.stdin.write(frame)
                except (BrokenPipeError, OSError):       # ffmpeg закончил раньше: видео оказалось короче
                    pass
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            code = proc.wait()
        finally:
            guard.cancel()
        if code != 0:
            err.seek(0)
            log.warning("ffmpeg (вертикальный клип): %s", err.read().decode("utf-8", "replace")[-600:])
    del app
    if title:
        try:
            os.remove(title[0])
        except OSError:
            pass
    return code == 0 and os.path.exists(out) and os.path.getsize(out) > 50_000


def main(spec_path: str) -> int:
    util.setup_logging("editor")
    try:
        with open(spec_path, "r", encoding="utf-8") as f:
            spec = json.load(f)
        return 0 if render(spec) else 1
    except Exception:
        log.exception("вертикальный клип не получился")
        return 1
