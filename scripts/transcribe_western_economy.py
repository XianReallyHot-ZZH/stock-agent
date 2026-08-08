"""Batch-transcribe the 西方经济 anthology (96 eps) → docs/Billibili-JZ/western_economy/.

Reuses the video-transcribe skill (transcribe_video.py) helpers. Two phases:
  1) subtitle: B站 AI-字幕 fast path (needs BILI_SESSDATA) — seconds/video where valid.
  2) whisper:  faster-whisper fallback for the rest (model loaded ONCE, reused).
Resumable (skips videos whose output .txt exists). Throttled to dodge 风控.
Output naming: <YYYY-MM-DD>_<safe-title>.txt (+ _segs.txt), sorted by publish date.

Usage:
  BILI_SESSDATA=<sessdata> PYTHONIOENCODING=utf-8 python scripts/transcribe_western_economy.py
  BILI_SESSDATA=<sessdata> python scripts/transcribe_western_economy.py --phase subtitle  # fast path only
  BILI_SESSDATA=<sessdata> python scripts/transcribe_western_economy.py --phase whisper   # fallback only
"""
from __future__ import annotations

import os
import sys
import json
import time
import shutil
import tempfile
import datetime
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import transcribe_video as tv  # reuse the skill's helpers

SOURCE_BV = "BV1psus6vE9T"
OUT_DIR = ROOT / "docs" / "Billibili-JZ" / "western_economy"
EPISODES_JSON = ROOT / "data" / "western_economy_episodes.json"
SLEEP = 2.0  # throttle between videos to avoid 风控


def fetch_episodes() -> list[dict]:
    if EPISODES_JSON.exists():
        return json.loads(EPISODES_JSON.read_text(encoding="utf-8"))
    H = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"}
    d = json.load(urllib.request.urlopen(urllib.request.Request(
        f"https://api.bilibili.com/x/web-interface/view/detail?bvid={SOURCE_BV}", headers=H), timeout=25))
    s = (d.get("data") or {}).get("View", {}).get("ugc_season", {})
    eps = []
    for sec in s.get("sections", []):
        for ep in sec.get("episodes", []):
            arc = ep.get("arc") or {}
            eps.append({"bvid": ep.get("bvid"), "title": arc.get("title"),
                        "pubdate": arc.get("pubdate"), "duration": arc.get("duration")})
    eps.sort(key=lambda e: e.get("pubdate") or 0)
    EPISODES_JSON.parent.mkdir(parents=True, exist_ok=True)
    EPISODES_JSON.write_text(json.dumps(eps, ensure_ascii=False, indent=2), encoding="utf-8")
    return eps


def out_path_for(ep: dict) -> Path:
    dt = datetime.datetime.fromtimestamp(ep.get("pubdate") or 0).strftime("%Y-%m-%d")
    return OUT_DIR / f"{dt}_{tv.make_safe_title(ep.get('title') or 'untitled')}.txt"


def phase_subtitle(eps, sessdata):
    done = need = fail = 0
    for i, e in enumerate(eps, 1):
        out = out_path_for(e)
        if out.exists():
            print(f"[{i}/{len(eps)}] skip(done) {e['bvid']}", flush=True)
            continue
        if not sessdata:
            need += 1
            time.sleep(SLEEP)
            continue
        url = f"https://www.bilibili.com/video/{e['bvid']}"
        try:
            got = tv.bili_fetch_subtitle(url, sessdata)
            if got:
                segs, covered, dur, _title = got
                if tv.subtitle_coverage_ok(covered, dur):
                    full = "".join(t for _, _, t in segs)
                    out.write_text(full, encoding="utf-8")
                    segp = out.with_name(out.stem + "_segs.txt")
                    segp.write_text("\n".join(f"[{a:.0f}-{b:.0f}] {t}" for a, b, t in segs) + "\n", encoding="utf-8")
                    print(f"[{i}/{len(eps)}] ✅subtitle {e['bvid']} {e['title'][:24]} ({len(full)}字)", flush=True)
                    done += 1
                else:
                    print(f"[{i}/{len(eps)}] 字幕串台/覆盖不足({covered:.0f}/{dur:.0f}s)→whisper {e['bvid']}", flush=True)
                    need += 1
            else:
                print(f"[{i}/{len(eps)}] 无字幕→whisper {e['bvid']} {e['title'][:20]}", flush=True)
                need += 1
        except Exception as ex:
            print(f"[{i}/{len(eps)}] 字幕异常 {e['bvid']} {ex!r}→whisper", flush=True)
            need += 1
            fail += 1
        time.sleep(SLEEP)
    print(f"\n=== phase subtitle: done={done}  need-whisper={need}  err={fail} ===", flush=True)


