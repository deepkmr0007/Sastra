"""Lightweight metrics: TTFT, latency, task success, citation quality, cost per task."""
from __future__ import annotations

from .db import Database, dumps, new_id, now


class Metrics:
    def __init__(self, db: Database):
        self.db = db

    def observe(self, name: str, value: float, **labels) -> None:
        self.db.insert("metrics", {"id": new_id(), "ts": now(), "name": name, "value": float(value), "labels": dumps(labels)})

    def summary(self, days: int = 7) -> dict:
        since = now() - days * 86400
        rows = self.db.query("SELECT name, COUNT(*) AS n, AVG(value) AS avg, MIN(value) AS min, MAX(value) AS max "
                             "FROM metrics WHERE ts >= ? GROUP BY name ORDER BY name", (since,))
        out = {r["name"]: {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items() if k != "name"} for r in rows}
        for name in list(out):
            vals = sorted(r["value"] for r in self.db.query("SELECT value FROM metrics WHERE name = ? AND ts >= ?", (name, since)))
            if vals:
                out[name]["p50"] = round(vals[len(vals) // 2], 3)
                out[name]["p95"] = round(vals[min(len(vals) - 1, int(len(vals) * 0.95))], 3)
        tasks = self.db.query("SELECT state, COUNT(*) AS n FROM tasks WHERE created_at >= ? GROUP BY state", (since,))
        counts = {r["state"]: r["n"] for r in tasks}
        finished = sum(counts.get(s, 0) for s in ("COMPLETED", "FAILED"))
        out["task_success_rate"] = round(counts.get("COMPLETED", 0) / finished, 3) if finished else None
        out["task_states"] = counts
        cost = self.db.one("SELECT COUNT(DISTINCT task_id) AS n, COALESCE(SUM(cost_usd),0) AS c FROM usage_ledger "
                           "WHERE task_id IS NOT NULL AND ts >= ?", (since,))
        out["cost_per_task_usd"] = round(cost["c"] / cost["n"], 6) if cost and cost["n"] else 0.0
        return out
