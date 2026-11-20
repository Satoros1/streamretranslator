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
                    r"спасибо за просмотр|подписывайтесь на канал|ставьте лайк|"
                    r"thanks? (?:you )?(?:so much )?for watching|please subscribe|like and subscribe|subscribe to (?:my|the|our) channel|"
                    r"subtitles by|amara\.org|transcribed by|see you (?:in the|next) (?:next )?video", re.I)

LANGS = ("ru", "en")      # на каких языках программа понимает стримы


def out_lang(job: dict, cfg: dict) -> str:
    """На каком языке писать заголовок и описание клипа: по-русски, или на языке стрима, если так выбрано."""
    if cfg.get("descr_lang") == "stream" and job.get("lang") in LANGS:
        return job["lang"]
    return "ru"


def transcribe(raw: str, start: float, end: float, cfg: dict, lang: str = "ru",
               info: dict | None = None) -> list[tuple[float, float, str]]:
    """Речь в куске [start, end]. Возвращает [(с, по, текст)] во времени исходника.

    lang: язык стрима ('ru', 'en'); пустой = неизвестен, Whisper определит сам и положит его в info["lang"].
    """
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
        kw = dict(language=lang if lang in LANGS else None, beam_size=2, condition_on_previous_text=False)
        try:
            segs, found = model.transcribe(audio, vad_filter=True, **kw)
            segs = list(segs)
        except Exception as e:
            log.info("распознавание с отсевом тишины не вышло (%s), пробую без него", e)
            segs, found = model.transcribe(audio, vad_filter=False, **kw)
            segs = list(segs)
        if kw["language"] is None:
            said = getattr(found, "language", "") or ""
            sure = float(getattr(found, "language_probability", 0) or 0)
            log.info("язык речи определён сам: %s (уверенность %.2f)", said or "?", sure)
            if info is not None and said in LANGS and sure >= 0.6:
                info["lang"] = said
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

# Английский стрим, а заголовок и описание по-русски (так по умолчанию: канал владельца русский)
SYSTEM_EN_RU = SYSTEM.replace("с русскоязычных Twitch-стримов", "с англоязычных Twitch-стримов").replace(
    "Иногда к этому приложены", "Стрим на английском: расшифровка и чат тоже. Заголовок и описание всё равно пиши "
    "по-русски, своими словами, а не дословным переводом; имена, названия игр и мемы вроде W или LMAO можно оставить "
    "как есть. Иногда к этому приложены")

SYSTEM_EN = """You edit highlight clips from English-language Twitch streams for TikTok. You get a piece of a stream: \
what the streamer said (the transcript may contain mistakes) and what chat wrote, with seconds from the start of the \
piece. Sometimes frames from the same piece are attached, each labelled with its second.
Chat reacted to something funny or unexpected. Your job:
1. Understand what happened and pick ONE continuous segment without cuts that hooks the viewer: a short lead-in, \
the moment itself, the reaction. Trim a boring start and a dragging tail.
2. Write a title. It is shown in large letters above the video, like in TikTok highlight clips, and must briefly \
name what happens on screen so the viewer wants to watch to the end. Examples: "stealing a wheelchair in gta 6", \
"streamer lost everything in 10 seconds". Up to 45 characters, in English, no quotes, emoji, hashtags or final period.
3. Write a description of one or two sentences, in English, in a lively voice, without retelling the obvious.
4. Rate from 0 to 100 how funny or gripping this is for someone watching highlight clips.
Don't make things up: write only what the text and frames show. If this is not a moment but just spam, an ad, \
a bot, or nothing is clear, set keep=false."""


def system_prompt(stream_lang: str, out: str) -> str:
    """Задание для ИИ под язык стрима и язык, на котором нужен ответ."""
    if out == "en":
        return SYSTEM_EN
    return SYSTEM_EN_RU if stream_lang == "en" else SYSTEM


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


