"""Описание клипа: что сказал стример (Whisper), что писал чат и, если включено, вердикт ИИ.

Токены тратятся только на уже отобранные моменты: один короткий запрос на клип.
"""
from __future__ import annotations

import logging
import os
import re

from . import config, util

log = logging.getLogger("describe")

# Whisper на музыке и тишине любит выдумывать такие фразы. Выкидываем.
_HALLU = re.compile(r"субтитр|продолжение следует|редактор субтитров|корректор|dimatorzok|"
                    r"спасибо за просмотр|подписывайтесь на канал|ставьте лайк", re.I)


def transcribe(raw: str, start: float, end: float, cfg: dict) -> list[tuple[float, float, str]]:
    """Русская речь в куске [start, end]. Возвращает [(с, по, текст)] во времени исходника."""
    if not cfg.get("whisper"):
        return []
    try:
        from faster_whisper import WhisperModel
    except Exception as e:
        log.info("Whisper недоступен в этой сборке: %s", e)
        return []
    try:
        import numpy as np
        # Звук достаём ffmpeg-ом и отдаём распознаванию готовым массивом: так не нужен отдельный декодер звука
        r = util.run([util.find_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-ss", f"{start:.2f}",
                      "-i", raw, "-t", f"{end - start:.2f}", "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"],
                     timeout=120)
        if r.returncode != 0 or len(r.stdout) < 16000:
            return []
        audio = np.frombuffer(r.stdout[: len(r.stdout) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
        model = WhisperModel(cfg.get("whisper_model") or "small", device="cpu", compute_type="int8",
                             download_root=str(config.subdir("models")),
                             cpu_threads=max(2, min(6, (os.cpu_count() or 4) // 2)))
        kw = dict(language="ru", beam_size=2, condition_on_previous_text=False)
        try:
            segs, _info = model.transcribe(audio, vad_filter=True, **kw)
            segs = list(segs)
        except Exception as e:
            log.info("распознавание с отсевом тишины не вышло (%s), пробую без него", e)
            segs, _info = model.transcribe(audio, vad_filter=False, **kw)
            segs = list(segs)
        out = []
        for s in segs:
            text = (s.text or "").strip()
            if text and not _HALLU.search(text) and getattr(s, "no_speech_prob", 0) < 0.8:
                out.append((start + s.start, start + s.end, text))
        log.info("распознано фраз: %d", len(out))
        return out
    except Exception as e:
        log.warning("распознавание речи не удалось: %s", e)
        return []


SYSTEM = """Ты редактор нарезок с русскоязычных Twitch-стримов для TikTok. Тебе дают кусок стрима: что говорил \
стример (расшифровка может быть с ошибками) и что писал чат, с секундами от начала куска. Иногда к этому \
приложены кадры из того же куска, у каждого подписана его секунда.
Чат отреагировал на что-то смешное или неожиданное. Твоя задача:
1. Понять, что произошло, и выбрать ОДИН непрерывный отрезок без склеек, который затягивает: \
короткая подводка, сам момент, реакция. Скучное начало и затянутый хвост отрежь.
2. Придумать заголовок. Он будет написан крупно над видео, как в TikTok-нарезках, и должен коротко называть \
то, что происходит на экране, чтобы зритель захотел досмотреть. Примеры: «угон инвалидной коляски в гта 6», \
«стример проиграл всё за 10 секунд». До 45 символов, по-русски, без кавычек, эмодзи, хэштегов и точки в конце.
3. Написать описание в одно-два предложения, по-русски, живым языком, без пересказа очевидного.
4. Оценить, насколько это смешно/залипательно для зрителя нарезок, от 0 до 100.
Не выдумывай: пиши только то, что видно из текста и кадров. Если это не момент, а просто спам, реклама, бот \
или ничего не понятно, поставь keep=false."""

SHOTS = 8             # сколько кадров показать ИИ
SHOT_W = 640          # ширина кадра: хватает, чтобы понять происходящее, и почти ничего не стоит


def shots(raw: str, start: float, end: float, n: int = SHOTS) -> list[tuple[float, bytes]]:
    """Кадры из куска [start, end] через равные промежутки: [(секунда, JPEG)]. Нужны, чтобы ИИ видел экран."""
    out: list[tuple[float, bytes]] = []
    if end - start < 2 or n < 1:
        return out
    step = (end - start) / n
    for i in range(n):
        t = start + step * (i + 0.5)
        try:
            r = util.run([util.find_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", raw,
                          "-frames:v", "1", "-vf", f"scale={SHOT_W}:-2", "-q:v", "6", "-f", "image2pipe",
                          "-c:v", "mjpeg", "pipe:1"], timeout=30)
        except Exception as e:
            log.info("кадр для ИИ не получился: %s", e)
            break
        if r.returncode == 0 and r.stdout[:2] == b"\xff\xd8" and len(r.stdout) > 1500:
            out.append((t, r.stdout))
    return out


def ai_content(user: str, pics: list[tuple[float, bytes]]):
    """Сообщение для модели: кадры с подписями секунд, за ними текст. Без кадров: просто текст."""
    if not pics:
        return user
    import base64
    content: list[dict] = []
    for t, jpg in pics:
        content.append({"type": "text", "text": f"Кадр на {t:.0f}-й секунде:"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.standard_b64encode(jpg).decode("ascii")}})
    content.append({"type": "text", "text": user})
    return content


def ai_verdict(job: dict, transcript, lo: float, hi: float, cur: tuple[float, float], cfg: dict) -> dict | None:
    """Спрашивает модель. Возвращает dict или None, если ИИ выключен/недоступен."""
    if not (cfg.get("ai_enabled") and cfg.get("ai_api_key")):
        return None
    try:
        import anthropic
        from pydantic import BaseModel

        class Verdict(BaseModel):
            keep: bool
            start: float
            end: float
            title: str
            description: str
            score: int

        manual = job.get("kind") == "manual"
        said = "\n".join(f"[{a:.0f}-{b:.0f}] {t}" for a, b, t in transcript) or "(речь не распознана)"
        chat = [c for c in job.get("chat", []) if lo - 5 <= c[0] <= hi + 5]
        step = max(1, len(chat) // 150)                       # не больше ~150 сообщений
        chat_txt = "\n".join(f"[{c[0]:.0f}] {c[1]}: {c[2][:120]}" for c in chat[::step])
        user = (f"Канал: {job['channel']}\nНазвание стрима: {job.get('title') or '?'}\n"
                f"Категория: {job.get('category') or '?'}\n")
        if manual:
            user += (f"Этот отрезок владелец вырезал сам, кнопкой: от {cur[0]:.0f} до {cur[1]:.0f} секунды. Границы "
                     f"менять нельзя: верни их как есть, keep=true. Нужны заголовок, описание и оценка.\n\n")
        else:
            user += (f"Чат начал реагировать на {job['onset']:.0f}-й секунде, пик на {job['peak']:.0f}-й, "
                     f"утих на {job['end']:.0f}-й.\n"
                     f"Можно выбрать отрезок в пределах от {lo:.0f} до {hi:.0f} секунды, длиной от "
                     f"{cfg['clip_min']} до {cfg['clip_max']} секунд. Предварительный выбор: {cur[0]:.0f}-{cur[1]:.0f}.\n\n")
        user += f"СТРИМЕР ГОВОРИЛ:\n{said}\n\nЧАТ ПИСАЛ:\n{chat_txt or '(пусто)'}"
        try:
            from .learning import Learner
            liked, disliked = Learner().examples(job["channel"])
            if liked or disliked:
                user += ("\n\nВКУС ВЛАДЕЛЬЦА (учитывай при оценке)."
                         + ("\nПонравилось:\n- " + "\n- ".join(liked) if liked else "")
                         + ("\nНе понравилось:\n- " + "\n- ".join(disliked) if disliked else ""))
        except Exception:
            pass
        pics = shots(job["raw"], cur[0], cur[1]) if cfg.get("ai_frames", True) and job.get("raw") else []
        if pics:
            log.info("ИИ получит кадров: %d", len(pics))
        client = anthropic.Anthropic(api_key=cfg["ai_api_key"], timeout=90.0, max_retries=2)
        # У модели размышление включено всегда и расходует тот же лимит, что и ответ. С лимитом в 2000 ответ
        # иногда обрывался на полуслове, и описание молча заменялось обычным. Даём запас; платим только за
        # реально написанное. Задача простая, поэтому усилие «low»: быстрее и дешевле.
        resp = client.messages.parse(
            model=cfg.get("ai_model") or "claude-haiku-5-5",
            max_tokens=8000,
            system=SYSTEM,
            messages=[{"role": "user", "content": ai_content(user, pics)}],
            output_config={"effort": "low"},
            output_format=Verdict,
        )
        if resp.stop_reason in ("refusal", "max_tokens") or resp.parsed_output is None:
            log.info("ИИ не дал ответа (%s), беру обычное описание", resp.stop_reason)
            return None
        v = resp.parsed_output
        return {"keep": v.keep, "start": float(v.start), "end": float(v.end), "title": v.title.strip()[:80],
                "description": v.description.strip()[:400], "score": max(0, min(100, int(v.score)))}
    except Exception as e:
        log.warning("ИИ-описание не получилось: %s", e)
        return None


def headline(job: dict, title: str, from_ai: bool, cfg: dict) -> str:
    """Надпись над видео в вертикальном клипе. Пустая строка: без надписи.

    Свой текст из настроек важнее всего. Дальше заголовок от ИИ (он знает, что на экране). Без ИИ надпись
    простая, по реакции чата; у клипа, вырезанного кнопкой, реакции нет, поэтому и надписи нет.
    """
    if not cfg.get("tt_title", True):
        return ""
    own = str(cfg.get("tt_title_text") or "").strip()
    if own:
        return own[:90]
    if from_ai:
        return title.strip()
    return "" if job.get("kind") == "manual" else title.strip()


def heuristic(job: dict, transcript) -> tuple[str, str]:
    """Заголовок и цитата без ИИ."""
    kind = job.get("kind")
    title = {"laugh": "Чат разрывает от смеха", "hype": "Чат в шоке", "manual": "Клип вручную"}.get(kind, "Момент")
    onset = job.get("onset", 0)
    near = [t for a, b, t in transcript if b >= onset - 12 and a <= onset + 3]
    quote = " ".join(near).strip()
    if len(quote) > 180:
        quote = "…" + quote[-180:]
    return title, quote


def caption(job: dict, title: str, desc: str, quote: str, score: int, dur: float, when: str) -> str:
    lines = [f"🎬 {job['channel']} · {title}"]
    if desc:
        lines.append(desc)
    if quote:
        lines.append(f"🗣 «{quote}»")
    if job.get("top"):
        lines.append("💬 " + " · ".join(f"«{p}»" for p in job["top"][:4]))
    meta = []
    if job.get("kind") != "manual":
        meta.append(f"🔥 {score}/100")
        if job.get("peak_z"):
            meta.append(f"чат ×{job['peak_z']:.0f}")
    meta += [f"{dur:.0f} с", when]
    lines.append(" · ".join(meta))
    if job.get("title"):
        lines.append(f"📺 {job['title'][:100]}")
    lines.append("#" + re.sub(r"\W", "_", job["channel"]))
    return "\n".join(lines)[:1000]
