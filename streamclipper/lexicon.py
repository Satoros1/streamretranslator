"""Как смеётся и удивляется чат: русский и английский. Чистые правила, без ИИ и без токенов.

Правила общие для обоих языков: в русском чате пишут английскими смайлами (KEKW, LUL), в английском иногда
по-русски, поэтому отдельный словарь на язык только навредил бы. Язык канала нужен для другого (распознавание
речи и описание клипа), его угадывает LangGuess по самому чату.
"""
from __future__ import annotations

import re
from collections import deque

_F = re.I | re.U

# --- смех -------------------------------------------------------------------
_LAUGH = re.compile(
    r"""
      (?<!\w)[аa]*(?:[хxh]+[аaеeыиоoi]+){2,}[хxh]*(?!\w)    # ахаха, хахах, хехе, axaxa, haha, hehe, hihi
    | (?<!\w)(?:[аa]+[хxh]+){2,}[аa]*(?!\w)                  # ахах, аххаххах, ahah
    | (?<!\w)(?:[пвкъыфaа]?[хx]){3,}[пвкъ]?(?!\w)            # пхпхпх, вхвхв, кхкх, ъхъхъ, хххх
    | (?<!\w)(?:[аa]?[зz][аa]){2,}(?!\w)                     # азаза
    | (?<!\w)ы{3,}(?!\w)                                     # ыыыы
    | (?<!\w)(?:ор+у+|ору+|орунах\w*|ор[её]м|орнул\w*|разорвало|порвало|вынесло)(?!\w)
    | (?<!\w)(?:рж[уа]\w*|ржак\w*|ржомб\w*|угар\w*|уссы\w*|обосса\w*|сме(?:шно|юсь|ёмся|х)\w*)
    | (?<!\w)(?:ло+л+|lo+l+w?|lmf?a+o+|lmf+a+o+|l+m+a+o+|rofl|ro+fl|рофл\w*|ке+к+[wв]?|ke+k+[wl]?|хд+|xd+|хдд+)(?!\w)
    | (?<!\w)(?:у?мер(?:ла)?|сдох(?:ла)?|плачу|я\s+не\s+могу|не\s+могу)(?!\w)
    # английский: «I'm dead», «LMAOOO», «I'm crying», «hilarious», «+2» (так смеётся чат многих стримеров)
    | (?<!\w)i'?m\s*(?:so\s+)?(?:dead|deceased|dying|crying|weak|screaming|wheezing|in\s+tears)(?!\w)
    | (?<!\w)(?:hilarious|wheez\w*|cackl\w*|funniest|so\s+funny|too\s+funny|comedy|comedic|kekl\w*)(?!\w)
    | (?<![\w+])\+2(?!\d)
    | [😂🤣😹😆💀☠😭]
    """,
    _F | re.X,
)

# Эмоуты пишутся словами. Ловим и стандартные, и канальные вида streamerKEKW.
_LAUGH_EMOTE = re.compile(
    r"(?<!\w)\w*(?:kekw?|lul|omegalul|laugh|giggle|icant|lmao|4head|xdd|pepela|jebaited|хаха)\w*(?!\w)",
    _F,
)

# Короткое сообщение целиком: «DEAD», «crying», «i cant», «HELP 😭». Внутри длинной фразы эти слова обычно
# значат другое («he's dead», «I can't see»), поэтому только так.
_LAUGH_WHOLE = re.compile(
    r"^[\W_]*(?:dead|ded|deceased|dying|crying|screaming|weak|help|stop+|i\s*can'?t|i\s*cant\s+breathe|bro\s+is\s+done)"
    r"[\W_]*$",
    _F,
)

