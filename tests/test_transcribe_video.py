"""Unit tests for transcribe_video.py pure helpers (no network / no GPU).

Covers: platform detection, bvid extraction, B站 subtitle parsing, the subtitle
coverage gate (must reject the observed 串台/mismatched subtitle), ModelScope URL
building, resume headers, filename sanitization, and cookie-file safety.
Integration paths (audio download, model download, transcription, subtitle HTTP)
are intentionally not unit-tested.
"""
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import transcribe_video as tv  # noqa: E402


# ---------------- platform / url ----------------
def test_detect_platform():
    assert tv.detect_platform("https://www.bilibili.com/video/BV1psus6vE9T") == "bilibili"
    assert tv.detect_platform("https://b23.tv/abcd") == "bilibili"
    assert tv.detect_platform("https://www.youtube.com/watch?v=abc") == "youtube"
    assert tv.detect_platform("https://youtu.be/abc") == "youtube"
    assert tv.detect_platform("https://v.douyin.com/abc/") == "other"
    assert tv.detect_platform("https://example.com/v") == "other"


def test_extract_bvid():
    assert tv.extract_bvid("https://www.bilibili.com/video/BV1psus6vE9T?spm_id=from=333") == "BV1psus6vE9T"
    assert tv.extract_bvid("BV1psus6vE9T") == "BV1psus6vE9T"
    assert tv.extract_bvid("https://b23.tv/xx") is None  # short link carries no BV
    assert tv.extract_bvid("no bvid here at all") is None


# ---------------- subtitle parsing + coverage gate ----------------
def test_parse_subtitle_json_basic():
    body = {"body": [
        {"from": 0.3, "to": 2.0, "content": "大家好"},
        {"from": 2.0, "to": 5.0, "content": "今天聊黄金"},
    ]}
    segs, span = tv.parse_subtitle_json(body)
    assert segs == [(0.3, 2.0, "大家好"), (2.0, 5.0, "今天聊黄金")]
    assert span == pytest.approx(5.0 - 0.3)


def test_parse_subtitle_json_strips_markup_and_empty():
    body = {"body": [{"from": 0, "to": 1, "content": "a<br>b\nc"}, {"from": 1, "to": 2, "content": "   "}]}
    segs, _ = tv.parse_subtitle_json(body)
    assert segs == [(0.0, 1.0, "abc")]  # whitespace-only segment dropped
    assert tv.parse_subtitle_json({"body": []}) == ([], 0.0)
    assert tv.parse_subtitle_json({}) == ([], 0.0)


def test_subtitle_coverage_rejects_mismatched_串台():
    # Observed real failure: B站 attached a wrong subtitle covering ~392s of a 986s video.
    assert tv.subtitle_coverage_ok(392.0, 986.0) is False
    # A genuine full subtitle passes.
    assert tv.subtitle_coverage_ok(980.0, 986.0) is True
    # Degenerate inputs never pass.
    assert tv.subtitle_coverage_ok(0.0, 986.0) is False
    assert tv.subtitle_coverage_ok(100.0, 0.0) is False
    # Threshold boundary.
    assert tv.subtitle_coverage_ok(80.0, 100.0) is True
    assert tv.subtitle_coverage_ok(79.0, 100.0) is False


# ---------------- model source ----------------
def test_modelscope_repo_for():
    assert tv.modelscope_repo_for("large-v3") == "pengzhendong/faster-whisper-large-v3"
    assert tv.modelscope_repo_for("medium").startswith("pengzhendong/")
    with pytest.raises(ValueError):
        tv.modelscope_repo_for("huge")


def test_modelscope_file_url():
    url = tv.modelscope_file_url("pengzhendong/faster-whisper-large-v3", "model.bin")
    assert "modelscope.cn" in url
    assert url.endswith("FilePath=model.bin")
    assert "pengzhendong/faster-whisper-large-v3" in url


def test_needed_model_files_filters_extras():
    files = [".gitattributes", "README.md", "configuration.json", "config.json",
             "model.bin", "tokenizer.json", "vocabulary.json", "preprocessor_config.json"]
    keep = tv.needed_model_files(files)
    for skip in (".gitattributes", "README.md", "configuration.json"):
        assert skip not in keep
    for need in ("config.json", "model.bin", "tokenizer.json"):
        assert need in keep


# ---------------- download resume + filename ----------------
def test_resume_range_header():
    assert tv.resume_range_header(0) == {}
    assert tv.resume_range_header(1000) == {"Range": "bytes=1000-"}


def test_make_safe_title():
    assert "/" not in tv.make_safe_title("黄金下一阶段的走势 / note")
    t = tv.make_safe_title("abc?<>:*|")
    assert all(c not in t for c in "/?:*|<>")
    assert tv.make_safe_title("") == "transcript"
    assert tv.make_safe_title("   ") == "transcript"
    assert len(tv.make_safe_title("x" * 200, max_len=60)) <= 60


# ---------------- cookie safety ----------------
def test_write_cookie_file_contains_only_sessdata():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "bili_cookies.txt"
        tv.write_cookie_file(p, "MYSESS123")
        txt = p.read_text(encoding="utf-8")
        assert "SESSDATA\tMYSESS123" in txt
        assert ".bilibili.com" in txt
        # exactly one SESSDATA entry, nothing else secret
        assert txt.count("SESSDATA") == 1
        # caller is responsible for deletion; verify it is deletable
        p.unlink()
        assert not p.exists()
