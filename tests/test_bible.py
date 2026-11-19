"""«Библия»: программа простыми словами записывает, как выбирает моменты и чему научилась на оценках."""
import random

import pytest

from streamclipper import bible, config
from streamclipper.learning import DIARY_KEEP, MIN_LABELS, Learner

CFG = {"sensitivity": 3, "channels": ["gta_live"]}


def _clip(L, cid, channel="gta_live", good=None, title="угон коляски", kind="laugh", tokens=(), feats=None, src="app"):
    L.record(cid, channel, kind, feats or {"z": 6, "share": 0.5, "laugh": 0.8, "dur": 20, "msgs": 150, "uniq": 0.7},
             list(tokens), 60, 60)
    L.set_clip(cid, f"/clips/{cid}.mp4", title, "описание", 60)
    if good is not None:
        L.label(cid, good, src)


def _notes(L, n=100):
    return [(r["channel"], r["text"]) for r in L.diary(n)]


def test_every_rating_goes_to_the_diary(tmp_path):
    L = Learner(str(tmp_path / "l.db"))
    assert L.diary() == []
    _clip(L, "a", good=True)
    _clip(L, "b", good=False, title="рейд от ботов", src="telegram")
    notes = _notes(L)
    assert ("gta_live", "Проба: клип «рейд от ботов» не понравился (оценка реакциями в канале).") in notes
    assert ("gta_live", "Проба: клип «угон коляски» понравился (оценка в программе).") in notes
    assert notes[0][1].startswith("Проба: клип «рейд")              # новые записи сверху
    L.label("a", True)                                               # та же оценка ещё раз: новой записи нет
    L.label("a", None)                                               # оценку сняли: писать не о чем
    assert len(_notes(L)) == len(notes)


def test_conclusions_are_written_in_plain_words(tmp_path):
    L = Learner(str(tmp_path / "l.db"))
    for i in range(3):
        _clip(L, f"g{i}", good=True, tokens=["пепега"])
    notes = _notes(L)
    assert any(ch == "gta_live" and "Выучено слово «пепега»" in t and "считается реакцией" in t for ch, t in notes)
    for i in range(4):
        _clip(L, f"b{i}", good=False, tokens=["рейд"], title="наплыв ботов")
    notes = _notes(L)
    assert any("Не считается реакцией: «рейд»" in t for _c, t in notes)
    assert any("Отбор стал строже: теперь на" in t and "строже обычного" in t for _c, t in notes)
    assert sum("Выучено слово «пепега»" in t for _c, t in notes) == 1       # вывод пишется один раз, а не при каждой оценке
    # восьмая оценка: модель вкуса включается
    _clip(L, "last", good=True, tokens=["пепега"])
    assert L.model()["labels"] == MIN_LABELS
    assert any(c == "" and f"Набралось {MIN_LABELS} оценок" in t for c, t in _notes(L))


def test_diary_does_not_grow_forever(tmp_path):
    L = Learner(str(tmp_path / "l.db"))
    L.note_many([("ch", f"запись {i}") for i in range(DIARY_KEEP + 50)])
    rows = L.diary(10_000)
    assert len(rows) == DIARY_KEEP and rows[0]["text"] == f"запись {DIARY_KEEP + 49}"


def test_what_changed_after_a_rating():
    old = {"labels": 7, "w": [0.0] * 8, "mult": {"a": 1.04, "b": 0.9}, "words": {"a": {"pos": ["кек"], "neg": []}}}
    new = {"labels": 9, "w": [0.0, 0.0, 0.9, 0.0, 0.0, 0.0, -0.5, 0.0], "mult": {"a": 1.16, "b": 0.99},
           "words": {"a": {"pos": ["ору", "жиза"], "neg": ["рейд"]}}}
    got = bible.lessons(old, new)
    texts = [t for _c, t in got]
    assert ("", f"Набралось {MIN_LABELS} оценок: с этого момента оценка каждого нового клипа поправляется под твой вкус.") in got
    assert "Вывод по оценкам: чаще нравятся клипы, где чат именно смеётся, а не просто удивляется." in texts
    assert "Вывод по оценкам: реже нравятся клипы, где реагирует много разных зрителей." in texts
    assert any(c == "a" and t.startswith("Выучены слова «жиза», «ору»") for c, t in got)
    assert any(c == "a" and t.startswith("Больше не считается реакцией: «кек»") for c, t in got)
    assert any(c == "a" and t.startswith("Не считается реакцией: «рейд»") for c, t in got)
    assert ("a", "Отбор стал строже: теперь на 16% строже обычного. Причина: клипы, которые не понравились.") in got
    assert ("b", "Отбор вернулся к обычному.") in got
    assert bible.lessons(new, new) == []                             # ничего не изменилось: писать нечего
    assert bible.lessons({}, {}) == []