# --- шок / хайп ------------------------------------------------------------
_HYPE = re.compile(
    r"""
      (?<!\w)(?:жесть|жёстк\w*|жестк\w*|кринж\w*|cringe|вау+|wow+|омг|omg|нифига|ничоси|ничего\s+себе
        |имба|клатч\w*|clutch|красав\w*|легенд\w*|гений|гениально|шок|в\s+шоке|чего+|чт[оо]{2,}|ч[её]{2,}
        |а[хх]у[ие]\w*|о[хх]у[ие]\w*|пизд\w*|[её]ба+ть|бля+(?:ть)?|нихуя\w*|ебан\w*|треш|трэш|мда+|найс|nice
        |ноу+\s*вей|no\s*way|что\s+происходит|что\s+он\s+(?:несет|несёт|творит|делает))(?!\w)
    | (?<!\w)\w*(?:pog|poggers|pogu|pogchamp|wutface|monka\w?|gasp|wtf|omegadance|clap|sadge|aware|clueless|copium
        |madge|pausechamp|nowaying|gigachad|weirdchamp|hypers|letsgo|huhh)\w*(?!\w)
    # английский: «NO WAY», «HOLY SHIT», «LETS GOOO», «bro what», «NAHHH», «chat is this real», «W»
    | (?<!\w)(?:holy\s*(?:shit|moly|fuck|crap|cow)|holy+|ho+ly|let'?s+\s*go+|lets+go+|yo{3,}|no{3,}|bro{3,}|bru+h+
        |na{2,}h+|nah{2,}|wha+t{2,}|wh?at\s+the\s+(?:fuck|hell|heck|f+)|wtf+|wth|omfg|oh\s+my\s+god|oh\s+no+|ain'?t\s+no\s+way
        |no\s*shot|no\s+chance|is\s+this\s+real|insane(?:ly)?|crazy|goated|sheesh\w*|she+sh|dam+n+|dayum|jesus
        |unreal|cinema|peak|massive\s+w|huge\s+w|massive\s+l|huge\s+l|fu+ck+(?:ing)?|shi+t+)(?!\w)
    | (?<![\w+-])(?:W|L)(?!\w)
    | [!?]{3,}
    | [😱🔥🤯😳🫣]
    """,
    _F | re.X,
)
# «EZ» только как отдельное слово или смайл: внутри обычных слов (freeze, Lopez) это не реакция
_EZ = re.compile(r"(?<!\w)(?:ez+|ezpz|\w*EZ\w*)(?!\w)", re.U)
# Короткое сообщение целиком: «WHAT», «bro», «nah», «w», «huh?». Внутри фразы эти слова ничего не значат.
_HYPE_WHOLE = re.compile(r"^[\W_]*(?:(?:bro+|dude|chat)\s+)?(?:what+|huh+|bro+|nah+|yo+|w+|l+|pause|dude|why+|how+)[\W_]*$", _F)

# --- прямые просьбы «в нарезку» -------------------------------------------
_CLIP = re.compile(
    r"(?<!\w)(?:клип\w*|clip\w*|нарезк\w*|в\s+тик\s?ток|в\s+шортс\w*|момент\s+года|мем\s+года|мемчик|в\s+мемы"
    r"|lsf|to\s+lsf|tik\s?tok|youtube\s+short\w*|thumbnail|highlight\w*|compilation|meme\s+of\s+the\s+year"
    r"|moment\s+of\s+the\s+(?:year|stream|day))(?!\w)",
    _F,
)

_CAPS = re.compile(r"[A-ZА-ЯЁ]")
_LETTER = re.compile(r"[A-Za-zА-Яа-яЁё]")


def classify(text: str) -> tuple[bool, bool, bool]:
    """Возвращает (смех, хайп, просьба_клипа) для одного сообщения чата."""
    if not text or text[0] in "!/":      # команды ботам не считаем
        return (False, False, False)
    short = len(text) <= 40
    laugh = bool(_LAUGH.search(text) or _LAUGH_EMOTE.search(text) or (short and _LAUGH_WHOLE.match(text)))
    clip = bool(_CLIP.search(text))
    hype = bool(_HYPE.search(text) or _EZ.search(text) or (short and _HYPE_WHOLE.match(text)))
    if not hype and not laugh:
        letters = _LETTER.findall(text)
        if len(letters) >= 5 and len(_CAPS.findall(text)) / len(letters) > 0.8:
            hype = True                  # ОРЁТ КАПСОМ
    return (laugh, hype, clip)


