"""Transcribe a video URL to text (Whisper) — standalone, general-purpose.

Input a video URL (B站 / YouTube / 抖音 / any yt-dlp-supported site) → output the spoken
text. Stops at "text out"; downstream analysis is a separate step.

Two paths, auto-chosen:
  1. B站 AI-字幕 fast path (only when a SESSDATA cookie is available): fetch the platform's
     AI subtitle in seconds, free, no Whisper. Auto-verified against video duration — if the
     subtitle is mismatched/串台 (B站 occasionally attaches the wrong subtitle; caught when
     covered-span < 80% of the video), it silently falls back to Whisper.
  2. Whisper (default): yt-dlp pulls audio → faster-whisper (large-v3) transcribes.

Hard-won env fixes baked in (verified on this Windows + conda + GTX 1660 Ti box):
  - Model comes from ModelScope (China CDN), NOT HuggingFace — HF/xet CDN 500s from China and
    hf-mirror redirects LFS binaries back to HF (0 bytes). Resumable, cached locally.
  - ctranslate2 sees CUDA even when cublas64_12.dll is missing; that silently degrades to a
    ~20-min crawl. We probe cublas, and if missing, hunt sibling conda envs' torch/lib and
    register them as DLL dirs; still missing → CPU int8 fallback.
  - KMP_DUPLICATE_LIB_OK=TRUE (conda mkl + duplicate libiomp would crash).
  - Greedy beam=1 (beam=5 is ~5x slower for negligible accuracy gain on summary-grade text).

Standalone by design — does not import the stockagent package, DB, or trading engine.

Usage:
  python scripts/transcribe_video.py --url "https://www.bilibili.com/video/BV1psus6vE9T"
  python scripts/transcribe_video.py --url <BV> --cookie "$BILI_SESSDATA"
  python scripts/transcribe_video.py --url <youtube> --model medium --device cpu
  python scripts/transcribe_video.py --url <BV> --no-subtitle        # force Whisper
"""
from __future__ import annotations

# Must precede any lib that pulls OpenMP / ctranslate2 (conda duplicate-libiomp crash).
import os
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import argparse
import ctypes
import glob
import json
import re
import shutil
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------
MODEL_DIR = ROOT / "data" / "cache" / "whisper_models"
DEFAULT_OUT_DIR = ROOT / "data" / "transcripts"
COOKIE_TIMEOUT_SESSDATA_URL = True  # only transient; never persisted by this script

# ModelScope repos (CT2 / faster-whisper format, real binaries served from China CDN).
# Verified: HF + hf-mirror do NOT serve the LFS binary from China (0 bytes / 500).
MODELSCOPE_REPOS = {
    "large-v3": "pengzhendong/faster-whisper-large-v3",
    "medium": "pengzhendong/faster-whisper-medium",
    "small": "pengzhendong/faster-whisper-small",
}
# core files faster-whisper needs to load from a local dir
MODEL_SKIP_FILES = {".gitattributes", "README.md", "configuration.json"}
MODEL_MIN_BIN_BYTES = 50_000_000  # treat smaller model.bin as broken/incomplete

BILI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com/",
}
INITIAL_PROMPT = (
    "以下是一段中文视频的语音内容，可能涉及财经、宏观经济、黄金、股市、利率、"
    "美元、美联储、就业与通胀等话题。"
)
SUBTITLE_COVERAGE_THRESHOLD = 0.8  # subtitle must cover >=80% of video duration else reject


# --------------------------------------------------------------------------
# pure helpers (unit-tested — see tests/test_transcribe_video.py)
# --------------------------------------------------------------------------
def detect_platform(url: str) -> str:
    """bilibili | youtube | other from a URL."""
    u = url.lower()
    if "bilibili.com" in u or "b23.tv" in u:
        return "bilibili"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    return "other"


def extract_bvid(url: str) -> str | None:
    """Pull a BV id from a B站 URL (e.g. .../video/BV1psus6vE9T)."""
    m = re.search(r"(BV[0-9A-Za-z]{10})", url)
    return m.group(1) if m else None


