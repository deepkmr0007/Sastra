"""Task persistence, state machine, limits, retries, idempotency and leases."""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass

from ..audit import AuditLog
from ..db import Database, dumps, loads, new_id, now

CREATED, PLANNING, AWAITING_APPROVAL, RUNNING, VERIFYING, COMPLETED = (
    "CREATED", "PLANNING", "AWAITING_APPROVAL", "RUNNING", "VERIFYING", "COMPLETED")
BLOCKED, FAILED, CANCELLED, NEEDS_USER_INPUT = "BLOCKED", "FAILED", "CANCELLED", "NEEDS_USER_INPUT"

TERMINAL = {COMPLETED, FAILED, CANCELLED}
TRANSITIONS: dict[str, set[str]] = {
    CREATED: {PLANNING, CANCELLED, BLOCKED, FAILED},
    PLANNING: {RUNNING, AWAITING_APPROVAL, NEEDS_USER_INPUT, BLOCKED, FAILED, CANCELLED},
    AWAITING_APPROVAL: {RUNNING, BLOCKED, FAILED, CANCELLED},
    RUNNING: {RUNNING, VERIFYING, AWAITING_APPROVAL, NEEDS_USER_INPUT, BLOCKED, FAILED, CANCELLED},
    VERIFYING: {COMPLETED, RUNNING, FAILED, CANCELLED},
    BLOCKED: {PLANNING, RUNNING, CANCELLED, FAILED},
    NEEDS_USER_INPUT: {PLANNING, RUNNING, CANCELLED},
    COMPLETED: set(), FAILED: {PLANNING}, CANCELLED: set(),  # FAILED -> PLANNING = manual retry
}
RUNNABLE = (CREATED, PLANNING, RUNNING, VERIFYING)


class InvalidTransition(Exception):
    pass


class LimitExceeded(Exception):
    pass


@dataclass
class Limits:
    max_seconds: int = 900
    max_tokens: int = 60_000
    max_tool_calls: int = 30
    max_cost_usd: float = 0.25
    max_retries: int = 3
    max_steps: int = 12

    @classmethod
    def from_dict(cls, d: dict | None) -> "Limits":
        base = cls()
        for k, v in (d or {}).items():
            if hasattr(base, k):
                setattr(base, k, type(getattr(base, k))(v))
        return base


