"""Проверка на настоящем Twitch (запускается в GitHub Actions на Windows, уже собранной программой).

Находит русскоязычные стримы, которые идут прямо сейчас, пишет их несколько минут, жмёт «Клип сейчас»
и проверяет, что клип действительно появился. Twitch может не пускать серверы GitHub, поэтому
проверка не блокирует сборку: её результат читается в логах.
"""
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")     # в Windows вывод иначе не умеет русские буквы

FALLBACK = ["buster", "evelone2004", "bratishkinoff", "zubarefff", "recrent", "stray228", "dmitry_lixxx",
            "by_owl", "t2x2", "mazellovvv", "pokanoname", "hellyeahplay"]


def live_ru(n: int = 4) -> list[str]:
    """Самые популярные русскоязычные стримы прямо сейчас. Пробуем два варианта запроса."""
    queries = [
        "query{streams(first:%d, options:{broadcasterLanguages:[RU]}){edges{node{broadcaster{login} viewersCount}}}}" % n,
        "query{streams(first:%d, languages:[RU]){edges{node{broadcaster{login} viewersCount}}}}" % n,
    ]
    for q in queries:
        req = urllib.request.Request("https://gql.twitch.tv/gql", data=json.dumps({"query": q}).encode(),
                                     headers={"Client-ID": "kimne78kx3ncx6brgo4mv6wki5h1ko",
                                              "Content-Type": "application/json"})
        try:
            d = json.load(urllib.request.urlopen(req, timeout=20))
            edges = [e["node"] for e in d["data"]["streams"]["edges"] if e["node"].get("broadcaster")]
            print("live RU streams from Twitch:", [(e["broadcaster"]["login"], e["viewersCount"]) for e in edges])
            if edges:
                return [e["broadcaster"]["login"] for e in edges]
        except Exception as e:
            print("GQL lookup failed:", e, str(locals().get("d"))[:300])
    return []


def lost_video(home: str) -> list[str]:
    """Всё ли полученное видео легло на диск. Сверяем по отчётам записи, пока буфер ещё ничего не удалял."""
    bad = []
    pat = re.compile(r"за (\d+) с получено ([\d.]+) МБ видео, на диске (\d+) кусков \(([\d.]+) МБ\)")
    for log in sorted(glob.glob(os.path.join(home, "logs", "worker_*.log"))):
        name = os.path.basename(log)[7:-4]
        with open(log, encoding="utf-8", errors="replace") as f:
            rows = [(int(m[1]), float(m[2]), float(m[4])) for m in pat.finditer(f.read()) if int(m[1]) <= 215]
        if not rows:
            continue
        sec, got, disk = rows[-1]
        print("RECORDED %s: %.1f of %.1f MB in %d s (%d%%)" % (name, disk, got, sec, round(100 * disk / max(got, 0.1))))
        if got > 10 and disk < 0.85 * got:
            bad.append("%s: on disk only %d%%" % (name, round(100 * disk / got)))
    return bad


def jumpy_clips(found: list[str]) -> list[str]:
    """Клип обязан быть одним цельным куском: между соседними кадрами не должно быть дыр."""
    bad = []
    for p in found:
        if p.endswith("_9x16.mp4"):
            continue                                        # вертикальный перекодирован, дыру в нём не увидеть; смотрим исходный
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time",
                            "-of", "csv=p=0", p], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        pts = sorted(float(x) for x in r.stdout.decode("ascii", "replace").replace(",", " ").split() if x[:1].isdigit())
        gap = max((b - a for a, b in zip(pts, pts[1:])), default=0.0)
        print("CONTINUITY %s: %d frames, biggest gap %.3f s" % (os.path.basename(p), len(pts), gap))
        if len(pts) < 100 or gap > 0.5:
            bad.append("%s: gap %.1f s" % (os.path.basename(p), gap))
    return bad


