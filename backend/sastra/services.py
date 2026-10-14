"""Smaller services: notifications, scheduler, learning loop, ventures, maintenance."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .audit import AuditLog
from .controls import Controls, Mode
from .db import KV, Database, dumps, loads, new_id, now


# ============================================================ notifications
class Notifications:
    def __init__(self, db: Database):
        self.db = db

    def add(self, kind: str, title: str, body: str, task_id: str | None = None) -> str:
        nid = new_id()
        self.db.insert("notifications", {"id": nid, "ts": now(), "kind": kind, "title": title[:120], "body": body[:2000],
                                         "task_id": task_id, "read_at": None})
        return nid

    def list(self, since: float = 0, unread_only: bool = False, limit: int = 100) -> list[dict]:
        q = "SELECT * FROM notifications WHERE ts > ?" + (" AND read_at IS NULL" if unread_only else "") + " ORDER BY ts DESC LIMIT ?"
        return self.db.query(q, (since, limit))

    def mark_read(self, nid: str) -> None:
        self.db.update("notifications", nid, {"read_at": now()})


# ================================================================ scheduler
class Scheduler:
    """Recurring workflows (autonomy mode 3). Each run becomes an ordinary task with
    its own budget, so every gate and limit still applies."""

    def __init__(self, db: Database, audit: AuditLog, controls: Controls):
        self.db, self.audit, self.controls = db, audit, controls
        self.tasks = None  # wired by App

    @staticmethod
    def _next(interval_minutes: int | None, daily_at: str | None, after: float) -> float:
        if interval_minutes:
            return after + interval_minutes * 60
        hh, mm = map(int, (daily_at or "09:00").split(":"))
        base = datetime.fromtimestamp(after, tz=timezone.utc)
        cand = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if cand.timestamp() <= after:
            cand += timedelta(days=1)
        return cand.timestamp()

    def create(self, name, request, kind, project_id, interval_minutes, daily_at, budget_usd, actor) -> str:
        if not (interval_minutes or daily_at):
            raise ValueError("interval_minutes or daily_at (UTC HH:MM) required")
        if interval_minutes is not None and interval_minutes < 60:
            raise ValueError("Minimum interval is 60 minutes")
        sid = new_id()
        self.db.insert("schedules", {"id": sid, "name": name, "request": request, "kind": kind, "project_id": project_id,
                                     "interval_minutes": interval_minutes, "daily_at": daily_at, "budget_usd": budget_usd,
                                     "enabled": 1, "next_run_at": self._next(interval_minutes, daily_at, now()),
                                     "last_run_at": None, "created_at": now()})
        self.audit.record(actor, "schedule.create", sid, name=name, kind=kind, budget_usd=budget_usd)
        return sid

    def list(self) -> list[dict]:
        return self.db.query("SELECT * FROM schedules ORDER BY created_at DESC")

    def set_enabled(self, sid: str, enabled: bool, actor: str) -> None:
        self.db.update("schedules", sid, {"enabled": int(enabled)})
        self.audit.record(actor, "schedule.enable" if enabled else "schedule.disable", sid)

    def delete(self, sid: str, actor: str) -> None:
        self.db.execute("DELETE FROM schedules WHERE id = ?", (sid,))
        self.audit.record(actor, "schedule.delete", sid)

    def tick(self) -> list[str]:
        """Create tasks for due schedules. Only runs in SCHEDULED mode and when workers are on."""
        if self.controls.mode < Mode.SCHEDULED or not self.controls.get("workers_enabled") or self.controls.get("agents_paused"):
            return []
        created = []
        for s in self.db.query("SELECT * FROM schedules WHERE enabled = 1 AND next_run_at <= ?", (now(),)):
            key = f"schedule:{s['id']}:{int(s['next_run_at'])}"
            t = self.tasks.create(s["request"], kind=s["kind"], mode=int(Mode.SCHEDULED), title=f"[scheduled] {s['name']}",
                                  project_id=s["project_id"], limits={"max_cost_usd": s["budget_usd"], "max_seconds": 600},
                                  idempotency_key=key, actor="scheduler")
            self.db.update("schedules", s["id"], {"last_run_at": now(),
                                                  "next_run_at": self._next(s["interval_minutes"], s["daily_at"], now())})
            created.append(t["id"])
        return created


# ============================================================ learning loop
# Tunable workflow parameters. Protected ones touch policy/permissions/security/
# credentials/model routing/spending and are NEVER changed without the owner.
WORKFLOW_PARAMS = {
    "research.max_sources": {"default": 6, "min": 2, "max": 10, "protected": False},
    "memory.search.min_score": {"default": 0.05, "min": 0.0, "max": 0.5, "protected": False},
    "tasks.default_max_retries": {"default": 3, "min": 0, "max": 6, "protected": False},
    "chat.history_messages": {"default": 16, "min": 4, "max": 60, "protected": False},
    "router.preference": {"default": None, "protected": True},
    "budget.daily_usd": {"default": None, "protected": True},
    "tools.disabled": {"default": None, "protected": True},
}


class Learning:
    """OBSERVE → EVALUATE → PROPOSE → TEST IN ISOLATION → COMPARE → APPROVE/REJECT → VERSION.

    This improves workflows (parameters, procedures), not model weights. Nothing
    here trains a model or sends private data anywhere."""

    def __init__(self, db: Database, kv: KV, audit: AuditLog):
        self.db, self.kv, self.audit = db, kv, audit

    # -------------------------------------------------------------- params
    def param(self, name: str):
        spec = WORKFLOW_PARAMS[name]
        return (self.kv.get("workflow_params") or {}).get(name, spec["default"])

    def _set_param(self, name: str, value, lesson_id: str | None, actor: str) -> int:
        params = self.kv.get("workflow_params") or {}
        params[name] = value
        self.kv.set("workflow_params", params)
        version = (self.db.scalar("SELECT MAX(version) FROM workflow_versions WHERE target = ?", (name,)) or 0) + 1
        self.db.insert("workflow_versions", {"id": new_id(), "target": name, "version": version, "body": dumps(value),
                                             "lesson_id": lesson_id, "created_at": now()})
        self.audit.record(actor, "learning.version", name, version=version, value=value)
        return version

    # ------------------------------------------------------------- observe
    def observe_task(self, task: dict) -> None:
        """Turn repeated failure patterns into proposals (evidence-based, rate-limited)."""
        if task["state"] != "FAILED" or not task.get("error"):
            return
        err = task["error"].lower()
        since = now() - 14 * 86400
        if "no usable sources" in err or "no search provider" in err:
            n = self.db.scalar("SELECT COUNT(*) FROM tasks WHERE state='FAILED' AND kind='research' AND created_at > ? "
                               "AND (error LIKE '%no usable sources%' OR error LIKE '%search provider%')", (since,))
            if n >= 3:
                self.propose("research.max_sources", min(10, self.param("research.max_sources") + 2),
                             f"{n} research tasks failed for lack of sources in 14 days", "Try more candidate sources per question")

    def feedback(self, message_id: str, rating: int, note: str, actor: str) -> str:
        """Owner feedback is stored as an observation; it never edits policy directly."""
        lid = new_id()
        self.db.insert("lessons", {"id": lid, "target": "feedback", "title": f"Feedback {'+' if rating > 0 else '-'} on message",
                                   "observation": dumps({"message_id": message_id, "rating": rating, "note": note[:2000]}),
                                   "proposal": "{}", "status": "observed", "test_result": None, "version": 0, "protected": 0,
                                   "created_at": now(), "decided_at": None})
        self.audit.record(actor, "learning.feedback", lid, rating=rating)
        return lid

    # ------------------------------------------------------------- propose
    def propose(self, target: str, value, observation: str, title: str) -> str | None:
        if target not in WORKFLOW_PARAMS:
            raise ValueError(f"Unknown workflow target {target}")
        spec = WORKFLOW_PARAMS[target]
        if not spec["protected"] and isinstance(value, (int, float)):
            value = max(spec["min"], min(spec["max"], value))
        if self.db.one("SELECT id FROM lessons WHERE target = ? AND status IN ('proposed','tested','awaiting_approval')", (target,)):
            return None  # one open proposal per target
        lid = new_id()
        self.db.insert("lessons", {"id": lid, "target": target, "title": title, "observation": observation,
                                   "proposal": dumps({"value": value, "previous": self.param(target)}),
                                   "status": "proposed", "test_result": None, "version": 0, "protected": int(spec["protected"]),
                                   "created_at": now(), "decided_at": None})
        self.audit.record("learning", "learning.propose", lid, param=target, value=value)
        return lid

    def test(self, lesson_id: str, evaluator) -> dict:
        """Run `evaluator(value) -> score` for baseline and candidate in isolation; higher is better."""
        lesson = self.db.one("SELECT * FROM lessons WHERE id = ?", (lesson_id,))
        prop = loads(lesson["proposal"], {})
        baseline = float(evaluator(prop.get("previous")))
        candidate = float(evaluator(prop.get("value")))
        result = {"baseline": baseline, "candidate": candidate, "improved": candidate > baseline}
        status = "awaiting_approval" if (lesson["protected"] or not result["improved"]) else "tested"
        self.db.update("lessons", lesson_id, {"test_result": dumps(result), "status": status})
        if status == "tested" and self.kv.get("auto_learning", False):
            self.apply(lesson_id, "learning", by_owner=False)
        return result

    def apply(self, lesson_id: str, actor: str, by_owner: bool) -> int:
        lesson = self.db.one("SELECT * FROM lessons WHERE id = ?", (lesson_id,))
        if lesson["protected"] and not by_owner:
            raise PermissionError("Protected targets require explicit owner approval")
        if not by_owner and not loads(lesson["test_result"], {}).get("improved"):
            raise PermissionError("Automatic application requires a passing isolated test")
        value = loads(lesson["proposal"], {}).get("value")
        version = self._set_param(lesson["target"], value, lesson_id, actor)
        self.db.update("lessons", lesson_id, {"status": "applied", "version": version, "decided_at": now()})
        return version

    def reject(self, lesson_id: str, actor: str) -> None:
        self.db.update("lessons", lesson_id, {"status": "rejected", "decided_at": now()})
        self.audit.record(actor, "learning.reject", lesson_id)

    def rollback(self, target: str, actor: str) -> int:
        rows = self.db.query("SELECT * FROM workflow_versions WHERE target = ? ORDER BY version DESC LIMIT 2", (target,))
        previous = loads(rows[1]["body"]) if len(rows) == 2 else WORKFLOW_PARAMS[target]["default"]
        return self._set_param(target, previous, None, actor)

    def list(self) -> dict:
        return {"lessons": self.db.query("SELECT * FROM lessons ORDER BY created_at DESC LIMIT 200"),
                "params": {k: {"value": self.param(k), **v} for k, v in WORKFLOW_PARAMS.items()},
                "versions": self.db.query("SELECT * FROM workflow_versions ORDER BY created_at DESC LIMIT 100"),
                "auto_learning": self.kv.get("auto_learning", False)}


# ================================================================ ventures
BASES = ("verified", "forecast", "assumption")
ENTRY_KINDS = ("revenue", "cost", "experiment", "metric")


class Ventures:
    """Income/product workspace. Verified money is kept apart from forecasts and
    assumptions; verified entries require evidence. Nothing here spends money or
    publishes — those go through approval-gated tools."""

    def __init__(self, db: Database, audit: AuditLog):
        self.db, self.audit = db, audit

    def create(self, name: str, hypothesis: str, budget_usd: float, project_id: str | None, actor: str) -> dict:
        vid = new_id()
        self.db.insert("ventures", {"id": vid, "name": name[:120], "hypothesis": hypothesis[:4000], "status": "idea",
                                    "budget_usd": max(0.0, budget_usd), "project_id": project_id, "created_at": now()})
        self.audit.record(actor, "venture.create", vid, name=name)
        return self.db.one("SELECT * FROM ventures WHERE id = ?", (vid,))

    def set_status(self, vid: str, status: str, actor: str) -> None:
        if status not in ("idea", "validating", "building", "launched", "paused", "stopped"):
            raise ValueError("bad status")
        self.db.update("ventures", vid, {"status": status})
        self.audit.record(actor, "venture.status", vid, status=status)

    def add_entry(self, vid: str, kind: str, amount: float, currency: str, basis: str, note: str, evidence: str, actor: str) -> str:
        if kind not in ENTRY_KINDS or basis not in BASES:
            raise ValueError(f"kind ∈ {ENTRY_KINDS}, basis ∈ {BASES}")
        if basis == "verified" and kind in ("revenue", "cost") and len(evidence.strip()) < 5:
            raise ValueError("Verified money needs evidence (invoice id, payout reference, statement line…)")
        eid = new_id()
        self.db.insert("venture_entries", {"id": eid, "venture_id": vid, "kind": kind, "amount": float(amount),
                                           "currency": currency.upper()[:3], "basis": basis, "note": note[:2000],
                                           "evidence": evidence[:2000], "ts": now()})
        self.audit.record(actor, "venture.entry", eid, venture=vid, kind=kind, basis=basis)
        return eid

    def summary(self, vid: str) -> dict:
        v = self.db.one("SELECT * FROM ventures WHERE id = ?", (vid,))
        if not v:
            raise KeyError(vid)
        rows = self.db.query("SELECT kind, basis, currency, SUM(amount) AS total, COUNT(*) AS n FROM venture_entries "
                             "WHERE venture_id = ? GROUP BY kind, basis, currency", (vid,))
        totals: dict = {}
        for r in rows:
            totals.setdefault(r["basis"], {}).setdefault(r["currency"], {})[r["kind"]] = round(r["total"], 2)
        verified_cost = sum(r["total"] for r in rows if r["basis"] == "verified" and r["kind"] == "cost")
        return {"venture": v, "totals_by_basis": totals,
                "budget_remaining_usd_verified_only": round(v["budget_usd"] - verified_cost, 2),
                "entries": self.db.query("SELECT * FROM venture_entries WHERE venture_id = ? ORDER BY ts DESC LIMIT 200", (vid,)),
                "disclaimer": "Forecasts and assumptions are not income. Only 'verified' rows with evidence are real."}

    def list(self) -> list[dict]:
        return self.db.query("SELECT * FROM ventures ORDER BY created_at DESC")
