import random

from streamclipper.detector import EARLY, MIN_DUR, RARE, RARE_MIN, SENS, Detector
from streamclipper.lexicon import classify, normalize


def test_russian_laughter():
    for s in ["ахахахах", "АХАХАХА", "хахаха", "пхпхпхп", "ору", "ОРУУУ", "я ору", "KEKW", "LUL", "OMEGALUL",
              "кек", "лол", "ржу", "xDDD", "хд", "вхвхвхв", "АХАХАХ что он несет", "streamerKEKW", "😂😂",
              "ахах", "аххахахах", "умер", "ъхъхъхъ", "азаза", "рофл", "pepeLaugh", "ICANT"]:
        assert classify(s)[0], s


def test_not_laughter():
    for s in ["привет всем", "какая игра?", "!drop", "когда стрим", "хорошо", "сахар", "мах", "храм",
              "!команда ахахах", "поехали"]:
        assert not classify(s)[0], s


def test_hype_and_clip():
    assert classify("ЧТО ЭТО БЫЛО")[1]
    assert classify("жесть")[1]
    assert classify("???")[1]
    assert classify("в нарезку")[2]
    assert classify("клипните это")[2]


def test_normalize():
    assert normalize("АХАХАХАХАХАХ") == normalize("ахахах")
    assert normalize("KEKW KEKW KEKW") == "KEKW"


def feed(det, rng, t0, seconds, rate, laugh_share=0.05):
    filler = ["привет", "го катку", "какой ранг", "норм", "стример топ", "а что за игра", "да", "нет"]
    laughs = ["АХАХАХАХ", "ахахах", "KEKW", "ору", "пхпхпх", "ХАХАХА что он несёт", "LUL"]
    events = []
    for s in range(seconds):
        t = t0 + s
        n = max(0, int(rng.gauss(rate, rate ** 0.5)))
        for i in range(n):
            text = rng.choice(laughs) if rng.random() < laugh_share else rng.choice(filler)
            det.add_message(t + i / (n + 1), f"u{rng.randint(1, 999)}", text)
        ev = det.tick(t + 1.0)
        if ev:
            events.append(ev)
    return events


def test_spike_gives_exactly_one_event():
    rng = random.Random(1)
    det = Detector(3)
    t0 = 1_000_000.0
    ev = feed(det, rng, t0, 300, 12)                       # обычный чат 12 сообщ/с
    assert ev == []
    ev = feed(det, rng, t0 + 300, 20, 45, laugh_share=0.8)  # 20 секунд ржача
    ev += feed(det, rng, t0 + 320, 60, 12)
    assert len(ev) == 1, ev
    e = ev[0]
    assert e.kind == "laugh"
    assert t0 + 294 <= e.onset <= t0 + 304
    assert t0 + 315 <= e.end <= t0 + 330
    assert e.score >= 60
    assert e.top and e.top[0] in ("АХАХАХ", "KEKW", "ору", "LUL")


def test_command_spam_is_ignored():
    rng = random.Random(2)
    det = Detector(3)
    t0 = 2_000_000.0
    assert feed(det, rng, t0, 200, 10) == []
    events = []
    for s in range(20):                                    # все дружно пишут !drop
        for i in range(60):
            det.add_message(t0 + 200 + s + i / 61, f"u{i}", "!drop")
        e = det.tick(t0 + 201 + s)
        if e:
            events.append(e)
    events += feed(det, rng, t0 + 220, 40, 10)
    assert events == []


def test_quiet_chat_no_false_positives():
    rng = random.Random(3)
    det = Detector(3)
    assert feed(det, rng, 3_000_000.0, 900, 6, laugh_share=0.08) == []


def test_two_bursts_close_together_merge():
    rng = random.Random(4)
    det = Detector(3)
    t0 = 4_000_000.0
    feed(det, rng, t0, 300, 15)
    ev = feed(det, rng, t0 + 300, 12, 50, laugh_share=0.8)
    ev += feed(det, rng, t0 + 312, 4, 15)                   # короткая передышка
    ev += feed(det, rng, t0 + 316, 12, 50, laugh_share=0.8)
    ev += feed(det, rng, t0 + 328, 60, 15)
    assert len(ev) == 1