def main() -> int:
    exe = sys.argv[1]
    seconds = sys.argv[2] if len(sys.argv) > 2 else "240"
    home = tempfile.mkdtemp(prefix="sc_live_")
    clips = os.path.join(home, "clips")
    with open(os.path.join(home, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"clips_dir": clips, "whisper": True, "whisper_model": "base", "manual_len": 30,
                   "auto_update_streamlink": True, "max_streams": 8, "clip_format": "both"}, f)
    channels = sys.argv[3:] or (live_ru(6) or FALLBACK)[:6]
    print("channels:", channels)
    env = dict(os.environ, STREAMCLIPPER_HOME=home, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    cmd = exe.split() + ["headless", seconds] + channels
    out_path = os.path.join(home, "headless.out")
    stuck = False
    with open(out_path, "wb") as out_f:                     # в файл, а не в память: вывод нужен, даже если программа зависла
        proc = subprocess.Popen(cmd, env=env, stdout=out_f, stderr=subprocess.STDOUT)
        try:
            proc.wait(int(seconds) + 900)
        except subprocess.TimeoutExpired:
            stuck = True
            if sys.platform == "win32":                     # вместе со всеми процессами записи и монтажа
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            proc.kill()
            proc.wait(30)
    with open(out_path, "rb") as f:
        text = f.read().decode("utf-8", "replace")
    print(text[-12000:])
    for log in glob.glob(os.path.join(home, "logs", "*.log")):
        print("=====", os.path.basename(log))
        with open(log, encoding="utf-8", errors="replace") as f:
            print(f.read()[-6000:])
    for dump in glob.glob(os.path.join(home, "logs", "dump_*.bin")):     # что именно прислал Twitch: формат и кодеки
        pr = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                             "format=format_name:stream=index,codec_type,codec_name,profile,width,height,r_frame_rate",
                             "-of", "compact", dump], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        with open(dump, "rb") as f:
            head = f.read(16)
        print("DUMP", os.path.basename(dump), os.path.getsize(dump), "bytes, head", head.hex(),
              "|", pr.stdout.decode("utf-8", "replace").strip().replace("\n", " ; ")[:900])
    found = glob.glob(os.path.join(clips, "**", "*.mp4"), recursive=True)
    for p in found:
        print("CLIP", os.path.getsize(p) // 1024, "KB", p)
    for p in glob.glob(os.path.join(clips, "**", "*.txt"), recursive=True):
        with open(p, encoding="utf-8") as f:
            print("-----", os.path.basename(p), "\n" + f.read())
    out = os.environ.get("SMOKE_OUT")
    if out and found:
        import shutil
        os.makedirs(out, exist_ok=True)
        for p in sorted(found, key=os.path.getsize)[:2]:
            shutil.copy(p, out)
        tall = [p for p in found if p.endswith("_9x16.mp4")]
        print("vertical clips:", len(tall), "of", len(found))
        for i, p in enumerate(tall[:3]):                    # кадры из вертикальных клипов: посмотреть чат на настоящем стриме
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "20", "-i", p, "-frames:v", "1",
                            "-vf", "scale=540:-2", os.path.join(out, "live_vertical_%d.jpg" % i)])
    # Хронология монтажа: успевает ли он за моментами. Печатается в конце, чтобы попасть в хвост лога.
    marks = ("пойман момент", "клип готов", "пропущен", "не удался", "не получился", "Запущено", "перезапускаю",
             "стрим закончился", "стрим в эфире", "интернет не успевает", "подошла очередь")
    caught = [ln for ln in text.splitlines() if "INFO supervisor" in ln and any(m in ln for m in marks)]
    print("TIMELINE (%d lines):" % len(caught))
    for ln in caught[:150]:
        print("  " + ln[11:19] + " " + ln.split("supervisor: ", 1)[-1][:110])
    left = re.search(r"UNFINISHED EDITS: (\d+)", text)
    print("moments caught: %d, clips made: %d, dropped as weakest: %d, left unedited: %s" % (
        sum("пойман момент" in ln for ln in caught), sum("клип готов" in ln for ln in caught),
        sum("Монтаж не успевает" in ln for ln in caught), left.group(1) if left else "0"))
    # Предел клипов в час: сколько моментов ждали очереди и сколько из них уступили более сильным.
    print("hourly limit: %d moments waited for their turn, %d passed over for stronger ones" % (
        sum("ждёт очереди" in ln for ln in caught),
        sum("в очереди есть сильнее" in ln or "обходили более сильные" in ln for ln in caught)))
    # Отбор: сколько всплесков чата программа сочла слишком слабыми для клипа и почему.
    weak: dict[str, int] = {}
    shown = 0
    for log in sorted(glob.glob(os.path.join(home, "logs", "*.log*"))):
        with open(log, encoding="utf-8", errors="replace") as f:
            for ln in f:
                if "не стал клипом: " in ln:
                    why = ln.split("не стал клипом: ", 1)[1].strip()
                    weak[why.split(" (")[0]] = weak.get(why.split(" (")[0], 0) + 1
                    if shown < 60:
                        shown += 1
                        print("  REJECTED %s %s: %s" % (ln[11:19], os.path.basename(log).split(".")[0], why[:110]))
    print("chat bursts rejected as too weak: %d %s" % (sum(weak.values()), weak or ""))
    # Проверка с зажатой скоростью: программа обязана сама заметить медленный интернет и снизить качество.
    cap = os.environ.get("STREAMCLIPPER_NET_CAP_KBPS") or ""
    lowered = sorted({ln.split("supervisor: ", 1)[-1].split(":")[0] for ln in caught if "дальше записываю в" in ln})
    print("SLOW NET: speed cap %s kbit/s per stream, quality lowered on %d of %d streams: %s" % (
        cap or "off", len(lowered), len(channels), lowered or "none"))
    if stuck:
        print("HEADLESS DID NOT FINISH: killed after %d s" % (int(seconds) + 900))
    made = sorted({os.path.basename(os.path.dirname(os.path.dirname(p))) for p in found})
    missing = [c for c in channels if c.lower() not in made]
    print("channels with clips: %d of %d" % (len(made), len(channels)), "| without:", missing or "none")
    lossy = lost_video(home) + jumpy_clips(found)
    print("video lost on the way to disk or inside clips:", lossy or "none")
    # один канал мог как раз уйти на рекламу или закончить стрим
    good = bool(found) and len(missing) <= 1 and not lossy and not stuck and (not cap or bool(lowered))
    print("LIVE SMOKE:", "PASSED, clips: %d" % len(found) if good else "FAILED, clips: %d" % len(found))
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