def test_the_whole_bible(tmp_path):
    L = Learner(str(tmp_path / "l.db"))
    empty = dict(bible.build(L, CFG))
    assert "92%" in " ".join(empty["Как выбираются моменты"]) and "8 секунд" in " ".join(empty["Как выбираются моменты"])
    assert empty["Твой вкус"][0].startswith("Оценок пока нет")
    assert empty["Дневник: пробы и выводы"][0].startswith("Пока пусто")
    assert "Канал gta_live" not in empty                              # о канале пока нечего сказать
    assert "почти любая реакция" in " ".join(dict(bible.build(L, dict(CFG, sensitivity=5)))["Как выбираются моменты"])

    rng = random.Random(1)
    for i in range(24):
        funny = i % 2 == 0
        _clip(L, f"c{i}", good=funny, tokens=["пепега"] if funny else ["рейд"], title="смешно" if funny else "скучно",
              feats={"z": rng.uniform(4, 9), "share": 0.5, "laugh": 0.9 if funny else 0.1, "dur": 20, "msgs": 200, "uniq": 0.7})
    L.record("m1", "gta_live", "manual", {}, [], 0, 0)
    L.set_clip("m1", "/clips/m1.mp4", "Клип вручную", "", 0)
    live = {"gta_live": {"state": "live", "bar": 3.4, "skips": {"у этого чата бывает сильнее": 12, "чат отреагировал слишком коротко": 4}},
            "other": {"state": "offline", "bar": 0, "skips": {}}}
    full = dict(bible.build(L, CFG, live))
    taste = " ".join(full["Твой вкус"])
    assert "👍 12 и 👎 12" in taste and "Чаще нравятся клипы, где чат именно смеётся" in taste
    ch = " ".join(full["Канал gta_live"])
    assert "Клипов сделано: 25. Понравилось 12, не понравилось 12, вырезано кнопкой 1." in ch
    assert "«пепега»" in ch and "Реакцией не считается: «рейд»" in ch
    assert "планка этого чата: 3.4" in ch and "отсеяно всплесков чата: 16" in ch
    assert ch.index("у этого чата бывает сильнее: 12") < ch.index("слишком коротко: 4")          # частые причины первыми
    assert "строже обычного" in ch or "мягче обычного" in ch or "Отбор обычный" in ch
    other = " ".join(full["Канал other"])
    assert "Клипов сделано: 0" in other and "планка" not in other     # эфира нет: о планке не говорим
    diary = full["Дневник: пробы и выводы"]
    assert 24 <= len(diary) <= bible.DIARY_SHOWN and " · gta_live: " in diary[0]

    text = bible.text(list(full.items()))
    assert text.startswith("ЧЕМУ НАУЧИЛАСЬ ПРОГРАММА") and "ТВОЙ ВКУС" in text and "• Всего оценок" in text


def test_bible_as_a_file(tmp_path, monkeypatch):
    monkeypatch.setenv("STREAMCLIPPER_HOME", str(tmp_path / "home"))
    L = Learner()
    _clip(L, "a", good=True)
    path = bible.save(bible.build(L, config.load()))
    assert path.name == bible.FILE and path.parent == config.data_dir()
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")                           # Блокнот Windows покажет русские буквы верно
    assert "Проба: клип «угон коляски» понравился" in raw.decode("utf-8-sig")
    bible.save(bible.build(L, config.load()))                        # второй раз: файл просто обновляется
    assert not list(path.parent.glob("*.tmp"))


def test_strictness_in_words():
    assert bible.strictness(1.0).startswith("Отбор обычный")
    assert bible.strictness(1.22) == "Отбор строже обычного на 22%: так программа ответила на клипы, которые не понравились."
    assert "вырезались кнопкой" in bible.strictness(0.8, manual=3) and "на 20%" in bible.strictness(0.8, manual=3)
    assert "чаще нравятся" in bible.strictness(0.9)


def test_learning_page_in_the_window(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("STREAMCLIPPER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel
    app = QApplication.instance() or QApplication([])
    from streamclipper import gui
    from streamclipper.supervisor import Supervisor, WorkerHandle
    sup = Supervisor()
    h = sup.workers["gta_live"] = WorkerHandle("gta_live")
    h.status = {"state": "live", "bar": 2.5, "skips": {"чат отреагировал слишком коротко": 3}}
    snap = sup.snapshot()[0]
    assert snap["bar"] == 2.5 and snap["skips"] == {"чат отреагировал слишком коротко": 3}
    _clip(sup.learner, "a", good=False, title="скучный момент")
    win = gui.MainWindow(sup)
    assert [b.text().strip() for b in win.nav] == ["Стримы", "Клипы", "Обучение", "Telegram", "Журнал", "Настройки"]
    win.go(win.PAGE_LEARN)
    app.processEvents()
    shown = " ".join(w.text() for w in win.stack.currentWidget().findChildren(QLabel))
    assert "Как выбираются моменты" in shown and "Канал gta_live" in shown and "планка этого чата: 2.5" in shown
    assert "Проба: клип «скучный момент» не понравился" in shown
    before = win._learn_shown
    win.refresh_learn()
    assert win._learn_shown is before                                # ничего не изменилось: страница не перерисовывается
    _clip(sup.learner, "b", good=True, title="угон коляски")
    win.refresh_learn()
    app.processEvents()
    shown = " ".join(w.text() for w in win.stack.currentWidget().findChildren(QLabel))
    assert "Проба: клип «угон коляски» понравился" in shown
    win.really_quit = True
    win.close()
    sup.shutdown()
