"""Typed tool registry and the action gateway.

Every tool declares: input schema, permissions, data exposure, network use,
cost, reversibility, risk, timeout and minimum autonomy mode. The gateway
enforces — outside the language model — kill switches, least-privilege agent
roles, autonomy modes, approvals/scoped grants, budgets, idempotency, timeouts,
canary leak detection and audit logging.
"""
from __future__ import annotations

import concurrent.futures as cf
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from ..audit import AuditLog, SecurityEvents
from ..budget import Budget, BudgetExceeded
from ..controls import Controls, Mode
from ..db import Database, dumps, loads, new_id, now
from ..security.crypto import sha256_hex
from ..security.redact import redact
from ..security.untrusted import Canary

RISK_LEVELS = ("low", "medium", "high", "critical")

# Least privilege: which tools each agent role may even attempt.
ROLE_TOOLS: dict[str, set[str]] = {
    "chat": {"calculator", "memory_search", "web_search", "fetch_page", "notify"},
    "researcher": {"calculator", "memory_search", "memory_propose", "web_search", "fetch_page", "research_report", "notify",
                   "document_generate"},
    "coder": {"calculator", "memory_search", "memory_propose", "fs_list", "fs_read", "fs_write", "code_run", "git_checkpoint",
              "git_log", "git_rollback", "secrets_scan", "artifact_store", "android_project_generate", "document_generate",
              "spreadsheet_analyze", "notify"},
    "analyst": {"calculator", "memory_search", "fs_list", "fs_read", "spreadsheet_analyze", "document_generate",
                "artifact_store", "code_run", "notify"},
    "operator": {"*"},  # owner-initiated actions; approvals still apply
    "scheduler": {"calculator", "memory_search", "web_search", "fetch_page", "research_report", "notify", "document_generate"},
}


class ToolError(Exception):
    pass


class ToolBlocked(ToolError):
    pass


class ApprovalRequired(ToolError):
    def __init__(self, approval_id: str, reason: str):
        super().__init__(reason)
        self.approval_id = approval_id


@dataclass
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[["CallContext", BaseModel], dict]
    permissions: list[str] = field(default_factory=list)
    data_exposure: str = "none"  # none | public | private
    network: bool = False
    cost_usd: float = 0.0
    reversible: bool = True
    risk: str = "low"
    timeout_s: int = 60
    min_mode: Mode = Mode.ADVISORY
    side_effects: bool = False
    approval: Callable[[BaseModel], bool] | None = None  # extra approval rule based on args

    def needs_approval(self, args: BaseModel) -> bool:
        if self.risk in ("high", "critical") or not self.reversible:
            return True
        return bool(self.approval and self.approval(args))

    def declaration(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_model.model_json_schema(),
                "permissions": self.permissions, "data_exposure": self.data_exposure, "network": self.network,
                "cost_usd": self.cost_usd, "reversible": self.reversible, "risk": self.risk, "timeout_s": self.timeout_s,
                "min_mode": int(self.min_mode), "side_effects": self.side_effects}


@dataclass
class CallContext:
    actor: str
    role: str
    task_id: str | None = None
    project_id: str | None = None
    idempotency_key: str | None = None
    services: Any = None  # the App container, for handlers


class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.risk not in RISK_LEVELS:
            raise ValueError(spec.risk)
        self.tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        if name not in self.tools:
            raise ToolError(f"Unknown tool: {name}")
        return self.tools[name]

    def for_role(self, role: str) -> list[ToolSpec]:
        allowed = ROLE_TOOLS.get(role, set())
        return [t for t in self.tools.values() if "*" in allowed or t.name in allowed]