def parse_subtitle_json(body: dict) -> tuple[list[tuple[float, float, str]], float]:
    """Parse a B站 subtitle JSON body into (segments, covered_span_seconds).

    segments: list of (start, end, text); covered_span: max(end) - min(start).
    """
    items = body.get("body") or []
    segs: list[tuple[float, float, str]] = []
    for s in items:
        txt = (s.get("content") or "").replace("\n", "").replace("<br>", "").strip()
        if txt:
            segs.append((float(s.get("from", 0)), float(s.get("to", 0)), txt))
    if not segs:
        return [], 0.0
    starts = [a for a, _, _ in segs]
    ends = [b for _, b, _ in segs]
    return segs, max(ends) - min(starts)


def subtitle_coverage_ok(covered_span: float, video_duration: float,
                         threshold: float = SUBTITLE_COVERAGE_THRESHOLD) -> bool:
    """True if the subtitle covers >= threshold of the video duration.

    Guards against B站 attaching a mismatched/串台 subtitle (one observed case covered only
    6.5 min of a 16.5 min video → 0.40 → rejected → Whisper fallback).
    """
    if video_duration <= 0:
        return False
    return (covered_span / video_duration) >= threshold


def modelscope_repo_for(model: str) -> str:
    if model not in MODELSCOPE_REPOS:
        raise ValueError(f"unknown model '{model}'; choose one of {list(MODELSCOPE_REPOS)}")
    return MODELSCOPE_REPOS[model]


def modelscope_file_url(repo: str, filename: str) -> str:
    return f"https://modelscope.cn/api/v1/models/{repo}/repo?Revision=master&FilePath={filename}"


def needed_model_files(all_files: list[str]) -> list[str]:
    """From a repo file list, keep the files faster-whisper needs (drop .gitattributes etc)."""
    return [f for f in all_files if f and f not in MODEL_SKIP_FILES]


def resume_range_header(have_bytes: int) -> dict:
    """Range header for resumable download; empty dict when starting fresh."""
    return {"Range": f"bytes={have_bytes}-"} if have_bytes > 0 else {}


def make_safe_title(title: str, max_len: int = 60) -> str:
    """Sanitize a video title into a safe filename (keep CJK/alnum/_-)."""
    t = re.sub(r"[^\w一-鿿]+", "_", title or "").strip("_")
    if not t:
        t = "transcript"
    return t[:max_len]


def write_cookie_file(path: Path, sessdata: str) -> None:
    """Write a minimal Netscape cookie file containing only SESSDATA."""
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        ".bilibili.com\tTRUE\t/\tFALSE\t1900000000\tSESSDATA\t" + sessdata + "\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------
# integration helpers (network / GPU — not unit-tested)
# --------------------------------------------------------------------------
def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def check_deps() -> bool:
    """Verify transcription deps; print a targeted install hint if missing."""
    missing = []
    for mod in ("yt_dlp", "faster_whisper", "imageio_ffmpeg"):
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    if missing:
        print(f"缺少依赖: {missing}")
        print("请先安装:  pip install yt-dlp faster-whisper imageio-ffmpeg")
        return False
    return True


