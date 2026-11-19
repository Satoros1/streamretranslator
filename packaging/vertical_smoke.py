"""Проверка вертикального клипа с заголовком и чатом уже собранной программой (GitHub Actions, Windows).

Делает короткое тестовое видео, просит программу собрать из него клип 1080x1920 с заголовком над видео и чатом
и проверяет размер кадра. Кадр из результата кладётся в папку shots, чтобы на него можно было посмотреть.
"""
import json
import os
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    exe = sys.argv[1].split()
    home = tempfile.mkdtemp(prefix="sc_vert_")
    raw = os.path.join(home, "raw.ts")
    out = os.path.join(home, "clip_9x16.mp4")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=60",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "10", "-c:v", "libx264", "-preset", "ultrafast",
                    "-g", "120", "-c:a", "aac", "-f", "mpegts", raw], check=True)
    names = ["Vasya_Pro", "котик_228", "DedInside", "Лёха", "mamkin_gamer", "Ириска"]
    texts = ["Kappa привет", "АХАХАХАХ", "что он несёт KEKW", "ХАХАХА 😂😂😂", "в нарезку", "ору",
             "это лучший момент за весь стрим, пересматриваю третий раз подряд и не могу остановиться"]
    chat = [{"t": -1 + i * 0.45, "name": names[i % len(names)], "color": ["#FF4500", "", "#1E90FF"][i % 3],
             "text": texts[i % len(texts)], "emotes": [[0, 4, "25"]] if texts[i % len(texts)].startswith("Kappa") else []}
            for i in range(22)]
    # room_id: настоящий большой канал, чтобы заодно проверить загрузку смайлов 7TV/BTTV/FFZ
    spec = {"raw": raw, "start": 1.0, "dur": 8.0, "out": out, "layout": "blur", "style": "soft", "size": "m",
            "room_id": "71092938", "chat": chat, "headline": "угон инвалидной коляски в гта 6"}
    path = os.path.join(home, "spec.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False)
    env = dict(os.environ, STREAMCLIPPER_HOME=home)
    r = subprocess.run(exe + ["vrender", path], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
    said = r.stdout.decode("utf-8", "replace")
    log = os.path.join(home, "logs", "editor.log")
    if os.path.exists(log):
        with open(log, encoding="utf-8", errors="replace") as f:
            said += "\n" + f.read()
    print(said[-4000:])
    if "заголовок над видео не нарисовался" in said:
        print("VERTICAL SMOKE: FAILED, the headline was not drawn")
        return 1
    if r.returncode != 0 or not os.path.exists(out):
        print("VERTICAL SMOKE: FAILED, exit code", r.returncode)
        return 1
    info = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height,r_frame_rate:format=duration",
                           "-of", "json", out], stdout=subprocess.PIPE, check=True).stdout.decode()
    print(info)
    streams = json.loads(info)["streams"]
    video = next(s for s in streams if s.get("width"))
    ok = (video["width"], video["height"], video["codec_name"]) == (1080, 1920, "h264") and any(s["codec_name"] == "aac" for s in streams)
    os.makedirs("shots", exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "6", "-i", out, "-frames:v", "1",
                    os.path.join("shots", "vertical.png")])
    print("VERTICAL SMOKE:", "PASSED" if ok else "FAILED, wrong format")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