class Approvals:
    def __init__(self, db: Database, audit: AuditLog, ttl_s: int = 7 * 86400):
        self.db, self.audit, self.ttl_s = db, audit, ttl_s

    @staticmethod
    def args_hash(tool: str, args: dict) -> str:
        return sha256_hex(tool + "|" + dumps(args))

    def request(self, task_id: str | None, tool: str, args: dict, risk: str, reason: str) -> str:
        h = self.args_hash(tool, args)
        existing = self.db.one("SELECT id FROM approvals WHERE args_hash = ? AND COALESCE(task_id,'') = ? AND status = 'pending'",
                               (h, task_id or ""))
        if existing:
            return existing["id"]
        aid = new_id()
        self.db.insert("approvals", {"id": aid, "task_id": task_id, "tool": tool, "args": redact(dumps(args)), "args_hash": h,
                                     "risk": risk, "reason": reason, "status": "pending", "decided_at": None,
                                     "decision_note": None, "created_at": now(), "expires_at": now() + self.ttl_s})
        self.audit.record("system", "approval.request", aid, tool=tool, task_id=task_id, risk=risk)
        return aid

    def consume_approved(self, task_id: str | None, tool: str, args: dict) -> bool:
        """An approval covers exactly one execution of exactly these arguments."""
        h = self.args_hash(tool, args)
        with self.db.transaction():
            row = self.db.one("SELECT id FROM approvals WHERE args_hash = ? AND COALESCE(task_id,'') = ? AND status = 'approved' "
                              "AND expires_at > ?", (h, task_id or "", now()))
            if not row:
                return False
            self.db.update("approvals", row["id"], {"status": "used"})
        return True

    def use_grant(self, tool: str, args: dict) -> str | None:
        """Scoped batch approvals: a grant names one tool, an argument scope (exact
        values that must match), a use count and an expiry. Never widens implicitly."""
        with self.db.transaction():
            for g in self.db.query("SELECT * FROM grants WHERE tool = ? AND revoked_at IS NULL AND expires_at > ? AND uses < max_uses",
                                   (tool, now())):
                scope = loads(g["scope"], {})
                if all(args.get(k) == v for k, v in scope.items()):
                    self.db.update("grants", g["id"], {"uses": g["uses"] + 1})
                    return g["id"]
        return None

    def decide(self, aid: str, approve: bool, actor: str, note: str = "", grant: dict | None = None) -> dict:
        row = self.db.one("SELECT * FROM approvals WHERE id = ?", (aid,))
        if not row or row["status"] != "pending":
            raise ToolError("Approval not pending")
        if row["expires_at"] < now():
            self.db.update("approvals", aid, {"status": "expired"})
            raise ToolError("Approval request expired")
        self.db.update("approvals", aid, {"status": "approved" if approve else "rejected", "decided_at": now(),
                                          "decision_note": note[:500]})
        self.audit.record(actor, "approval." + ("approve" if approve else "reject"), aid, tool=row["tool"], note=note)
        if approve and grant:
            self.create_grant(row["tool"], grant.get("scope", {}), int(grant.get("max_uses", 1)),
                              float(grant.get("ttl_hours", 24)), actor)
        return self.db.one("SELECT * FROM approvals WHERE id = ?", (aid,))

    def create_grant(self, tool: str, scope: dict, max_uses: int, ttl_hours: float, actor: str) -> str:
        if max_uses < 1 or max_uses > 100 or ttl_hours <= 0 or ttl_hours > 24 * 30:
            raise ToolError("Grant must allow 1–100 uses and last at most 30 days")
        gid = new_id()
        self.db.insert("grants", {"id": gid, "tool": tool, "scope": dumps(scope), "max_uses": max_uses, "uses": 0,
                                  "expires_at": now() + ttl_hours * 3600, "created_at": now(), "revoked_at": None})
        self.audit.record(actor, "grant.create", gid, tool=tool, scope=scope, max_uses=max_uses, ttl_hours=ttl_hours)
        return gid

    def revoke_grant(self, gid: str, actor: str) -> None:
        self.db.update("grants", gid, {"revoked_at": now()})
        self.audit.record(actor, "grant.revoke", gid)

    def pending(self) -> list[dict]:
        self.db.execute("UPDATE approvals SET status = 'expired' WHERE status = 'pending' AND expires_at < ?", (now(),))
        return self.db.query("SELECT * FROM approvals WHERE status = 'pending' ORDER BY created_at DESC")

    def list(self, limit: int = 100) -> list[dict]:
        return self.db.query("SELECT * FROM approvals ORDER BY created_at DESC LIMIT ?", (limit,))

    def grants(self) -> list[dict]:
        return self.db.query("SELECT * FROM grants ORDER BY created_at DESC LIMIT 100")


