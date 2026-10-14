"""Cost ledger and hard spending caps (fail closed).

Prices are configuration, not code: defaults below are provisional figures
that MUST be checked against the provider's official pricing page and can be
overridden with PUT /api/budget/pricing. Unknown paid models are refused rather
than assumed free.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from .db import KV, Database, new_id, now

# USD per 1M tokens (input, output). Local/offline models are free.
DEFAULT_PRICING: dict[str, list[float]] = {
    "anthropic:claude-haiku-5-5": [0.10, 0.50],   # provisional — verify
    "anthropic:claude-sonnet-5-5": [2.00, 10.00],  # provisional — verify
}
# Search APIs: USD per request
DEFAULT_REQUEST_PRICING: dict[str, float] = {"brave": 0.005, "tavily": 0.008, "searxng": 0.0}


class BudgetExceeded(Exception):
    pass


class UnknownPrice(Exception):
    pass


def _month_start(ts: float) -> float:
    d = datetime.fromtimestamp(ts, tz=timezone.utc)
    return datetime(d.year, d.month, 1, tzinfo=timezone.utc).timestamp()


def _day_start(ts: float) -> float:
    d = datetime.fromtimestamp(ts, tz=timezone.utc)
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()


class Budget:
    def __init__(self, db: Database, kv: KV, daily_usd: float, monthly_usd: float):
        self.db = db
        self.kv = kv
        self.default_daily = daily_usd
        self.default_monthly = monthly_usd

    # ------------------------------------------------------------ config
    @property
    def pricing(self) -> dict[str, list[float]]:
        return {**DEFAULT_PRICING, **(self.kv.get("pricing") or {})}

    @property
    def request_pricing(self) -> dict[str, float]:
        return {**DEFAULT_REQUEST_PRICING, **(self.kv.get("request_pricing") or {})}

    @property
    def limits(self) -> dict:
        return {"daily_usd": self.kv.get("daily_budget_usd", self.default_daily),
                "monthly_usd": self.kv.get("monthly_budget_usd", self.default_monthly)}

    def set_limits(self, daily_usd: float, monthly_usd: float) -> None:
        if daily_usd < 0 or monthly_usd < 0:
            raise ValueError("Budgets cannot be negative")
        self.kv.set("daily_budget_usd", float(daily_usd))
        self.kv.set("monthly_budget_usd", float(monthly_usd))

    def set_price(self, provider_model: str, input_per_m: float, output_per_m: float) -> None:
        p = self.kv.get("pricing") or {}
        p[provider_model] = [float(input_per_m), float(output_per_m)]
        self.kv.set("pricing", p)

    # ------------------------------------------------------------ math
    def price_tokens(self, provider: str, model: str, tokens_in: int, tokens_out: int, is_local: bool) -> float:
        if is_local or provider == "offline":
            return 0.0
        key = f"{provider}:{model}"
        if key not in self.pricing:
            raise UnknownPrice(f"No price configured for {key}; set it before use (fail-closed)")
        pin, pout = self.pricing[key]
        return (tokens_in * pin + tokens_out * pout) / 1_000_000

    def spent(self, since: float) -> float:
        return float(self.db.scalar("SELECT COALESCE(SUM(cost_usd), 0) AS s FROM usage_ledger WHERE ts >= ?", (since,)) or 0.0)

    def status(self) -> dict:
        t = time.time()
        lim = self.limits
        day, month = self.spent(_day_start(t)), self.spent(_month_start(t))
        return {"spent_today_usd": round(day, 6), "spent_month_usd": round(month, 6), **lim,
                "remaining_today_usd": round(max(0.0, lim["daily_usd"] - day), 6),
                "remaining_month_usd": round(max(0.0, lim["monthly_usd"] - month), 6)}

    def check(self, projected_usd: float) -> None:
        """Raise unless projected spend fits under every cap."""
        if projected_usd <= 0:
            return
        s = self.status()
        if projected_usd > s["remaining_today_usd"]:
            raise BudgetExceeded(f"Daily cap reached (spent ${s['spent_today_usd']:.4f} of ${s['daily_usd']:.2f})")
        if projected_usd > s["remaining_month_usd"]:
            raise BudgetExceeded(f"Monthly cap reached (spent ${s['spent_month_usd']:.4f} of ${s['monthly_usd']:.2f})")

    def record(self, provider: str, model: str, kind: str, cost_usd: float, tokens_in: int = 0, tokens_out: int = 0,
               task_id: str | None = None, project_id: str | None = None) -> None:
        self.db.insert("usage_ledger", {"id": new_id(), "ts": now(), "provider": provider, "model": model, "kind": kind,
                                        "task_id": task_id, "project_id": project_id, "tokens_in": tokens_in,
                                        "tokens_out": tokens_out, "cost_usd": cost_usd})

    def report(self, days: int = 30) -> dict:
        since = now() - days * 86400
        by = {}
        for col in ("provider", "project_id", "task_id", "kind"):
            by[col] = self.db.query(
                f"SELECT {col} AS key, COUNT(*) AS calls, SUM(tokens_in) AS tokens_in, SUM(tokens_out) AS tokens_out, "
                f"SUM(cost_usd) AS cost_usd FROM usage_ledger WHERE ts >= ? GROUP BY {col} ORDER BY cost_usd DESC LIMIT 50", (since,))
        return {"days": days, "by": by, "status": self.status()}
