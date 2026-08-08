"""Claim scoring + auto-settlement (ADR-0001 · Q6).

For each claim whose horizon has passed AND whose asset has a scoreable series, compute a Settlement:
  actual_direction : from the series close at episode_date vs horizon
  hit              : actual_direction matches the claimed direction
  baseline         : prior-trend direction (asset return over the equal-length window BEFORE the claim)
  baseline_hit     : actual matches baseline
  edge             : hit AND NOT baseline_hit   ← the ONLY measure of skill (Q6)

Range claims → scored vs the band (stayed inside = hit; range is non-trend so baseline misses → edge).
Scenario alt-branches → skipped (only the primary branch counts). Rules → quarantined (never scored).
level / timing / event-horizon / manual-only assets (半导体/恒生/无 series) → left unsettled for human.

Pure functions; settle_claims(store, asof) walks all draft+confirmed claims and writes wm_settlements.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import pandas as pd

from .extract import ASSET_REGISTRY

log = logging.getLogger(__name__)

_DIR_THRESHOLD = 0.01  # |return| < 1% over the horizon → "flat"


def _parse_date(h: str) -> Optional[str]:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(h or ""))
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def _direction(start: float, end: float) -> str:
    if not start or abs(start) < 1e-9:
        return "flat"
    r = (end - start) / abs(start)   # abs 分母 → 负值系列(如倒挂 2s10s 利差)方向也正确
    if r > _DIR_THRESHOLD:
        return "up"
    if r < -_DIR_THRESHOLD:
        return "down"
    return "flat"


def _value_at_or_after(s: pd.Series, date: str) -> Optional[float]:
    if s is None or len(s) == 0:
        return None
    after = s[s.index >= date]
    return float(after.iloc[0]) if len(after) else float(s.iloc[0])


def _value_at_or_before(s: pd.Series, date: str) -> Optional[float]:
    if s is None or len(s) == 0:
        return None
    before = s[s.index <= date]
    return float(before.iloc[-1]) if len(before) else float(s.iloc[-1])


def _prior_trend(s: pd.Series, ep: str, hdate: str) -> Optional[str]:
    """Direction over the equal-length window ENDING at the episode date (the momentum baseline).
    None if the window is too short. Baseline = "asset continues its prior trend"."""
    try:
        ep_ts, h_ts = pd.Timestamp(ep), pd.Timestamp(hdate)
    except Exception:  # noqa: BLE001
        return None
    dur = h_ts - ep_ts
    if dur.days <= 0:
        return None
    start_ts = (ep_ts - dur).strftime("%Y-%m-%d")
    window = s[(s.index >= start_ts) & (s.index <= ep)]
    if len(window) < 2:
        return None
    return _direction(float(window.iloc[0]), float(window.iloc[-1]))


def series_for(asset: str, store) -> Optional[pd.Series]:
    """close series (index=date str, values=float) for an asset, or None if manual-only/no data."""
    rec = ASSET_REGISTRY.get(asset)
    if not rec:
        return None
    _node, series = rec
    if not series:
        return None
    source, symbol = series
    if source == "commodity":
        s = store.get_commodity_series(symbol)  # pd.Series(close, index=date)
        return s.astype(float) if s is not None and len(s) else None
    if source == "index_daily":
        df = store.get_index_daily_series(symbol)
    else:
        df = store.get_western_series(source, symbol)
    if df is None or len(df) == 0:
        return None
    return df["close"].astype(float)


def settle_claim(claim: dict, store, asof: str) -> Optional[dict]:
    """Settle one claim, or None if not auto-settleable (future horizon / no series / alt-branch / level)."""
    hdate = _parse_date(claim.get("horizon"))
    ep = claim.get("episode_date")
    if hdate is None or hdate > asof or (ep and hdate < ep):
        # future (not yet), non-date (timing/event → manual), or inverted (horizon before the
        # claim was made — an extraction/dating error; the human-confirm gate fixes the source).
        return None
    ep = claim.get("episode_date")
    s = series_for(claim.get("asset"), store)
    if s is None:
        return None
    start = _value_at_or_after(s, ep)
    end = _value_at_or_before(s, hdate)
    if start is None or end is None or abs(start) < 1e-9:
        return None
    actual = _direction(start, end)
    note = f"{start:.2f}->{end:.2f}"
    ctype = claim.get("claim_type")

    if ctype == "range":
        lo, hi = claim.get("range_low"), claim.get("range_high")
        window = s[(s.index >= ep) & (s.index <= hdate)]
        if len(window) == 0 or lo is None or hi is None:
            return None
        hit = bool(float(window.min()) >= lo and float(window.max()) <= hi)
        baseline_hit = False  # range is non-trend; a trend baseline misses by construction
    elif ctype in ("direction", "scenario"):
        if ctype == "scenario" and claim.get("is_primary") == 0:
            return None  # only the primary branch counts toward edge
        claimed = claim.get("direction")
        if not claimed:
            return None
        # 路径感知:捕捉"中途回调/反弹"(端点法会漏掉"跌完又涨回")。窗口内显著波动≥3%即视为该方向发生
        window = s[(s.index >= ep) & (s.index <= hdate)]
        if len(window) < 2:
            return None
        mfe_up = (float(window.max()) - start) / abs(start)
        mfe_down = (start - float(window.min())) / abs(start)
        if mfe_down >= 0.03 and mfe_down >= mfe_up:
            actual = "down"
        elif mfe_up >= 0.03:
            actual = "up"
        else:
            actual = _direction(start, end)  # 无显著波动 → 看端点
        note += f" (中途波动{max(mfe_up, mfe_down) * 100:.0f}%)"
        hit = (actual == claimed)
        bdir = _prior_trend(s, ep, hdate)
        baseline_hit = (actual == bdir) if bdir else (actual == "up")
    elif ctype == "level":
        L = claim.get("level_value")
        if L is None:
            return None
        window = s[(s.index >= ep) & (s.index <= hdate)]
        if len(window) == 0:
            return None
        d = claim.get("direction")
        wmax, wmin = float(window.max()), float(window.min())
        if d == "down":
            hit = (wmin <= L) and (actual == "down")   # 跌到L 且 整体确实跌(防"摸到就涨回"假命中)
        elif d == "up":
            hit = (wmax >= L) and (actual == "up")     # 涨到L 且 整体确实涨
        else:
            hit = wmin >= L                            # 守住/不破 L(支撑),不限方向
        baseline_hit = False              # 精确点位=非随势,命中即 edge
    else:  # timing / event-horizon → manual
        return None

    return {
        "claim_uid": claim["uid"],
        "actual_direction": actual,
        "actual_value": end,
        "hit": 1 if hit else 0,
        "baseline_hit": 1 if baseline_hit else 0,
        "edge": 1 if (hit and not baseline_hit) else 0,
        "method": "auto",
        "settled_at": asof,
        "note": note,
    }


def settle_claims(store, asof: str, states=("draft", "confirmed")) -> dict:
    """Walk all draft+confirmed claims; auto-settle every settleable one into wm_settlements."""
    settled = 0
    for c in store.get_wm_claims():
        if c.get("state") not in states:
            continue
        res = settle_claim(c, store, asof)
        if res is None:
            continue
        store.upsert_wm_settlement(res)
        settled += 1
    log.info("auto-settled %d claims (asof %s)", settled, asof)
    return {"settled": settled}
