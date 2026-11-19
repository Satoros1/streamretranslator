from streamclipper.config import parse_channel
from streamclipper.editor import choose_window, snap
from streamclipper.segments import Segment, select
from streamclipper.worker import pick_quality

CFG = {"clip_min": 25, "clip_max": 60, "lead_in": 20, "tail": 4, "manual_len": 45}


def test_parse_channel():
    assert parse_channel("https://www.twitch.tv/Buster") == "buster"
    assert parse_channel("twitch.tv/evelone2004?tt=1") == "evelone2004"
    assert parse_channel("m.twitch.tv/stray228/clips") == "stray228"
    assert parse_channel("recrent") == "recrent"
    assert parse_channel("https://youtube.com/watch?v=1") is None
    assert parse_channel("") is None


def test_window_is_one_continuous_piece_around_peak():
    job = {"kind": "laugh", "onset": 45.0, "peak": 52.0, "end": 66.0, "raw_dur": 80.0, "win_start": 0, "win_end": 80}
    s, e = choose_window(job, CFG)
    assert s == 25.0 and e == 70.0                 # подводка 20 с, момент, хвост 4 с
    assert s < job["onset"] < job["peak"] < e


def test_short_reaction_is_padded_to_minimum():
    job = {"kind": "laugh", "onset": 45.0, "peak": 47.0, "end": 49.0, "raw_dur": 80.0, "win_start": 0, "win_end": 80}
    s, e = choose_window(job, {**CFG, "clip_min": 40})
    assert round(e - s) == 40 and s < 45 < e


def test_long_hype_is_capped_and_keeps_peak():
    job = {"kind": "hype", "onset": 45.0, "peak": 120.0, "end": 130.0, "raw_dur": 150.0, "win_start": 0, "win_end": 150}
    s, e = choose_window(job, CFG)
    assert round(e - s) == 60 and s < 120 < e


def test_does_not_overlap_previous_clip():
    job = {"kind": "laugh", "onset": 45.0, "peak": 50.0, "end": 60.0, "raw_dur": 80.0, "win_start": 0, "win_end": 80,
           "not_before": 40.0}
    s, e = choose_window(job, CFG)
    assert s == 40.0 and e <= 80


def test_manual_clip_takes_last_seconds():
    job = {"kind": "manual", "onset": 70, "peak": 72, "end": 80, "raw_dur": 80.0, "win_start": 0, "win_end": 80}
    assert choose_window(job, CFG) == (35.0, 80)


def test_snap_moves_cut_into_silence():
    loud = [1000.0] * 100
    loud[42] = 5.0                                 # пауза в речи на 10.5 с
    assert abs(snap(10.0, loud) - 10.6) < 0.2
    assert snap(10.0, []) == 10.0


def segs(run, start, n):
    return [Segment(f"r{run}_{i}", run, start + 4 * i, start + 4 * i + 4, 1) for i in range(n)]


def test_never_cuts_across_an_ad_break():
    all_segs = segs(1, 1000, 20) + segs(2, 1200, 20)          # между прогонами была реклама
    chosen, s, e = select(all_segs, 1050, 1230, anchor=1215)   # пик после рекламы
    assert chosen and {c.run for c in chosen} == {2}
    assert s == 1200 and e == 1230
    chosen, s, e = select(all_segs, 1040, 1100, anchor=1070)   # пик до рекламы
    assert {c.run for c in chosen} == {1} and e == 1080
    assert select(all_segs, 1100, 1150, anchor=1140)[0] == []  # пик пришёлся на саму рекламу


def test_pick_quality():
    names = ["audio_only", "160p", "360p", "480p", "720p60", "1080p60", "worst", "best"]
    assert pick_quality(names, "best") == "best"
    assert pick_quality(names, "720") == "720p60"
    assert pick_quality(names, "1080") == "1080p60"
    assert pick_quality(["360p", "720p", "720p60", "best"], "720") == "720p60"
    assert pick_quality(["1080p60", "best"], "480") == "1080p60"