def _load_dotenv_optional() -> None:
    """Load .env if present so BILI_SESSDATA set there is picked up (does not override)."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _bili_get(url: str, sessdata: str | None, timeout: int = 25) -> dict:
    headers = dict(BILI_HEADERS)
    if sessdata:
        headers["Cookie"] = "SESSDATA=" + sessdata
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def bili_fetch_subtitle(url: str, sessdata: str) -> tuple[list[tuple[float, float, str]], float, float, str] | None:
    """B站 AI-字幕 fast path. Returns (segments, covered_span, video_duration, title) or None."""
    bvid = extract_bvid(url)
    if not bvid:
        return None
    try:
        view = _bili_get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", sessdata)
        data = view.get("data") or {}
        cid, duration, title = data.get("cid"), data.get("duration"), data.get("title")
        if not cid:
            return None
        player = _bili_get(f"https://api.bilibili.com/x/player/v2?bvid={bvid}&cid={cid}", sessdata)
        subs = ((player.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
        ai = next((s for s in subs if s.get("lan", "").startswith("ai") or s.get("lan") == "zh-CN"), None)
        if not ai or not ai.get("subtitle_url"):
            return None
        sub_url = ai["subtitle_url"]
        if sub_url.startswith("//"):
            sub_url = "https:" + sub_url
        sub_json = _bili_get(sub_url, sessdata, timeout=30)
        segs, covered = parse_subtitle_json(sub_json)
        if not segs:
            return None
        return segs, covered, float(duration or 0), title or bvid
    except Exception as e:
        print(f"  [字幕] B站字幕快路径失败 ({e!r})，回退 Whisper", flush=True)
        return None


def download_audio(url: str, workdir: Path, ffmpeg_exe: str,
                   cookie_file: str | None) -> tuple[Path, str, float]:
    """yt-dlp → 16kHz mono wav. Returns (wav_path, title, duration_seconds)."""
    from yt_dlp import YoutubeDL

    opts: dict = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "ffmpeg_location": ffmpeg_exe,
        "outtmpl": str(workdir / "audio.%(ext)s"),
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
        "postprocessor_args": ["-ar", "16000", "-ac", "1"],
        "quiet": True, "no_warnings": True,
    }
    if cookie_file:
        opts["cookiefile"] = cookie_file
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    wav = workdir / "audio.wav"
    if not wav.exists():  # fallback: locate whatever wav got produced
        cand = sorted(workdir.glob("audio*.wav"))
        if not cand:
            raise RuntimeError("yt-dlp did not produce an audio wav")
        wav = cand[0]
    title = (info or {}).get("title") or "transcript"
    duration = float((info or {}).get("duration") or 0.0)
    return wav, title, duration


def ensure_model(model: str) -> Path:
    """Ensure the CT2 model is present locally, downloading from ModelScope if needed."""
    dest = MODEL_DIR / model
    dest.mkdir(parents=True, exist_ok=True)
    bin_path = dest / "model.bin"
    if bin_path.exists() and bin_path.stat().st_size >= MODEL_MIN_BIN_BYTES:
        return dest
    repo = modelscope_repo_for(model)
    print(f"  [模型] 从 ModelScope 下载 {repo}（首次 ~3GB，断点续传）...", flush=True)
    api = f"https://modelscope.cn/api/v1/models/{repo}/repo/files?Revision=master"
    files = [
        f["Path"] for f in
        (_bili_get(api, None, timeout=30).get("Data") or {}).get("Files", [])
    ]
    files = needed_model_files(files)
    for fn in files:
        _download_with_resume(modelscope_file_url(repo, fn), dest / fn, label=fn)
    return dest


def _download_with_resume(url: str, path: Path, label: str, retries: int = 6) -> None:
    last = 0.0
    for attempt in range(retries):
        have = path.stat().st_size if path.exists() else 0
        headers = {"User-Agent": "Mozilla/5.0"}
        headers.update(resume_range_header(have))
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as r:
                mode = "ab" if (have and r.status == 206) else "wb"
                if mode == "wb":
                    have = 0
                with open(path, mode) as f:
                    while True:
                        chunk = r.read(1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        have += len(chunk)
                        if time.time() - last > 4:
                            print(f"    {label}: {have / 1e6:.0f} MB", flush=True)
                            last = time.time()
            print(f"    {label}: DONE {path.stat().st_size / 1e6:.0f} MB", flush=True)
            return
        except Exception as e:
            print(f"    {label}: 重试 {attempt + 1}/{retries} ({e!r})", flush=True)
            time.sleep(3)
    raise RuntimeError(f"download failed after {retries} retries: {label}")


def _find_cublas_dirs() -> list[Path]:
    """Hunt sibling conda envs / pkgs for cublas64_*.dll dirs (Windows DLL-dir registration)."""
    hits: list[Path] = []
    prefix = os.environ.get("CONDA_PREFIX")
    envs_dir = Path(prefix).parent if prefix else None
    pkgs_dir = Path(prefix).parent.parent / "pkgs" if prefix else None
    for root, glib in ((envs_dir, "*/Lib/site-packages/torch/lib/cublas64_*.dll"),
                       (pkgs_dir, "*/Library/bin/cublas64_*.dll")):
        if not root or not root.exists():
            continue
        for p in root.glob(glib):
            if p.parent not in hits:
                hits.append(p.parent)
    return hits


def _cublas_loadable() -> bool:
    try:
        ctypes.WinDLL("cublas64_12.dll")  # type: ignore[attr-defined]
        return True
    except OSError:
        pass
    try:
        ctypes.WinDLL("cublas64_11.dll")  # type: ignore[attr-defined]
        return True
    except OSError:
        return False


def resolve_device(prefer: str) -> tuple[str, str, str]:
    """Decide (device, compute_type, note). Probes cublas; patches DLL dirs; falls back to CPU."""
    if prefer == "cpu":
        return "cpu", "int8", "强制 CPU (int8)"
    try:
        import ctranslate2
        n_cuda = ctranslate2.get_cuda_device_count()
    except Exception:
        n_cuda = 0
    if n_cuda < 1:
        return "cpu", "int8", "无 CUDA → CPU (int8)"
    if _cublas_loadable():
        return "cuda", "float16", f"GPU fp16 (cuda devices={n_cuda})"
    # cublas missing — register sibling-env torch/lib as DLL dirs, then re-probe
    patched = []
    for d in _find_cublas_dirs():
        try:
            os.add_dll_directory(str(d))  # type: ignore[attr-defined]
        except Exception:
            pass
        os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
        patched.append(str(d))
    if patched and _cublas_loadable():
        return "cuda", "float16", f"GPU fp16 (cublas via {patched[0]})"
    return "cpu", "int8", "GPU 缺 cublas → CPU (int8) 兜底"


def transcribe(wav: Path, model_dir: Path, device: str, compute_type: str,
               prompt: str, segs_out: Path) -> tuple[str, int, int]:
    """Run faster-whisper; write timestamped segments live; return (full_text, n_segs, n_chars)."""
    from faster_whisper import WhisperModel
    print(f"  [转写] load {device}/{compute_type} ...", flush=True)
    t0 = time.time()
    model = WhisperModel(str(model_dir), device=device, compute_type=compute_type)
    print(f"  [转写] loaded {time.time() - t0:.0f}s, 转写中 (greedy beam=1)...", flush=True)
    segments, _info = model.transcribe(
        str(wav), language="zh", beam_size=1, vad_filter=True,
        condition_on_previous_text=False, initial_prompt=prompt,
    )
    segs: list[str] = []
    n = 0
    last_min = -1
    full_len = 0
    with open(segs_out, "w", encoding="utf-8") as sf:
        t1 = time.time()
        for s in segments:
            txt = (s.text or "").strip()
            if not txt:
                continue
            n += 1
            full_len += len(txt)
            segs.append(txt)
            sf.write(f"[{s.start:.0f}-{s.end:.0f}] {txt}\n")
            sf.flush()
            m = int(s.end // 60)
            if m != last_min:
                last_min = m
                print(f"    ...{s.end:.0f}s ({m}min) segs={n} chars={full_len}", flush=True)
    print(f"  [转写] done: {n} segs / {full_len} chars in {time.time() - t1:.0f}s", flush=True)
    return "".join(segs), n, full_len


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Transcribe a video URL to text (Whisper). General-purpose.")
    ap.add_argument("--url", required=True, help="video URL (B站 / YouTube / 抖音 / any yt-dlp site)")
    ap.add_argument("--cookie", default=None, help="B站 SESSDATA (for 充电/preview videos & subtitle path)")
    ap.add_argument("--cookie-file", default=None, help="path to a Netscape cookie file (alt to --cookie)")
    ap.add_argument("--model", default="large-v3", choices=tuple(MODELSCOPE_REPOS),
                    help="faster-whisper model (default large-v3; first run downloads ~3GB)")
    ap.add_argument("--device", default="auto", choices=("auto", "cuda", "cpu"))
    ap.add_argument("--out", default=None, help="output .txt path (default data/transcripts/<title>.txt)")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="output directory when --out not set")
    ap.add_argument("--no-subtitle", action="store_true", help="skip B站 AI-字幕 fast path, force Whisper")
    ap.add_argument("--prompt", default=INITIAL_PROMPT, help="initial_prompt hint for domain terms")
    ap.add_argument("--keep-audio", action="store_true", help="keep the downloaded audio wav")
    args = ap.parse_args()

    _utf8_stdout()
    if not check_deps():
        return 1
    _load_dotenv_optional()

    # cookie: explicit flag > env (.env / BILI_SESSDATA)
    sessdata = args.cookie or os.environ.get("BILI_SESSDATA") or None
    platform = detect_platform(args.url)
    print(f"平台: {platform}  模型: {args.model}  设备偏好: {args.device}", flush=True)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- 1. B站 AI-字幕 fast path ----
    if platform == "bilibili" and sessdata and not args.no_subtitle:
        print("\n[1/2] 尝试 B站 AI-字幕快路径 ...", flush=True)
        got = bili_fetch_subtitle(args.url, sessdata)
        if got:
            segs, covered, dur, title = got
            ok = subtitle_coverage_ok(covered, dur)
            print(f"  字幕覆盖 {covered:.0f}s / 视频 {dur:.0f}s → {'采用' if ok else '串台/不全 → 回退 Whisper'}", flush=True)
            if ok:
                out_path = Path(args.out) if args.out else out_dir / f"{make_safe_title(title)}.txt"
                full = "".join(t for _, _, t in segs)
                out_path.write_text(full, encoding="utf-8")
                segs_path = out_path.with_suffix("").with_name(out_path.stem + "_segs.txt")
                segs_path.write_text(
                    "\n".join(f"[{a:.0f}-{b:.0f}] {t}" for a, b, t in segs) + "\n", encoding="utf-8")
                _report(out_path, full, len(segs), method="B站AI字幕", device="—", title=title)
                return 0
    else:
        why = "非B站" if platform != "bilibili" else ("无cookie" if not sessdata else "被--no-subtitle禁用")
        print(f"\n[1/2] 跳过字幕快路径（{why}）→ 直接 Whisper", flush=True)

    # ---- 2. Whisper path ----
    print("[2/2] Whisper 转写 ...", flush=True)
    import tempfile
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
        ffmpeg_exe = get_ffmpeg_exe()
    except Exception:
        print("缺少 imageio-ffmpeg（ffmpeg 二进制）；pip install imageio-ffmpeg")
        return 1

    workdir = Path(tempfile.mkdtemp(prefix="transcribe_"))
    cookie_file = None
    try:
        if args.cookie_file:
            cookie_file = args.cookie_file
        elif sessdata and platform == "bilibili":
            cookie_file = str(workdir / "bili_cookies.txt")
            write_cookie_file(Path(cookie_file), sessdata)

        print("  [音频] yt-dlp 拉音频 → 16kHz mono wav ...", flush=True)
        wav, title, duration = download_audio(args.url, workdir, ffmpeg_exe, cookie_file)
        print(f"  [音频] {wav.name} 时长 {duration:.0f}s — {title}", flush=True)

        model_dir = ensure_model(args.model)
        device, compute_type, note = resolve_device(args.device)
        print(f"  [设备] {note}", flush=True)

        out_path = Path(args.out) if args.out else out_dir / f"{make_safe_title(title)}.txt"
        segs_path = out_path.with_name(out_path.stem + "_segs.txt")
        full, n_segs, n_chars = transcribe(wav, model_dir, device, compute_type, args.prompt, segs_path)
        out_path.write_text(full, encoding="utf-8")

        _report(out_path, full, n_segs, method=f"Whisper({args.model})", device=note, title=title)
        return 0
    finally:
        if args.keep_audio:
            print(f"  [音频] 保留在 {workdir}", flush=True)
        else:
            shutil.rmtree(workdir, ignore_errors=True)


def _report(out_path: Path, full: str, n_segs: int, *, method: str, device: str, title: str) -> None:
    print("\n" + "=" * 60, flush=True)
    print(f"✅ 转写完成: {title}", flush=True)
    print(f"   方法: {method}   设备: {device}", flush=True)
    print(f"   段数: {n_segs}   字数: {len(full)}", flush=True)
    print(f"   全文: {out_path}", flush=True)
    print(f"   时间戳: {out_path.with_name(out_path.stem + '_segs.txt')}", flush=True)
    print("=" * 60, flush=True)


if __name__ == "__main__":
    sys.exit(main())
