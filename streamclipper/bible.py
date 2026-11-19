"""«Библия» программы: простыми словами о том, как она выбирает моменты и чему научилась на пробах и ошибках.

Ничего не хранит сама: каждый раз собирается заново из того, что уже знает обучение (learning.py),
из настроек и из того, что прямо сейчас видит запись каждого стрима. Страница «Обучение» показывает
её в окне, а рядом с настройками лежит тот же текст файлом.
"""
from __future__ import annotations

import time
from pathlib import Path

from . import config, detector
from .learning import FEATURES, MIN_LABELS

FILE = "Чему научилась программа.txt"
WORDS_SHOWN = 12
DIARY_SHOWN = 40
WEIGHT = 0.15          # слабее этого признак на оценку почти не влияет: о нём не пишем

# Чем измеряется момент (признаки из learning.FEATURES) человеческими словами: «…клипы, где <это>».
TASTE = {
    "z": "чат взрывается резко и сильно",
    "share": "реагирует почти весь чат, а не его часть",
    "laugh": "чат именно смеётся, а не просто удивляется",
    "dur": "реакция держится долго",
    "msgs": "сообщений очень много",
    "clipreq": "зрители сами просят клип",
    "uniq": "реагирует много разных зрителей",
    "learned": "в чате звучат выученные слова этого канала",
}


def _quoted(words: list[str], limit: int = WORDS_SHOWN) -> str:
    shown = ", ".join(f"«{w}»" for w in words[:limit])
    return shown + (f" и ещё {len(words) - limit}" if len(words) > limit else "")


def _learned(n: int) -> str:
    return "Выучено слово" if n == 1 else "Выучены слова"


def taste(model: dict) -> tuple[list[str], list[str]]:
    """Что по оценкам нравится чаще и что реже: ([…], […]), самое заметное первым. Пока оценок мало, пусто."""
    w = model.get("w") or []
    if len(w) != len(FEATURES) or int(model.get("labels") or 0) < MIN_LABELS:
        return [], []
    ranked = sorted(zip(w, FEATURES), key=lambda p: -abs(p[0]))
    liked = [TASTE[f] for v, f in ranked if v >= WEIGHT]
    disliked = [TASTE[f] for v, f in ranked if v <= -WEIGHT]
    return liked[:3], disliked[:3]


def _step(mult: float) -> int:
    """Строгость канала ступенями по 10%: в дневник попадает переход со ступени на ступень, а не каждая оценка."""
    return int(round((float(mult) - 1.0) * 10, 6))


def strictness(mult: float, manual: int = 0) -> str:
    pct = round(abs(float(mult) - 1.0) * 100)
    if pct < 3:
        return "Отбор обычный: оценки пока не дали повода его менять."
    if mult > 1:
        return f"Отбор строже обычного на {pct}%: так программа ответила на клипы, которые не понравились."
    if manual:
        return (f"Отбор мягче обычного на {pct}%: моменты здесь вырезались кнопкой «Клип сейчас», "
                f"значит программа их пропускала.")
    return f"Отбор мягче обычного на {pct}%: клипы с этого канала чаще нравятся."


