"""tushare pro 客户端薄包装(只读旁路数据腿 · 2026-09-13 引入,2000 积分档)。

定位: 替换被 push2/WAF 掐脖子的腿(行业成分/现货估值) + 补齐长期缺数据源
(扣非批量/资产负债表明细/股票曾用名)。永不喂引擎。

纪律:
- 频率: 2000 积分档 200 次/分钟 → 全局节流 min_interval(默认 0.35s ≈ 170 次/分)
  + 触发限频时指数退避重试(不吞错,最多 retries 次);
- token 从 .env 的 TUSHARE_TOKEN 读(缺 token → TushareError,调用方降级);
- 积分是门槛不消耗——本客户端不做配额记账。
"""
from __future__ import annotations

import os
import threading
import time

try:  # tushare 是可选依赖(未装/未配 token 时各腿降级回 akshare)
    import tushare as _ts
except Exception:  # noqa: BLE001
    _ts = None


class TushareError(RuntimeError):
    pass


_CLIENT = None
_LOCK = threading.Lock()
_LAST_CALL = 0.0


def _get_client():
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        raise TushareError("TUSHARE_TOKEN 未配置(.env)")
    if _ts is None:
        raise TushareError("tushare SDK 未安装(pip install tushare)")
    _CLIENT = _ts.pro_api(token)
    return _CLIENT


def has_token() -> bool:
    return bool(os.environ.get("TUSHARE_TOKEN", "").strip())


def query(api: str, min_interval: float = 0.35, retries: int = 4, **params):
    """调 tushare pro 接口,返回 DataFrame。全局节流 + 限频退避重试。

    频率档位: 2000 积分 = 200 次/分钟。其他异常(网络/参数)也重试,耗尽后抛
    TushareError(调用方按腿降级,不炸整板)。"""
    global _LAST_CALL
    pro = _get_client()
    fn = getattr(pro, api, None)
    if fn is None:
        raise TushareError(f"未知接口 {api}")
    last_err = None
    for attempt in range(retries):
        with _LOCK:
            wait = _LAST_CALL + min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _LAST_CALL = time.monotonic()
        try:
            df = fn(**params)
            if df is None:
                raise TushareError(f"{api} 返回 None")
            return df
        except Exception as ex:  # noqa: BLE001 — 限频/网络都走退避
            msg = str(ex)
            last_err = msg
            # 限频类错误退避更久;参数错误快速失败不浪费重试
            if "每分钟" in msg or "频" in msg or "sorry" in msg.lower():
                time.sleep(20.0 * (attempt + 1))
            elif "积分" in msg or "权限" in msg:
                raise TushareError(f"{api} 权限不足: {msg[:120]}")
            else:
                time.sleep(2.0 * (attempt + 1))
    raise TushareError(f"{api} failed after {retries} retries: {str(last_err)[:150]}")


def query_paged(api: str, page_size: int = 100, offset_field: str = "offset",
                 max_pages: int = 200, **params):
    """分页拉全量(offset/limit 分页族——fina_indicator/balancesheet 等)。
    返回 concat 后的 DataFrame;首页为空 → 空 DataFrame。"""
    import pandas as pd
    frames = []
    for page in range(max_pages):
        df = query(api, limit=page_size, **{offset_field: page * page_size}, **params)
        if df is None or len(df) == 0:
            break
        frames.append(df)
        if len(df) < page_size:
            break
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    return out.drop_duplicates() if len(out.columns) else out
