import random

from streamclipper.detector import Detector
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