class ActionGateway:
    def __init__(self, registry: ToolRegistry, controls: Controls, approvals: Approvals, budget: Budget, db: Database,
                 audit: AuditLog, events: SecurityEvents, canary: Canary):
        self.registry, self.controls, self.approvals, self.budget = registry, controls, approvals, budget
        self.db, self.audit, self.events, self.canary = db, audit, events, canary
        self._pool = cf.ThreadPoolExecutor(max_workers=4, thread_name_prefix="tool")

    def invoke(self, name: str, raw_args: dict, ctx: CallContext) -> dict:
        spec = self.registry.get(name)
        # 1. schema validation
        try:
            args = spec.input_model.model_validate(raw_args)
        except ValidationError as e:
            raise ToolError(f"Invalid arguments for {name}: {e.errors()[:3]}") from e
        args_dict = args.model_dump()
        # 2. global switches
        if self.controls.get("agents_paused") and ctx.role != "operator":
            raise ToolBlocked("All agents are paused (Security → Resume)")
        if name in (self.controls.get("disabled_tools") or []):
            raise ToolBlocked(f"Tool '{name}' is disabled by the owner")
        if spec.network and not self.controls.get("internet_enabled"):
            raise ToolBlocked("Internet access is switched off")
        # 3. least privilege
        allowed = ROLE_TOOLS.get(ctx.role, set())
        if "*" not in allowed and name not in allowed:
            self.audit.record(ctx.actor, "tool.denied_role", name, role=ctx.role, task_id=ctx.task_id)
            raise ToolBlocked(f"Role '{ctx.role}' may not use '{name}'")
        # 4. autonomy mode
        mode = self.controls.mode
        if mode < spec.min_mode:
            raise ToolBlocked(f"'{name}' needs autonomy mode ≥ {int(spec.min_mode)} ({spec.min_mode.name}); current is {mode.name}")
        if mode == Mode.ADVISORY and spec.side_effects:
            raise ToolBlocked("Advisory mode: actions with side effects are disabled")
        # 5. exfiltration canary
        if self.canary.leaked_in(dumps(args_dict)):
            self.events.raise_event("canary_leak", "critical", f"Protected instruction text appeared in arguments to {name}")
            raise ToolBlocked("Blocked: arguments contain protected system content")
        # 6. idempotency
        if ctx.idempotency_key:
            prior = self.db.one("SELECT * FROM tool_calls WHERE idempotency_key = ?", (ctx.idempotency_key,))
            if prior and prior["status"] == "ok":
                return loads(prior["output"], {})
            if prior:  # earlier attempt failed or timed out: allow a fresh attempt under the same key
                self.db.execute("DELETE FROM tool_calls WHERE id = ?", (prior["id"],))
        # 7. approvals
        via = None
        if spec.needs_approval(args):
            if self.approvals.consume_approved(ctx.task_id, name, args_dict):
                via = "approval"
            else:
                gid = self.approvals.use_grant(name, args_dict)
                if gid:
                    via = f"grant:{gid}"
                else:
                    aid = self.approvals.request(ctx.task_id, name, args_dict, spec.risk,
                                                 f"{spec.description} (risk {spec.risk}, {'reversible' if spec.reversible else 'NOT reversible'})")
                    raise ApprovalRequired(aid, f"Owner approval needed for {name}")
        # 8. budget
        if spec.cost_usd:
            try:
                self.budget.check(spec.cost_usd)
            except BudgetExceeded as e:
                raise ToolBlocked(str(e)) from e
        # 9. execute with timeout
        cid = new_id()
        started = time.perf_counter()
        self.db.insert("tool_calls", {"id": cid, "task_id": ctx.task_id, "tool": name, "idempotency_key": ctx.idempotency_key,
                                      "args": redact(dumps(args_dict))[:20000], "status": "running", "output": None, "error": None,
                                      "duration_ms": None, "created_at": now()})
        fut = self._pool.submit(spec.handler, ctx, args)
        try:
            output = fut.result(timeout=spec.timeout_s)
        except cf.TimeoutError:
            self._finish(cid, "timeout", None, "timed out", started)
            raise ToolError(f"{name} timed out after {spec.timeout_s}s")
        except ApprovalRequired:
            self._finish(cid, "awaiting_approval", None, None, started)
            raise
        except Exception as e:  # noqa: BLE001
            self._finish(cid, "error", None, str(e), started)
            self.audit.record(ctx.actor, "tool.error", name, task_id=ctx.task_id, error=str(e)[:300])
            raise ToolError(f"{name} failed: {e}") from e
        if spec.cost_usd:
            self.budget.record(name, "tool", "tool", spec.cost_usd, task_id=ctx.task_id, project_id=ctx.project_id)
        self._finish(cid, "ok", output, None, started)
        self.audit.record(ctx.actor, "tool.call", name, task_id=ctx.task_id, role=ctx.role, via=via, risk=spec.risk)
        return output

    def _finish(self, cid: str, status: str, output: dict | None, error: str | None, started: float) -> None:
        self.db.update("tool_calls", cid, {"status": status, "output": redact(dumps(output))[:200000] if output is not None else None,
                                           "error": redact(error) if error else None,
                                           "duration_ms": int((time.perf_counter() - started) * 1000)})