def reaction_weight(laugh: bool, hype: bool, clip: bool) -> float:
    return (1.0 if laugh else 0.0) + (0.6 if hype and not laugh else 0.0) + (1.5 if clip else 0.0)


_NORM_RE = re.compile(r"(.)\1{2,}", re.U)
_LAUGH_NORM = re.compile(r"^[ахxhaпвкъыеeoi]+$", _F)
_LMAO_NORM = re.compile(r"^l+m+f?a+o+$", _F)
_CYR = re.compile(r"[А-Яа-яЁё]")


def normalize(text: str) -> str:
    """Склеивает «АХАХАХАХ» и «ахахах» (и «LMAOOO» с «lmao») в одну фразу для подсчёта популярных реакций."""
    t = " ".join(text.strip().split())
    low = t.lower()
    flat = low.replace(" ", "")
    if len(low) >= 4 and _LAUGH_NORM.match(flat) and _LAUGH.fullmatch(flat):
        # смех латиницей с «h» пишет английский чат: ему и показываем «HAHAHA», а не русское «АХАХАХ»
        return "HAHAHA" if "h" in flat and not _CYR.search(flat) else "АХАХАХ"
    if _LMAO_NORM.match(flat):
        return "LMAO"
    words = t.split(" ")
    if len(words) > 1 and len(set(words)) == 1:      # KEKW KEKW KEKW
        t = words[0]
    t = _NORM_RE.sub(r"\1\1", t)
    return t[:60]


# --- язык чата ------------------------------------------------------------
# Служебные английские слова. Смайлы (KEKW, LUL) и словечки вроде «lol», «ok», «bro» тоже латиница, ими пишет
# и русский чат, поэтому английским считается только сообщение, где есть обычное служебное слово.
_EN_WORDS = set("""the you your you're youre is isn't are aren't was wasn't were it it's its this that what why how
who when where i i'm im me my he he's his him she her they them their we our and but not do does did don't dont
doesn't didn't can can't cant could would should will won't just like have has had of to in on at for with from
about been there here then than very some any out know think said tell want need""".split())
_EN_TOKEN = re.compile(r"[a-z']+")
LANG_MIN = 40          # столько «языковых» сообщений надо увидеть, чтобы судить
LANG_KEEP = 600        # по скольким последним таким сообщениям судим


def message_lang(text: str) -> str:
    """'ru', 'en' или '' (не понять: смайлы, команды, другой язык) для одного сообщения."""
    if not text or text[0] in "!/":
        return ""
    if len(_CYR.findall(text)) >= 2:
        return "ru"
    words = _EN_TOKEN.findall(text.lower())
    if sum(1 for w in words if w in _EN_WORDS) >= (1 if len(words) <= 3 else 2):
        return "en"
    return ""


class LangGuess:
    """Угадывает язык канала по его чату: доля сообщений с кириллицей против сообщений с английскими словами."""

    def __init__(self) -> None:
        self.seen: deque[str] = deque(maxlen=LANG_KEEP)
        self.lang = ""

    def add(self, text: str) -> None:
        lang = message_lang(text)
        if lang:
            self.seen.append(lang)
            if len(self.seen) >= LANG_MIN and len(self.seen) % 10 == 0:
                self.lang = self._decide()

    def _decide(self) -> str:
        ru = sum(1 for x in self.seen if x == "ru") / len(self.seen)
        # В русском чате кириллица почти в каждом втором осмысленном сообщении, в английском её почти нет.
        # Между порогами чат смешанный: оставляем прежний вывод, а без него берём то, чего больше.
        if ru >= 0.35:
            return "ru"
        if ru <= 0.12:
            return "en"
        return self.lang or ("ru" if ru >= 0.5 else "en")
