"""Буфер записи: ни один кусочек не теряется, а время каждого известно точно."""
import os
import shutil
import subprocess
import time

import pytest

from streamclipper import segments
from streamclipper.worker import recorder_cmd


def make_run(folder, run, anchor, pieces, listed=None):
    """pieces: [(начало в записи, конец в записи, когда файл закрыт по часам)]. listed: сколько из них уже в списке."""
    pattern, listing = segments.run_files(folder, run, anchor)
    lines = []
    for i, (cs, ce, closed) in enumerate(pieces):
        path = pattern.replace("%%", "%") % i
        with open(path, "wb") as f:
            f.write(b"x" * 100)
        os.utime(path, (closed, closed))
        if listed is None or i < listed:
            lines.append(f"{os.path.basename(path)},{cs:.6f},{ce:.6f}\n")
    with open(listing, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return listing


def test_pieces_born_in_the_same_second_are_all_kept(tmp_path):
    # Twitch отдал видео пачками по 6 секунд: кусочки 2 и 3 закрыты в одну и ту же секунду
    t0 = 1_800_000_000.0
    make_run(tmp_path, 1, t0, [(0.0, 3.99, t0 + 0.1), (4.02, 7.99, t0 + 6.1), (8.02, 11.99, t0 + 12.1),
                               (12.02, 15.99, t0 + 12.1), (16.02, 19.99, t0 + 18.1)])
    segs = segments.scan(tmp_path)
    assert len(segs) == 5
    assert [round(b.start - a.start) for a, b in zip(segs, segs[1:])] == [4, 4, 4, 4]      # ровная лента времени
    assert all(a.end == b.start for a, b in zip(segs, segs[1:]))
    assert abs(segs[0].start - (t0 - 3.89)) < 0.05          # отсчёт от самого «быстрого» кусочка, без задержек сети


def test_piece_still_being_written_continues_the_timeline(tmp_path):
    t0 = 1_800_000_000.0
    make_run(tmp_path, 3, t0, [(0.0, 4.0, t0 + 4), (4.0, 8.0, t0 + 8), (8.0, 0.0, t0 + 9.5)], listed=2)
    segs = segments.scan(tmp_path)
    assert [round(s.start - t0) for s in segs] == [0, 4, 8]
    assert segs[-1].end >= segs[-1].start and segs[-1].run == 3


def test_fresh_run_without_list_starts_at_its_launch_time(tmp_path):
    t0 = 1_800_000_000.0
    listing = make_run(tmp_path, 2, t0, [(0.0, 0.0, t0 + 2.5)], listed=0)
    os.remove(listing)
    segs = segments.scan(tmp_path)
    assert len(segs) == 1 and segs[0].start == t0 and segs[0].end == t0 + 2.5


def test_runs_are_separate_and_ordered(tmp_path):
    t0 = 1_800_000_000.0
    make_run(tmp_path, 2, t0 + 100, [(0.0, 4.0, t0 + 104), (4.0, 8.0, t0 + 108)])
    make_run(tmp_path, 1, t0, [(1.4, 5.4, t0 + 4), (5.4, 9.4, t0 + 8)])
    segs = segments.scan(tmp_path)
    assert [s.run for s in segs] == [1, 1, 2, 2]
    assert round(segs[0].start - t0, 1) == 0.0             # запись может начинаться не с нуля, время от этого не едет
    assert round(segs[2].start - t0) == 100
    chosen, start, end = segments.select(segs, t0 - 50, t0 + 300, t0 + 103)
    assert [s.run for s in chosen] == [2, 2] and round(end - start) == 8      # через разрыв клип не идёт


def test_prune_drops_old_pieces_and_their_lists(tmp_path):
    now = time.time()
    old_list = make_run(tmp_path, 1, now - 900, [(0.0, 4.0, now - 896), (4.0, 8.0, now - 892)])
    os.utime(old_list, (now - 892, now - 892))
    new_list = make_run(tmp_path, 2, now - 8, [(0.0, 4.0, now - 4), (4.0, 8.0, now)])
    left = segments.prune(tmp_path, 360, now)
    assert left == 200
    assert not os.path.exists(old_list) and os.path.exists(new_list)
    assert sorted(n for n in os.listdir(tmp_path) if n.endswith(".ts")) == [
        os.path.basename(s.path) for s in segments.scan(tmp_path)]
    segments.wipe(tmp_path)
    assert os.listdir(tmp_path) == []


def test_percent_in_folder_name_is_safe(tmp_path):
    folder = tmp_path / "100% видео"
    pattern, listing = segments.run_files(folder, 7, 1_800_000_000.0)
    assert "100%% видео" in pattern and pattern.endswith("r00007_1800000000000_%06d.ts")
    assert "100% видео" in listing and listing.endswith("r00007_1800000000000.csv")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нужен ffmpeg")
def test_stream_arriving_in_bursts_is_recorded_whole(tmp_path):
    """Настоящий ffmpeg: видео приходит пачками по 6 секунд (так его отдаёт Twitch), на диске должно быть всё."""
    src = tmp_path / "src.ts"
    made = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30",
                           "-f", "lavfi", "-i", "sine=frequency=440", "-t", "24", "-c:v", "libx264", "-preset", "ultrafast",
                           "-g", "60", "-keyint_min", "60", "-sc_threshold", "0", "-c:a", "aac", "-f", "mpegts", str(src)])
    if made.returncode != 0:
        pytest.skip("в этом ffmpeg нет кодировщика для тестового видео")
    data = src.read_bytes()
    buf = tmp_path / "buf"
    buf.mkdir()
    ff = subprocess.Popen(recorder_cmd(buf, 1, "ts"), stdin=subprocess.PIPE)
    step = len(data) // 4 // 188 * 188                     # четыре пачки примерно по 6 секунд видео
    for i in range(0, len(data), step):
        ff.stdin.write(data[i:i + step])
        ff.stdin.flush()
        time.sleep(1.2)
    ff.stdin.close()
    assert ff.wait(30) == 0
    segs = segments.scan(buf)
    assert len(segs) == 6                                   # 24 секунды = шесть кусочков по 4 с, ни один не затёрт
    assert 23.0 < segs[-1].end - segs[0].start < 25.0
    assert sum(s.size for s in segs) > 0.9 * len(data)