def lessons(old: dict, new: dict) -> list[tuple[str, str]]:
    """Что изменилось в выученном после очередной оценки: [(канал или "", вывод простыми словами)]."""
    out: list[tuple[str, str]] = []
    was, now = int(old.get("labels") or 0), int(new.get("labels") or 0)
    if was < MIN_LABELS <= now:
        out.append(("", f"Набралось {MIN_LABELS} оценок: с этого момента оценка каждого нового клипа "
                        f"поправляется под твой вкус."))
    (liked_old, bad_old), (liked, bad) = taste(old), taste(new)
    if liked and liked[:1] != liked_old[:1]:
        out.append(("", f"Вывод по оценкам: чаще нравятся клипы, где {liked[0]}."))
    if bad and bad[:1] != bad_old[:1]:
        out.append(("", f"Вывод по оценкам: реже нравятся клипы, где {bad[0]}."))
    old_words, new_words = old.get("words") or {}, new.get("words") or {}
    old_mult, new_mult = old.get("mult") or {}, new.get("mult") or {}
    for ch in sorted(set(new_words) | set(new_mult) | set(old_words)):
        ow, nw = old_words.get(ch) or {}, new_words.get(ch) or {}
        added = sorted(set(nw.get("pos") or []) - set(ow.get("pos") or []))
        gone = sorted(set(ow.get("pos") or []) - set(nw.get("pos") or []))
        junk = sorted(set(nw.get("neg") or []) - set(ow.get("neg") or []))
        if added:
            out.append((ch, f"{_learned(len(added))} {_quoted(added)}: так этот чат встречает удачные моменты. "
                            f"Теперь это считается реакцией наравне со смехом."))
        if gone:
            out.append((ch, f"Больше не считается реакцией: {_quoted(gone)}. Стало попадаться и в неудачных клипах."))
        if junk:
            out.append((ch, f"Не считается реакцией: {_quoted(junk)}. Чаще бывает в клипах, которые не понравились "
                            f"(спам, боты, рейды)."))
        om, nm = float(old_mult.get(ch, 1.0)), float(new_mult.get(ch, 1.0))
        if _step(om) != _step(nm):
            pct = round(abs(nm - 1.0) * 100)
            if pct < 3:
                out.append((ch, "Отбор вернулся к обычному."))
            elif nm > om:
                out.append((ch, f"Отбор стал строже: теперь на {pct}% {'строже' if nm > 1 else 'мягче'} обычного. "
                                f"Причина: клипы, которые не понравились."))
            else:
                out.append((ch, f"Отбор стал мягче: теперь на {pct}% {'мягче' if nm < 1 else 'строже'} обычного. "
                                f"Причина: удачные клипы и моменты, вырезанные кнопкой."))
    return out


def rules(cfg: dict) -> list[str]:
    """Как программа выбирает моменты при нынешних настройках."""
    sens = int(cfg.get("sensitivity") or 3)
    rare = detector.RARE.get(sens, detector.RARE[3])
    out = ["Программа следит за чатом и ждёт, когда он резко оживится: смех, «АХАХА», смайлы, просьбы сделать клип."]
    if rare:
        out.append(f"Клип делается, только если реакция сильнее, чем в {round(rare * 100)}% минут этого же канала за "
                   f"последние полтора часа. У каждого канала своя планка: в тихом чате хватит дружного смеха, "
                   f"в шумном нужен настоящий взрыв.")
        out.append(f"Реакция должна держаться не меньше {detector.MIN_DUR:.0f} секунд и идти хотя бы от "
                   f"{detector.MIN_USERS} разных зрителей. Один человек со смайлами клипом не станет.")
        out.append(f"Первые {detector.RARE_MIN} минут эфира программа присматривается к чату и берёт только очень "
                   f"сильные моменты.")
        pause = detector.PAUSE.get(sens, detector.PAUSE[3])
        if pause:
            out.append(f"После клипа {pause // 60} минут с того же канала берётся только момент заметно сильнее "
                       f"прошлого. Когда чат взрывается раз за разом, выходит лучший клип, а не пять подряд.")
    else:
        out.append("Чувствительность стоит на 5: берётся почти любая реакция чата.")
    try:
        cap = max(0, int(cfg.get("clips_per_hour") or 0))
    except (TypeError, ValueError):
        cap = 0
    if cap:
        out.append(f"Со всех стримов вместе выходит не больше {cap} клипов в час. Когда моментов больше, они ждут "
                   f"очереди: первым идёт самый сильный, а самые слабые пропускаются. Сила считается от планки "
                   f"своего канала, и смех ценится выше прочих реакций. Клипы по кнопке сюда не входят.")
    out.append("Клип вырезается одним цельным куском вокруг момента. Через рекламу и дыры в записи он не режется, "
               "а слишком короткий кусок не сохраняется.")
    out.append(f"Сейчас чувствительность {sens} из 5. Меняется в настройках: 1 оставляет только самое сильное, "
               f"5 берёт почти всё.")
    return out


