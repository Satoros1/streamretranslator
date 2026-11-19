"""Как русский чат смеётся и удивляется. Чистые правила, без ИИ и без токенов."""
from __future__ import annotations

import re

_F = re.I | re.U

# --- смех -------------------------------------------------------------------
_LAUGH = re.compile(
    r"""
      (?<!\w)[аa]*(?:[хxh]+[аaеeыиоo]+){2,}[хxh]*(?!\w)     # ахаха, хахах, хехе, axaxa, haha
    | (?<!\w)(?:[аa]+[хxh]+){2,}[аa]*(?!\w)                  # ахах, аххаххах
    | (?<!\w)(?:[пвкъыфaа]?[хx]){3,}[пвкъ]?(?!\w)            # пхпхпх, вхвхв, кхкх, ъхъхъ, хххх
    | (?<!\w)(?:[аa]?[зz][аa]){2,}(?!\w)                     # азаза
    | (?<!\w)ы{3,}(?!\w)                                     # ыыыы
    | (?<!\w)(?:ор+у+|ору+|орунах\w*|ор[её]м|орнул\w*|разорвало|порвало|вынесло)(?!\w)
    | (?<!\w)(?:рж[уа]\w*|ржак\w*|ржомб\w*|угар\w*|уссы\w*|обосса\w*|сме(?:шно|юсь|ёмся|х)\w*)
    | (?<!\w)(?:ло+л+|lo+l+|lmf?a+o+|rofl|рофл\w*|ке+к+[wв]?|ke+k+w?|хд+|xd+|хдд+)(?!\w)
    | (?<!\w)(?:у?мер(?:ла)?|сдох(?:ла)?|плачу|я\s+не\s+могу|не\s+могу)(?!\w)
    | [😂🤣😹😆💀☠]
    """,
    _F | re.X,
)

# Эмоуты пишутся словами. Ловим и стандартные, и канальные вида streamerKEKW.
_LAUGH_EMOTE = re.compile(
    r"(?<!\w)\w*(?:kekw?|lul|omegalul|laugh|giggle|icant|lmao|4head|xdd|pepela|jebaited|хаха)\w*(?!\w)",
    _F,
)

# --- шок / хайп ------------------------------------------------------------
_HYPE = re.compile(
    r"""
      (?<!\w)(?:жесть|жёстк\w*|жестк\w*|кринж\w*|cringe|вау+|wow+|омг|omg|нифига|ничоси|ничего\s+себе
        |имба|клатч\w*|clutch|красав\w*|легенд\w*|гений|гениально|шок|в\s+шоке|чего+|чт[оо]{2,}|ч[её]{2,}
        |а[хх]у[ие]\w*|о[хх]у[ие]\w*|пизд\w*|[её]ба+ть|бля+(?:ть)?|нихуя\w*|ебан\w*|треш|трэш|мда+|найс|nice
        |ноу+\s*вей|no\s*way|что\s+происходит|что\s+он\s+(?:несет|несёт|творит|делает))(?!\w)
    | (?<!\w)\w*(?:pog|poggers|pogu|pogchamp|wutface|monka\w?|gasp|wtf|omegadance|ez|clap|sadge|aware)\w*(?!\w)
    | [!?]{3,}
    | [😱🔥🤯😳🫣]
    """,
    _F | re.X,
)

# --- прямые просьбы «в нарезку» -------------------------------------------
_CLIP = re.compile(
    r"(?<!\w)(?:клип\w*|clip\w*|нарезк\w*|в\s+тик\s?ток|в\s+шортс\w*|момент\s+года|мем\s+года|мемчик|в\s+мемы)(?!\w)",
    _F,
)

_CAPS = re.compile(r"[A-ZА-ЯЁ]")
_LETTER = re.compile(r"[A-Za-zА-Яа-яЁё]")


def classify(text: str) -> tuple[bool, bool, bool]:
    """Возвращает (смех, хайп, просьба_клипа) для одного сообщения чата."""
    if not text or text[0] in "!/":      # команды ботам не считаем
        return (False, False, False)
    laugh = bool(_LAUGH.search(text) or _LAUGH_EMOTE.search(text))
    clip = bool(_CLIP.search(text))
    hype = bool(_HYPE.search(text))
    if not hype and not laugh:
        letters = _LETTER.findall(text)
        if len(letters) >= 5 and len(_CAPS.findall(text)) / len(letters) > 0.8:
            hype = True                  # ОРЁТ КАПСОМ
    return (laugh, hype, clip)


def reaction_weight(laugh: bool, hype: bool, clip: bool) -> float:
    return (1.0 if laugh else 0.0) + (0.6 if hype and not laugh else 0.0) + (1.5 if clip else 0.0)


_NORM_RE = re.compile(r"(.)\1{2,}", re.U)
_LAUGH_NORM = re.compile(r"^[ахxhaпвкъы]+$", _F)


def normalize(text: str) -> str:
    """Склеивает «АХАХАХАХ» и «ахахах» в одну фразу для подсчёта популярных реакций."""
    t = " ".join(text.strip().split())
    low = t.lower()
    if _LAUGH_NORM.match(low.replace(" ", "")) and len(low) >= 4:
        return "АХАХАХ"
    words = t.split(" ")
    if len(words) > 1 and len(set(words)) == 1:      # KEKW KEKW KEKW
        t = words[0]
    t = _NORM_RE.sub(r"\1\1", t)
    return t[:60]