# --- дыры в записи: интернет не успел скачать кусок видео -----------------------
def test_hole_in_the_recording_is_seen_and_never_inside_a_clip(tmp_path):
    t0 = 1_800_000_000.0
    # 12 секунд видео, потом 7 секунд пропало (streamlink не успел их скачать), потом запись идёт дальше
    make_run(tmp_path, 1, t0, [(0.0, 4.0, t0 + 4), (4.0, 8.0, t0 + 8), (8.0, 12.0, t0 + 12),
                               (19.0, 23.0, t0 + 23), (23.0, 27.0, t0 + 27), (27.0, 31.0, t0 + 31)])
    segs = segments.scan(tmp_path)
    assert round(segs[2].end - t0) == 12 and round(segs[3].start - t0) == 19      # щель осталась на месте
    parts = segments.pieces(segs)
    assert [len(p) for p in parts] == [3, 3]
    chosen, start, end = segments.select(segs, t0 - 30, t0 + 60, t0 + 25)
    assert [round(s.start - t0) for s in chosen] == [19, 23, 27]                # клип только после дыры
    assert round(start - t0) == 19 and round(end - t0) == 31
    chosen, start, end = segments.select(segs, t0 - 30, t0 + 60, t0 + 6)
    assert round(start - t0) == 0 and round(end - t0) == 12                      # или только до неё
    # момент пришёлся на саму дыру: по кнопке берём ближайшее видео, а сам по себе такой клип не нужен
    assert segments.select(segs, t0, t0 + 60, t0 + 16, slack=15.0)[0]
    assert segments.select(segs, t0, t0 + 60, t0 + 16, slack=2.0)[0] == []


def test_tiny_gaps_between_pieces_are_not_holes(tmp_path):
    t0 = 1_800_000_000.0
    make_run(tmp_path, 1, t0, [(0.0, 3.97, t0 + 4), (4.02, 7.99, t0 + 8), (8.02, 12.0, t0 + 12)])
    segs = segments.scan(tmp_path)
    assert len(segments.pieces(segs)) == 1 and all(a.end == b.start for a, b in zip(segs, segs[1:]))
    assert 0.3 <= segments.HOLE <= 1.5


def test_lagging_recording_does_not_pretend_to_have_video(tmp_path):
    """Запись отстала от эфира на полминуты. Кусочек, который ещё пишется, раньше «растягивался» до текущего
    времени, и клип из него выходил в 2 секунды вместо тридцати."""
    t0 = 1_800_000_000.0
    make_run(tmp_path, 1, t0, [(0.0, 4.0, t0 + 4), (4.0, 8.0, t0 + 8), (8.0, 0.0, t0 + 40)], listed=2)
    segs = segments.scan(tmp_path)
    assert round(segs[-1].start - t0) == 8
    assert segs[-1].end - segs[-1].start <= 1.5 * segments.SEG_TIME              # а не 32 секунды
    assert segments.select(segs, t0, t0 + 60, t0 + 35, slack=2.0)[0] == []       # видео того момента ещё не пришло


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нужен ffmpeg")
def test_real_ffmpeg_shows_the_hole(tmp_path):
    """Настоящий ffmpeg: из потока выпало 6 секунд. В списке кусочков это должно быть видно."""
    src = tmp_path / "src.ts"
    enc = ["-c:v", "libx264", "-preset", "ultrafast", "-g", "60", "-keyint_min", "60", "-sc_threshold", "0", "-c:a", "aac"]
    made = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30",
                           "-f", "lavfi", "-i", "sine=frequency=440", "-t", "30", *enc, "-f", "mpegts", str(src)])
    if made.returncode != 0:
        pytest.skip("в этом ffmpeg нет кодировщика для тестового видео")
    a, b = tmp_path / "a.ts", tmp_path / "b.ts"
    cut = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    assert subprocess.run(cut + ["-i", str(src), "-t", "12", "-c", "copy", "-copyts", "-f", "mpegts", str(a)]).returncode == 0
    assert subprocess.run(cut + ["-ss", "18", "-i", str(src), "-c", "copy", "-copyts", "-f", "mpegts", str(b)]).returncode == 0
    buf = tmp_path / "buf"
    buf.mkdir()
    ff = subprocess.Popen(recorder_cmd(buf, 1, "ts"), stdin=subprocess.PIPE)
    ff.stdin.write(a.read_bytes() + b.read_bytes())
    ff.stdin.close()
    assert ff.wait(30) == 0
    parts = segments.pieces(segments.scan(buf))
    assert len(parts) == 2, [(round(s.start, 1), round(s.end, 1)) for p in parts for s in p]
    gap = parts[1][0].start - parts[0][-1].end
    assert 4.0 < gap < 9.0
    assert 8.0 < parts[0][-1].end - parts[0][0].start < 13.0
