"""Portfolio state persistence: target holdings, actual (user-reported) holdings,
sent-report log (for idempotent push), adherence tracking.

Lives in the same SQLite file as prices. Used by both the scheduler (M3) and the
adherence loop (M4).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS target_holdings (
    as_of TEXT, symbol TEXT, weight REAL, entry_date TEXT,
    PRIMARY KEY (as_of, symbol)
);
CREATE TABLE IF NOT EXISTS actual_holdings (
    as_of TEXT, symbol TEXT, weight REAL,
    PRIMARY KEY (as_of, symbol)
);
CREATE TABLE IF NOT EXISTS sent_reports (
    as_of TEXT, channel TEXT, ok INTEGER, ts TEXT,
    PRIMARY KEY (as_of, channel)
);
"""


class State:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self):
        c = sqlite3.connect(str(self.db_path))
        c.execute("PRAGMA journal_mode=WAL;")
        return c

    # ---- target holdings (what the system says to hold) ----
    def latest_target_date(self) -> Optional[str]:
        with self._conn() as c:
            row = c.execute("SELECT MAX(as_of) FROM target_holdings").fetchone()
            return row[0] if row and row[0] else None

    def get_target_holdings(self) -> dict:
        d = self.latest_target_date()
        if not d:
            return {}
        with self._conn() as c:
            rows = c.execute(
                "SELECT symbol, weight, entry_date FROM target_holdings WHERE as_of=?", (d,)
            ).fetchall()
        return {r[0]: {"weight": r[1], "entry_date": r[2]} for r in rows}

    def set_target_holdings(self, as_of: str, holdings: dict):
        """holdings: {symbol: {'weight','entry_date'}}. Replaces the as_of snapshot."""
        with self._conn() as c:
            c.execute("DELETE FROM target_holdings WHERE as_of=?", (as_of,))
            c.executemany(
                "INSERT INTO target_holdings(as_of,symbol,weight,entry_date) VALUES(?,?,?,?)",
                [(as_of, s, float(info.get("weight", 0)), info.get("entry_date")) for s, info in holdings.items()],
            )

    def derive_entry_dates(self, as_of: str, new_symbols: list[str]) -> dict:
        """Keep old entry_date for held symbols; new symbols enter today."""
        prev = self.get_target_holdings()
        out = {}
        for s in new_symbols:
            if s in prev and prev[s].get("entry_date"):
                out[s] = prev[s]["entry_date"]
            else:
                out[s] = as_of
        return out

    # ---- actual holdings (user-reported, for adherence) ----
    def record_actual(self, as_of: str, holdings: dict):
        with self._conn() as c:
            c.execute("DELETE FROM actual_holdings WHERE as_of=?", (as_of,))
            c.executemany(
                "INSERT INTO actual_holdings(as_of,symbol,weight) VALUES(?,?,?)",
                [(as_of, s, float(w)) for s, w in holdings.items()],
            )

    def latest_actual(self) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute("SELECT MAX(as_of) FROM actual_holdings").fetchone()
            if not row or not row[0]:
                return None
            rows = c.execute(
                "SELECT symbol, weight FROM actual_holdings WHERE as_of=?", (row[0],)
            ).fetchall()
        return {"as_of": row[0], "holdings": {r[0]: r[1] for r in rows}}

    def adherence(self) -> dict:
        """Drift between latest target and latest actual holdings."""
        tgt = self.get_target_holdings()
        act = (self.latest_actual() or {}).get("holdings", {})
        if not act:
            return {"available": False}
        syms = set(tgt) | set(act)
        drift = sum(abs(tgt.get(s, {}).get("weight", 0) - act.get(s, 0)) for s in syms)
        max_drift = sum(max(tgt.get(s, {}).get("weight", 0), act.get(s, 0)) for s in syms) or 1
        return {
            "available": True,
            "target_as_of": self.latest_target_date(),
            "adherence_pct": round((1 - drift / max_drift) * 100, 1),
            "total_drift": round(drift, 3),
            "target": {s: round(i.get("weight", 0), 3) for s, i in tgt.items()},
            "actual": {s: round(w, 3) for s, w in act.items()},
        }

    # ---- report idempotency ----
    def mark_report_sent(self, as_of: str, channel: str, ok: bool):
        with self._conn() as c:
            c.execute(
                "INSERT INTO sent_reports(as_of,channel,ok,ts) VALUES(?,?,?,?) "
                "ON CONFLICT(as_of,channel) DO UPDATE SET ok=excluded.ok,ts=excluded.ts",
                (as_of, channel, 1 if ok else 0, datetime.now().isoformat()),
            )

    def report_sent_today(self, as_of: str) -> bool:
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM sent_reports WHERE as_of=? AND ok=1 LIMIT 1", (as_of,)
            ).fetchone()
        return bool(row)