def taste_lines(model: dict, stats: dict) -> list[str]:
    good = sum(s["good"] for s in stats.values())
    bad = sum(s["bad"] for s in stats.values())
    if good + bad == 0:
        return ["Оценок пока нет. Ставь клипам 👍 и 👎 в программе или реакциями в канале Telegram: считаются оценки "
                "всех подписчиков. На них программа и учится."]
    out = [f"Всего оценок: 👍 {good} и 👎 {bad}."]
    labels = int(model.get("labels") or 0)
    if labels < MIN_LABELS:
        out.append(f"Оценка клипов под твой вкус включится, когда наберётся {MIN_LABELS} оценок автоматических "
                   f"клипов. Сейчас их {labels}.")
        return out
    liked, disliked = taste(model)
    if liked:
        out.append("Чаще нравятся клипы, где " + "; где ".join(liked) + ".")
    if disliked:
        out.append("Реже нравятся клипы, где " + "; где ".join(disliked) + ".")
    if not liked and not disliked:
        out.append("Явных предпочтений по оценкам пока не видно: нравятся и не нравятся похожие моменты.")
    out.append("Оценка каждого нового клипа поправляется с учётом этого.")
    return out


def channel_lines(name: str, model: dict, stats: dict, live: dict | None) -> list[str]:
    st = stats.get(name) or {"clips": 0, "good": 0, "bad": 0, "manual": 0}
    out = [f"Клипов сделано: {st['clips']}. Понравилось {st['good']}, не понравилось {st['bad']}"
           + (f", вырезано кнопкой {st['manual']}." if st["manual"] else ".")]
    out.append(strictness(float((model.get("mult") or {}).get(name, 1.0)), st["manual"]))
    words = (model.get("words") or {}).get(name) or {}
    if words.get("pos"):
        out.append(f"Этот чат встречает удачные моменты так: {_quoted(words['pos'])}. Это считается реакцией.")
    if words.get("neg"):
        out.append(f"Реакцией не считается: {_quoted(words['neg'])}. Чаще бывает в неудачных клипах.")
    if live and live.get("state") in ("live", "ad", "pause"):
        bar = live.get("bar")
        if bar:
            out.append(f"Прямо сейчас планка этого чата: {float(bar):.1f} сообщения-реакции в секунду. "
                       f"Слабее этого клипом не станет.")
        else:
            out.append("Планка этого чата ещё набирается: программа присматривается к нему.")
        skips = {str(k): int(v) for k, v in (live.get("skips") or {}).items() if v}
        if skips:
            why = ", ".join(f"{k}: {v}" for k, v in sorted(skips.items(), key=lambda p: -p[1]))
            out.append(f"За этот эфир отсеяно всплесков чата: {sum(skips.values())}. Причины: {why}.")
    return out


def build(learner, cfg: dict, live: dict[str, dict] | None = None) -> list[tuple[str, list[str]]]:
    """Вся «библия» по разделам: [(заголовок, [строки])]."""
    live = live or {}
    model, stats = learner.model(), learner.stats()
    out = [("Как выбираются моменты", rules(cfg)), ("Твой вкус", taste_lines(model, stats))]
    names = dict.fromkeys(list(cfg.get("channels") or []) + sorted(stats) + sorted(live))
    for name in names:
        if name in stats or name in live or name in (model.get("mult") or {}):
            out.append((f"Канал {name}", channel_lines(name, model, stats, live.get(name))))
    notes = []
    for row in learner.diary(DIARY_SHOWN):
        when = time.strftime("%d.%m %H:%M", time.localtime(row["ts"]))
        notes.append(f"{when} · {row['channel'] + ': ' if row['channel'] else ''}{row['text']}")
    out.append(("Дневник: пробы и выводы", notes or ["Пока пусто. Первая запись появится после первой оценки клипа."]))
    return out


def text(sections: list[tuple[str, list[str]]]) -> str:
    lines = ["ЧЕМУ НАУЧИЛАСЬ ПРОГРАММА", "Обновлено " + time.strftime("%d.%m.%Y %H:%M"), ""]
    for title, body in sections:
        lines.append(title.upper())
        lines += ["• " + ln for ln in body]
        lines.append("")
    return "\n".join(lines)


def save(sections: list[tuple[str, list[str]]]) -> Path:
    """Кладёт «библию» текстовым файлом рядом с настройками и возвращает путь к нему."""
    path = config.data_dir() / FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text(sections), encoding="utf-8-sig")       # с меткой: Блокнот Windows откроет кириллицу верно
    tmp.replace(path)
    return path