class TaskService:
    def __init__(self, db: Database, audit: AuditLog):
        self.db = db
        self.audit = audit

    # ------------------------------------------------------------- create
    def create(self, request: str, *, kind: str, mode: int, title: str | None = None, project_id: str | None = None,
               parent_id: str | None = None, limits: dict | None = None, idempotency_key: str | None = None,
               actor: str = "owner") -> dict:
        if idempotency_key:
            existing = self.db.one("SELECT * FROM tasks WHERE idempotency_key = ?", (idempotency_key,))
            if existing:
                return self._public(existing)
        tid = new_id()
        t = now()
        row = {"id": tid, "parent_id": parent_id, "project_id": project_id, "title": (title or request)[:120],
               "request": request, "kind": kind, "state": CREATED, "mode": int(mode), "plan": "[]", "step_index": 0,
               "result": None, "error": None, "idempotency_key": idempotency_key,
               "limits": dumps(asdict(Limits.from_dict(limits))), "usage": dumps({"tokens": 0, "tool_calls": 0, "cost_usd": 0.0, "outputs": {}}),
               "attempts": 0, "next_run_at": t, "lease_owner": None, "lease_expires_at": None, "cancel_requested": 0,
               "created_at": t, "updated_at": t, "started_at": None, "finished_at": None}
        try:
            self.db.insert("tasks", row)
        except Exception:
            if idempotency_key:  # concurrent duplicate: return the winner
                existing = self.db.one("SELECT * FROM tasks WHERE idempotency_key = ?", (idempotency_key,))
                if existing:
                    return self._public(existing)
            raise
        self.event(tid, "created", f"Task created ({kind})")
        self.audit.record(actor, "task.create", tid, kind=kind, mode=mode)
        return self.get(tid)

    # ---------------------------------------------------------- transitions
    def transition(self, tid: str, to: str, *, message: str = "", **fields) -> dict:
        with self.db.transaction():
            row = self.db.one("SELECT * FROM tasks WHERE id = ?", (tid,))
            if not row:
                raise KeyError(tid)
            frm = row["state"]
            if to not in TRANSITIONS[frm]:
                raise InvalidTransition(f"{frm} -> {to} not allowed")
            upd = {"state": to, "updated_at": now(), **fields}
            if to == RUNNING and not row["started_at"]:
                upd["started_at"] = now()
            if to in TERMINAL:
                upd["finished_at"] = now()
                upd["lease_owner"] = None
                upd["lease_expires_at"] = None
            self.db.update("tasks", tid, upd)
        self.event(tid, "state", message or f"{frm} → {to}", frm=frm, to=to)
        return self.get(tid)

    def event(self, tid: str, kind: str, message: str, **data) -> None:
        self.db.insert("task_events", {"id": new_id(), "task_id": tid, "ts": now(), "kind": kind, "message": message[:2000],
                                       "data": dumps(data)})

    def cancel(self, tid: str, actor: str) -> dict:
        task = self.get(tid)
        if task["state"] in TERMINAL:
            return task
        self.db.update("tasks", tid, {"cancel_requested": 1, "updated_at": now()})
        self.audit.record(actor, "task.cancel", tid)
        # If nobody holds the lease, cancel immediately; otherwise the worker stops at the next step boundary.
        row = self.db.one("SELECT * FROM tasks WHERE id = ?", (tid,))
        if not row["lease_owner"] or (row["lease_expires_at"] or 0) < now():
            return self.transition(tid, CANCELLED, message=f"Cancelled by {actor}")
        return self.get(tid)

    def retry(self, tid: str, actor: str) -> dict:
        task = self.get(tid)
        if task["state"] != FAILED:
            raise InvalidTransition("Only failed tasks can be retried manually")
        self.audit.record(actor, "task.retry", tid)
        return self.transition(tid, PLANNING, message="Manual retry", attempts=0, error=None, next_run_at=now(),
                               step_index=0, plan="[]")

    def schedule_retry(self, tid: str, error: str) -> dict:
        task = self.get(tid)
        limits = Limits.from_dict(task["limits"])
        attempts = task["attempts"] + 1
        if attempts > limits.max_retries:
            return self.transition(tid, FAILED, message=f"Failed after {attempts - 1} retries: {error}", error=error, attempts=attempts)
        delay = min(600.0, 2 ** attempts) * (0.8 + 0.4 * random.random())  # noqa: S311 - jitter, not security
        self.db.update("tasks", tid, {"attempts": attempts, "next_run_at": now() + delay, "error": error,
                                      "lease_owner": None, "lease_expires_at": None, "updated_at": now()})
        self.event(tid, "retry", f"Retry {attempts}/{limits.max_retries} in {delay:.0f}s: {error}")
        if task["state"] != RUNNING:
            self.transition(tid, RUNNING, message="Retry scheduled")
        return self.get(tid)

    # --------------------------------------------------------------- leases
    def claim(self, worker_id: str, lease_s: int = 120) -> dict | None:
        t = now()
        with self.db.transaction():
            row = self.db.one(
                "SELECT * FROM tasks WHERE state IN ('CREATED','PLANNING','RUNNING','VERIFYING') AND cancel_requested = 0 "
                "AND (next_run_at IS NULL OR next_run_at <= ?) AND (lease_expires_at IS NULL OR lease_expires_at < ?) "
                "ORDER BY next_run_at LIMIT 1", (t, t))
            if not row:
                return None
            n = self.db.execute("UPDATE tasks SET lease_owner = ?, lease_expires_at = ? WHERE id = ? AND "
                                "(lease_expires_at IS NULL OR lease_expires_at < ?)", (worker_id, t + lease_s, row["id"], t))
            if n != 1:
                return None
        if row["lease_owner"]:
            self.event(row["id"], "recovered", f"Lease of {row['lease_owner']} expired; resumed by {worker_id}")
        return self.get(row["id"])

    def heartbeat(self, tid: str, worker_id: str, lease_s: int = 120) -> None:
        self.db.execute("UPDATE tasks SET lease_expires_at = ? WHERE id = ? AND lease_owner = ?", (now() + lease_s, tid, worker_id))

    def release(self, tid: str, worker_id: str) -> None:
        self.db.execute("UPDATE tasks SET lease_owner = NULL, lease_expires_at = NULL WHERE id = ? AND lease_owner = ?", (tid, worker_id))

    def pending_cancels(self) -> list[dict]:
        return self.db.query("SELECT id FROM tasks WHERE cancel_requested = 1 AND state NOT IN ('COMPLETED','FAILED','CANCELLED') "
                             "AND (lease_expires_at IS NULL OR lease_expires_at < ?)", (now(),))

    # --------------------------------------------------------------- usage
    def add_usage(self, tid: str, tokens: int = 0, tool_calls: int = 0, cost_usd: float = 0.0) -> dict:
        task = self.get(tid)
        u = task["usage"]
        u["tokens"] = u.get("tokens", 0) + tokens
        u["tool_calls"] = u.get("tool_calls", 0) + tool_calls
        u["cost_usd"] = round(u.get("cost_usd", 0.0) + cost_usd, 6)
        self.db.update("tasks", tid, {"usage": dumps(u), "updated_at": now()})
        return u

    def set_output(self, tid: str, key: str, value) -> None:
        task = self.get(tid)
        u = task["usage"]
        u.setdefault("outputs", {})[key] = value
        self.db.update("tasks", tid, {"usage": dumps(u)})

    def check_limits(self, task: dict) -> None:
        lim = Limits.from_dict(task["limits"])
        u = task["usage"]
        started = task["started_at"] or task["created_at"]
        if now() - started > lim.max_seconds:
            raise LimitExceeded(f"Time limit of {lim.max_seconds}s exceeded")
        if u.get("tokens", 0) > lim.max_tokens:
            raise LimitExceeded(f"Token limit of {lim.max_tokens} exceeded")
        if u.get("tool_calls", 0) > lim.max_tool_calls:
            raise LimitExceeded(f"Tool-call limit of {lim.max_tool_calls} exceeded")
        if u.get("cost_usd", 0) > lim.max_cost_usd:
            raise LimitExceeded(f"Cost limit of ${lim.max_cost_usd} exceeded")
        if task["step_index"] > lim.max_steps:
            raise LimitExceeded(f"Step limit of {lim.max_steps} exceeded")

    # ---------------------------------------------------------------- read
    @staticmethod
    def _public(row: dict) -> dict:
        r = dict(row)
        for k, d in (("plan", []), ("result", None), ("limits", {}), ("usage", {})):
            r[k] = loads(r.get(k), d)
        return r

    def get(self, tid: str) -> dict:
        row = self.db.one("SELECT * FROM tasks WHERE id = ?", (tid,))
        if not row:
            raise KeyError(tid)
        return self._public(row)

    def events(self, tid: str, limit: int = 500) -> list[dict]:
        rows = self.db.query("SELECT * FROM task_events WHERE task_id = ? ORDER BY ts LIMIT ?", (tid, limit))
        for r in rows:
            r["data"] = loads(r["data"], {})
        return rows

    def list(self, state: str | None = None, project_id: str | None = None, limit: int = 100) -> list[dict]:
        clauses, params = [], []
        if state:
            clauses.append("state = ?")
            params.append(state)
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return [self._public(r) for r in self.db.query(f"SELECT * FROM tasks{where} ORDER BY created_at DESC LIMIT ?", [*params, limit])]
