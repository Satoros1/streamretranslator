"""Проверка на настоящем Twitch (запускается в GitHub Actions на Windows, уже собранной программой).

Находит русскоязычные стримы, которые идут прямо сейчас, пишет их несколько минут, жмёт «Клип сейчас»
и проверяет, что клип действительно появился. Twitch может не пускать серверы GitHub, поэтому
проверка не блокирует сборку: её результат читается в логах.
"""
import glob
import json
import os
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
    r = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=int(seconds) + 900)
    print(r.stdout.decode("utf-8", "replace")[-12000:])
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
    made = sorted({os.path.basename(os.path.dirname(os.path.dirname(p))) for p in found})
    missing = [c for c in channels if c.lower() not in made]
    print("channels with clips: %d of %d" % (len(made), len(channels)), "| without:", missing or "none")
    good = bool(found) and len(missing) <= 1               # один канал мог как раз уйти на рекламу или закончить стрим
    print("LIVE SMOKE:", "PASSED, clips: %d" % len(found) if good else "FAILED, clips: %d" % len(found))
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
