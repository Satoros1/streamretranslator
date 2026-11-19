import random

from streamclipper.detector import Detector
from streamclipper.learning import Learner, tokenize


def test_tokenize():
    assert tokenize("АААААА buster5Head ору!!!") == {"аа", "buster", "head", "ору"}


def test_no_labels_means_no_change(tmp_path):
    L = Learner(str(tmp_path / "l.db"))
    assert L.predict("ch", {"z": 5, "share": 0.5}, 70) == 70
    assert L.channel_mult("ch") == 1.0


def test_model_learns_owner_taste(tmp_path):
    """Владельцу нравятся моменты со смехом и не нравятся «хайповые» без смеха: модель должна это усвоить."""
    L = Learner(str(tmp_path / "l.db"))
    rng = random.Random(0)
    for i in range(40):
        funny = i % 2 == 0
        f = {"z": rng.uniform(4, 9), "share": rng.uniform(0.3, 0.8), "laugh": 0.9 if funny else 0.1,
             "dur": 20, "msgs": 200, "uniq": 0.7}
        L.record(f"c{i}", "ch", "laugh" if funny else "hype", f, ["мем"] if funny else ["рейд"], 60, 60)
        L.set_clip(f"c{i}", f"/x/{i}.mp4", "t", "d", 60)
        L.label(f"c{i}", funny)
    good = L.predict("ch", {"z": 6, "share": 0.5, "laugh": 0.9, "dur": 20, "msgs": 200, "uniq": 0.7}, 60)
    bad = L.predict("ch", {"z": 6, "share": 0.5, "laugh": 0.1, "dur": 20, "msgs": 200, "uniq": 0.7}, 60)
    assert good > 60 > bad and good - bad > 25
    pos, neg = L.channel_words("ch")
    assert "мем" in pos and "рейд" in neg
    assert "Обучение" in L.summary()


def test_sensitivity_adapts(tmp_path):
    L = Learner(str(tmp_path / "l.db"))
    for i in range(6):
        L.record(f"b{i}", "spam", "hype", {"z": 5}, [], 50, 50)
        L.label(f"b{i}", False)
        L.record(f"m{i}", "missed", "manual", {}, ["локальныймем"], 0, 0)
    L.train()
    assert L.channel_mult("spam") > 1.2          # много мусора: строже
    assert L.channel_mult("missed") < 0.8        # пропускали моменты: чувствительнее
    assert "локальныймем" in L.channel_words("missed")[0]


def test_telegram_label_does_not_override_app(tmp_path):
    L = Learner(str(tmp_path / "l.db"))
    L.record("a", "ch", "laugh", {}, [], 50, 50)
    L.set_clip("a", "/x/a.mp4", "t", "d", 50)
    assert L.label_path("/x/a_9x16.mp4", True, "app")
    assert not L.label_path("/x/a.mp4", False, "telegram")
    assert L.recent()[0]["label"] == 1


def test_learned_channel_word_triggers_detection():
    """Чат реагирует своим локальным словом, которого нет в общем словаре."""
    def run(words):
        rng = random.Random(5)
        det = Detector(3)
        det.set_words(words, set())
        t0, events = 5_000_000.0, []
        for s in range(400):
            hot = 300 <= s < 318
            for i in range(40 if hot else 12):
                text = "бабиджон" if hot and rng.random() < 0.8 else rng.choice(["привет", "го", "норм", "да"])
                det.add_message(t0 + s + i / 50, f"u{rng.randint(1, 900)}", text)
            e = det.tick(t0 + s + 1.0)
            if e:
                events.append(e)
        return events
    # без обучения: поток вырос в 3 раза, но «реакций» нет, решает только общий всплеск
    learned = run({"бабиджон"})
    assert len(learned) == 1 and "бабиджон" in learned[0].tokens
    assert learned[0].features["learned"] > 0.5