# Текст запроса к ИИ. Для ответа по-английски и сам запрос по-английски: иначе модель тянет на русский.
_ASK = {
    "ru": {
        "head": "Канал: {channel}\nНазвание стрима: {title}\nКатегория: {category}\n",
        "manual": ("Этот отрезок владелец вырезал сам, кнопкой: от {a:.0f} до {b:.0f} секунды. Границы "
                   "менять нельзя: верни их как есть, keep=true. Нужны заголовок, описание и оценка.\n\n"),
        "auto": ("Чат начал реагировать на {onset:.0f}-й секунде, пик на {peak:.0f}-й, утих на {end:.0f}-й.\n"
                 "Можно выбрать отрезок в пределах от {lo:.0f} до {hi:.0f} секунды, длиной от {mn} до {mx} секунд. "
                 "Предварительный выбор: {a:.0f}-{b:.0f}.\n\n"),
        "body": "СТРИМЕР ГОВОРИЛ:\n{said}\n\nЧАТ ПИСАЛ:\n{chat}",
        "nospeech": "(речь не распознана)", "empty": "(пусто)",
        "taste": "\n\nВКУС ВЛАДЕЛЬЦА (учитывай при оценке).",
        "liked": "\nПонравилось:\n- ", "disliked": "\nНе понравилось:\n- ",
    },
    "en": {
        "head": "Channel: {channel}\nStream title: {title}\nCategory: {category}\n",
        "manual": ("The owner cut this segment by hand, with a button: from second {a:.0f} to second {b:.0f}. Do not "
                   "change the bounds: return them as they are, keep=true. Title, description and score are needed.\n\n"),
        "auto": ("Chat started reacting at second {onset:.0f}, peaked at {peak:.0f}, calmed down at {end:.0f}.\n"
                 "You may pick a segment between second {lo:.0f} and {hi:.0f}, from {mn} to {mx} seconds long. "
                 "Preliminary pick: {a:.0f}-{b:.0f}.\n\n"),
        "body": "THE STREAMER SAID:\n{said}\n\nCHAT WROTE:\n{chat}",
        "nospeech": "(no speech recognized)", "empty": "(empty)",
        "taste": "\n\nTHE OWNER'S TASTE (take it into account when scoring).",
        "liked": "\nLiked:\n- ", "disliked": "\nDisliked:\n- ",
    },
}


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
        lang = out_lang(job, cfg)
        T = _ASK[lang]
        said = "\n".join(f"[{a:.0f}-{b:.0f}] {t}" for a, b, t in transcript) or T["nospeech"]
        chat = [c for c in job.get("chat", []) if lo - 5 <= c[0] <= hi + 5]
        step = max(1, len(chat) // 150)                       # не больше ~150 сообщений
        chat_txt = "\n".join(f"[{c[0]:.0f}] {c[1]}: {c[2][:120]}" for c in chat[::step])
        user = T["head"].format(channel=job["channel"], title=job.get("title") or "?", category=job.get("category") or "?")
        if manual:
            user += T["manual"].format(a=cur[0], b=cur[1])
        else:
            user += T["auto"].format(onset=job["onset"], peak=job["peak"], end=job["end"], lo=lo, hi=hi,
                                     mn=cfg["clip_min"], mx=cfg["clip_max"], a=cur[0], b=cur[1])
        user += T["body"].format(said=said, chat=chat_txt or T["empty"])
        try:
            from .learning import Learner
            liked, disliked = Learner().examples(job["channel"])
            if liked or disliked:
                user += (T["taste"]
                         + (T["liked"] + "\n- ".join(liked) if liked else "")
                         + (T["disliked"] + "\n- ".join(disliked) if disliked else ""))
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
            system=system_prompt(str(job.get("lang") or ""), lang),
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


_TITLES = {
    "ru": {"laugh": "Чат разрывает от смеха", "hype": "Чат в шоке", "manual": "Клип вручную", "": "Момент"},
    "en": {"laugh": "Chat can't stop laughing", "hype": "Chat is shocked", "manual": "Manual clip", "": "Moment"},
}


def heuristic(job: dict, transcript, lang: str = "ru") -> tuple[str, str]:
    """Заголовок и цитата без ИИ. lang: язык заголовка."""
    kind = job.get("kind")
    names = _TITLES.get(lang, _TITLES["ru"])
    title = names.get(kind or "", names[""])
    onset = job.get("onset", 0)
    near = [t for a, b, t in transcript if b >= onset - 12 and a <= onset + 3]
    quote = " ".join(near).strip()
    if len(quote) > 180:
        quote = "…" + quote[-180:]
    return title, quote


def caption(job: dict, title: str, desc: str, quote: str, score: int, dur: float, when: str, lang: str = "ru") -> str:
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
            meta.append(("chat" if lang == "en" else "чат") + f" ×{job['peak_z']:.0f}")
    meta += [f"{dur:.0f} " + ("s" if lang == "en" else "с"), when]
    lines.append(" · ".join(meta))
    if job.get("title"):
        lines.append(f"📺 {job['title'][:100]}")
    lines.append("#" + re.sub(r"\W", "_", job["channel"]))
    return "\n".join(lines)[:1000]
