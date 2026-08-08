---
name: video-transcribe
description: Transcribe a video URL to text (B站 / YouTube / 抖音 / any yt-dlp site) via faster-whisper large-v3. Use when the user wants to "转写视频 / 把这个视频转成文字 / 提取视频文字 / 视频转文字稿 / transcribe video" and gives a URL. B站 videos with a SESSDATA cookie try the free AI-字幕 fast path first (auto-falls-back to Whisper if the subtitle is mismatched/串台); otherwise yt-dlp pulls audio and Whisper transcribes. Standalone read-only tool — does not touch the trading engine, DB, or stockagent package. First run downloads the ~3GB model from ModelScope (China CDN); later runs reuse the cache.
---

# 视频转文字（通用 · yt-dlp + faster-whisper）

输入视频链接 → 输出语音文字稿。**只到"出文字"为止**，后续分析单独做。
**只读旁路**：不碰交易引擎、不写 DB、不依赖 stockagent 包。产物落 `data/transcripts/`（gitignored）。

脚本：`scripts/transcribe_video.py`（standalone，可直接 `python` 跑，不必经 skill）。

## 触发场景
- 用户给一个视频链接 + "转写 / 转成文字 / 提取文字稿 / transcribe / 听写"
- 想把财经/讲座/访谈视频变成可分析、可引用的文字
- 只需文字，分析后续另做（本 skill 不做对照/解读）

## 前置
- `pip install yt-dlp faster-whisper imageio-ffmpeg`（脚本启动会 `check_deps()` 并提示）
- 首次转写会下 faster-whisper `large-v3` 模型（~3GB，从 **ModelScope** 国内 CDN，断点续传），缓存到 `data/cache/whisper_models/large-v3/`，之后复用
- B站**充电/付费**视频或想走「字幕快路径」时需要 SESSDATA cookie（见下「Cookie 安全」）

## 标准流程

### 1. 识别链接 + 是否有 cookie
- 从用户消息里取 URL（B站/YouTube/抖音等均可）。
- 若是 B站 且用户提供了 SESSDATA（或 `.env` 里有 `BILI_SESSDATA`）→ 传 `--cookie`，脚本会先试 **B站 AI-字幕快路径**（秒级、免费、不走 Whisper）；字幕被判定为串台/不全时**自动回退 Whisper**。
- 非B站 或 无 cookie → 直接 Whisper。

### 2. 跑脚本
```bash
PYTHONIOENCODING=utf-8 python scripts/transcribe_video.py --url "<视频链接>"
# B站带 cookie（字幕快路径 + 充电视频全量音频）：
PYTHONIOENCODING=utf-8 python scripts/transcribe_video.py --url "<BV链接>" --cookie "<SESSDATA>"
# 强制 Whisper、跳过字幕：
PYTHONIOENCODING=utf-8 python scripts/transcribe_video.py --url "<链接>" --no-subtitle
# 轻量模型 / 强制 CPU：
PYTHONIOENCODING=utf-8 python scripts/transcribe_video.py --url "<链接>" --model medium --device cpu
```
产物：`data/transcripts/<标题>.txt`（纯文本）+ `<标题>_segs.txt`（带时间戳）。`--out` 可指定输出路径。

### 3. 汇报（给用户）
- 全文路径 + 时间戳路径
- 方法（B站AI字幕 / Whisper）、设备（GPU fp16 / CPU int8、是否走了 cublas 兜底）、段数/字数
- 若用了字幕快路径，说明"已验真覆盖≥80%"
- 提示常见**术语误识**（greedy 转写黑话易错，如"大非农"可能被识成"大飞龙/达菲农"），可顺手列几个供用户核对；不要把误识当结论

## Cookie 安全
- 用户若直接贴 SESSDATA：**只临时用**——传 `--cookie`，脚本不写 `.env`、不落盘（仅必要时写一个临时 cookie 文件、用完随工作目录删除）。**绝不回显、绝不提交、绝不写进记忆。**
- 默认**不**把 cookie 存进 `.env`；只有当用户明确要"以后都自动带上"时，才提示其在本地 `.env`（gitignored）填 `BILI_SESSDATA=`。
- SESSDATA 是登录凭证，等同临时借用用户的 B站 只读身份（仅用于抓字幕/拉音频，不做账号操作）。

## 已知坑（脚本已自动处理，知会即可）
- **B站 AI 字幕会"串台"**：偶尔挂错视频的字幕。脚本用「字幕覆盖时长 ≥80% 视频」验真，不合格自动回退 Whisper（本次黄金视频就是 6.5min/16.5min=40% 被拦下）。
- **HuggingFace 在国内拿不到模型**：HF/xet CDN 500、hf-mirror 对 LFS 跳回 HF 返回 0 字节 → 脚本固定走 **ModelScope**。
- **GPU 缺 cublas 会静默退化**：ctranslate2 见到 CUDA 但 `cublas64_12.dll` 缺失时，会退化成 ~20 分钟的爬行。脚本探测 cublas，缺失则自动注册同机 conda env 的 `torch/lib` 为 DLL 目录；仍不行 → CPU int8 兜底。
- **libiomp 重复**：conda 环境 mkl+torch 双份会崩 → 脚本已设 `KMP_DUPLICATE_LIB_OK=TRUE`。
- **充电视频只给 10 分钟预览**：未带 cookie 时 B站 只发预览音频；带 cookie 拿全量。
- **beam=5 太慢**：默认 greedy `beam=1`（5.7 分钟出稿 vs beam=5 的 20 分钟+）。

## 何时用这个
- 任何"视频→文字"需求都用它（本仓无 B站 专用 skill，此 skill 通用）。
- 要做"转写后对照研究/个股看板分析"：先用本 skill 出文字，再单独让我基于文字 + 看板数据做分析（两步分开，别混在这步里）。