# --- строгость отбора: смешок не клип -----------------------------------------
def stream(det, minutes, rate=12, seed=3, chuckle_every=100, bigs=()):
    """Живой чат: каждые chuckle_every секунд коротко посмеивается, в моменты bigs (секунды) ржёт по-настоящему."""
    filler = ["привет", "го катку", "какой ранг", "норм", "стример топ", "а что за игра", "да", "нет"]
    laughs = ["АХАХАХАХ", "ахахах", "KEKW", "ору", "пхпхпх", "ХАХАХА что он несёт", "LUL"]
    rng = random.Random(seed)
    plan = {}
    for k in range(60, minutes * 60, chuckle_every):
        for s in range(k, k + 4):
            plan[s] = (rate * 1.6, 0.45)                 # смешок: 4 секунды, чат чуть оживился
    for b in bigs:
        for s in range(b, b + 18):
            plan[s] = (rate * 4, 0.8)                    # момент: 18 секунд, чат вчетверо быстрее и почти весь ржёт
    t0 = 1_000_000.0
    events = []
    for s in range(minutes * 60):
        r, share = plan.get(s, (rate, 0.05))
        n = max(0, int(rng.gauss(r, r ** 0.5)))
        for i in range(n):
            text = rng.choice(laughs) if rng.random() < share else rng.choice(filler)
            det.add_message(t0 + s + i / (n + 1), f"u{rng.randint(1, 999)}", text)
        ev = det.tick(t0 + s + 1.0)
        if ev:
            events.append(ev.onset - t0)
    return events


BIGS = (20 * 60 + 30, 33 * 60 + 15, 47 * 60 + 40)


def caught(events, moment):
    return any(moment - 12 <= e <= moment + 18 for e in events)


def test_chuckles_used_to_become_clips():
    """Так было: чат «чуть посмеялся», и это уже клип. Держим старое поведение под рукой, чтобы видеть разницу."""
    det = Detector(3)
    det.rare = 0.0
    events = stream(det, 50, bigs=BIGS)
    assert len(events) > 20


def test_only_real_moments_become_clips():
    det = Detector(3)
    events = stream(det, 50, bigs=BIGS)
    assert all(caught(events, b) for b in BIGS), events      # настоящие моменты все на месте
    assert len(events) <= len(BIGS) + 1, events              # а смешки клипами больше не становятся
    assert sum(det.skipped.values()) >= 15
    assert det.last_skip


def test_the_bar_is_the_channels_own_strongest_minutes():
    det = Detector(3)
    assert det.rare_bar(10_000) is None                      # ещё нечего сравнивать
    for m in range(RARE_MIN - 1):
        det.minute_peaks.append((m, 1.0))
    assert det.rare_bar(10_000) is None
    det.minute_peaks.clear()
    for m in range(50):
        det.minute_peaks.append((m, float(m)))               # в минуту m самая сильная реакция была m в секунду
    assert det.rare_bar(50 * 60) == 45.0                     # 92% минут слабее: планка на 46-й из 50
    assert det.rare_bar(20 * 60) == 18.0                     # минуты самого всплеска и после него не считаются
    det.set_sensitivity(1)
    assert det.rare_bar(50 * 60) == 48.0                     # строже чувствительность: выше планка
    det.set_sensitivity(5)
    assert det.rare_bar(50 * 60) is None                     # «брать почти всё»: без сравнения
    assert all(0 <= RARE[k] < 1 for k in SENS) and RARE[1] > RARE[3] > RARE[5]


def test_first_minutes_take_only_the_obvious():
    """Программа только запустилась, сравнивать не с чем: проходит лишь то, что сильно выше порога."""
    det = Detector(3)
    weak = {"onset": 0.0, "peak_z": det.z_react * EARLY - 0.1, "peak_rate": 5.0}
    strong = dict(weak, peak_z=det.z_react * EARLY + 0.1)
    assert det._too_weak(weak) and not det._too_weak(strong)
    for m in range(40):
        det.minute_peaks.append((m, 3.0))
    assert not det._too_weak(dict(weak, onset=40 * 60.0))     # история есть: решает сравнение с ней (5 > 3)
    assert "бывает сильнее" in det._too_weak(dict(strong, onset=40 * 60.0, peak_rate=3.0))
    assert MIN_DUR >= 6


def test_restart_forgets_what_was_strong():
    det = Detector(3)
    for m in range(40):
        det.minute_peaks.append((m, 9.0))
    det.restart(2_000_000.0)
    assert not det.minute_peaks and det.rare_bar(3_000_000) is None
