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