def _whisper(model, wav: Path, segs_out: Path):
    segs, _ = model.transcribe(str(wav), language="zh", beam_size=1, vad_filter=True,
                               condition_on_previous_text=False, initial_prompt=tv.INITIAL_PROMPT)
    parts, n, nc = [], 0, 0
    with open(segs_out, "w", encoding="utf-8") as sf:
        for s in segs:
            t = (s.text or "").strip()
            if not t:
                continue
            n += 1
            nc += len(t)
            parts.append(t)
            sf.write(f"[{s.start:.0f}-{s.end:.0f}] {t}\n")
    return "".join(parts), n, nc


def phase_whisper(eps, sessdata, model_name):
    device, compute, note = tv.resolve_device("auto")
    print(f"[whisper] device: {note}", flush=True)
    model_dir = tv.ensure_model(model_name)
    from faster_whisper import WhisperModel
    t0 = time.time()
    model = WhisperModel(str(model_dir), device=device, compute_type=compute)
    print(f"[whisper] model loaded {time.time() - t0:.0f}s", flush=True)
    from imageio_ffmpeg import get_ffmpeg_exe
    ffmpeg = get_ffmpeg_exe()
    cookie_file = None
    if sessdata:
        cookie_file = os.path.join(tempfile.gettempdir(), "bili_we_cookies.txt")
        tv.write_cookie_file(Path(cookie_file), sessdata)
    done = fail = 0
    for i, e in enumerate(eps, 1):
        out = out_path_for(e)
        if out.exists():
            print(f"[{i}/{len(eps)}] skip(done) {e['bvid']}", flush=True)
            continue
        url = f"https://www.bilibili.com/video/{e['bvid']}"
        workdir = Path(tempfile.mkdtemp(prefix=f"we_{e['bvid']}_"))
        try:
            wav, title, dur = tv.download_audio(url, workdir, ffmpeg, cookie_file)
            segp = out.with_name(out.stem + "_segs.txt")
            full, _n, nc = _whisper(model, wav, segp)
            out.write_text(full, encoding="utf-8")
            print(f"[{i}/{len(eps)}] ✅whisper {e['bvid']} {e['title'][:24]} ({nc}字,{dur:.0f}s)", flush=True)
            done += 1
        except Exception as ex:
            print(f"[{i}/{len(eps)}] ❌fail {e['bvid']} {ex!r}", flush=True)
            fail += 1
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        time.sleep(SLEEP)
    print(f"\n=== phase whisper: done={done}  fail={fail} ===", flush=True)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Batch-transcribe 西方经济 anthology.")
    ap.add_argument("--phase", choices=("both", "subtitle", "whisper"), default="both")
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--recent-first", action="store_true",
                    help="process most-recent first (descending pubdate); default oldest-first")
    args = ap.parse_args()

    tv._utf8_stdout()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sessdata = os.environ.get("BILI_SESSDATA") or None
    if not sessdata:
        print("⚠ 无 BILI_SESSDATA：字幕快路径跳过，充电视频音频可能只有10分钟预览", flush=True)
    eps = fetch_episodes()
    if args.recent_first:
        eps = list(reversed(eps))  # fetch_episodes returns ascending; flip to most-recent first
    print(f"共 {len(eps)} 集 → {OUT_DIR}  顺序: {'最近→最早' if args.recent_first else '最早→最近'}\n", flush=True)

    if args.phase in ("both", "subtitle"):
        phase_subtitle(eps, sessdata)
    if args.phase in ("both", "whisper"):
        phase_whisper(eps, sessdata, args.model)


if __name__ == "__main__":
    main()
