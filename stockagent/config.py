"""Central configuration loading: paths, params.yaml, etf_pool.yaml, .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Project root = parent of this package's directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
PARAMS_FILE = CONFIG_DIR / "params.yaml"
POOL_FILE = CONFIG_DIR / "etf_pool.yaml"
INDUSTRY_FILE = CONFIG_DIR / "stock_industry.yaml"  # 候选个股池(V7): 东财板块→三类+商品映射
ENV_FILE = PROJECT_ROOT / ".env"


def _load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass
class Config:
    params: dict
    pool: dict
    env: dict
    industry: dict = field(default_factory=dict)  # stock_industry.yaml(缺文件 → {} 容错)

    @property
    def db_path(self) -> Path:
        rel = self.env.get("SA_DB_PATH") or self.params.get("data", {}).get(
            "db_path", "data/stockagent.sqlite"
        )
        p = Path(rel)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    # --- pool accessors ---
    @property
    def benchmark_symbol(self) -> str:
        return self.params["regime"]["benchmark_symbol"]

    @property
    def risk_off_symbol(self) -> str:
        return self.params["regime"]["risk_off_symbol"]

    def rotation_symbols(self) -> list[str]:
        """Unique, ordered rotation-pool symbols (deduped, preserves first occurrence)."""
        seen, out = set(), []
        for row in self.pool.get("rotation_pool", []):
            s = str(row["symbol"])
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out

    def symbol_meta(self) -> dict[str, dict]:
        """symbol -> {name, sector, role} for all known symbols."""
        meta: dict[str, dict] = {}
        b = self.pool.get("benchmark") or {}
        if b:
            meta[str(b["symbol"])] = {**b, "role": "regime_benchmark"}
        r = self.pool.get("risk_off") or {}
        if r:
            meta[str(r["symbol"])] = {**r, "role": "risk_off_parking"}
        for row in self.pool.get("rotation_pool", []):
            s = str(row["symbol"])
            if s not in meta:
                meta[s] = {**row, "role": "rotation"}
        return meta

    def all_symbols(self) -> list[str]:
        """All symbols the system must track (rotation + benchmark + risk-off)."""
        s = list(dict.fromkeys(self.rotation_symbols()))
        for extra in (self.benchmark_symbol, self.risk_off_symbol):
            if extra not in s:
                s.append(extra)
        return s

    def industry_class(self) -> dict[str, dict]:
        """东财行业板块 → {type: cyclic/growth/value, commodity: 品种|缺} 映射。
        未列出的板块 = 未映射(候选池 join_industry 诚实降级为 type=None)。"""
        return self.industry.get("industry_class", {}) or {}


@lru_cache(maxsize=1)
def get_config() -> Config:
    if ENV_FILE.exists():
        load_dotenv(ENV_FILE)
    env = dict(os.environ)
    params = _load_yaml(PARAMS_FILE)
    pool = _load_yaml(POOL_FILE)
    industry = _load_yaml(INDUSTRY_FILE) if INDUSTRY_FILE.exists() else {}
    return Config(params=params, pool=pool, env=env, industry=industry)
