"""HTTP API (Starlette + Pydantic). All /api routes except setup/login require a
bearer token bound to a revocable session."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, ValidationError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from ..auth import AuthError, Principal
from ..backup import create_backup, list_backups
from ..budget import BudgetExceeded
from ..core import App
from ..memory.store import CATEGORIES, MemoryRejected
from ..research.engine import ResearchUnavailable
from ..security.netguard import NetworkPolicyError
from ..security.ratelimit import RateLimiter
from ..security.redact import redact
from ..tasks.orchestrator import KINDS, classify
from ..tasks.service import InvalidTransition
from ..tools.registry import ToolError
from ..workspace.projects import WorkspaceError

log = logging.getLogger("sastra.api")
MAX_BODY = 12 * 1024 * 1024
STATIC = Path(__file__).parent / "static"


class HTTPError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


# ------------------------------------------------------------------ bodies
class SetupIn(BaseModel):
    username: str = Field(min_length=2, max_length=40)
    password: str = Field(min_length=10, max_length=200)
    setup_code: str = Field(max_length=20)


class LoginIn(BaseModel):
    username: str = Field(max_length=40)
    password: str = Field(max_length=200)
    device_name: str = Field("device", max_length=80)
    totp: str | None = Field(None, max_length=10)


class PasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=10, max_length=200)


class CodeIn(BaseModel):
    code: str = Field(max_length=10)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=50_000)
    conversation_id: str | None = None
    project_id: str | None = None
    route: Literal["auto", "chat", "task"] = "auto"
    client_id: str | None = None


class ConversationIn(BaseModel):
    title: str = Field("New chat", max_length=120)
    project_id: str | None = None
    privacy: Literal["normal", "private"] = "normal"


class FeedbackIn(BaseModel):
    rating: int = Field(ge=-1, le=1)
    note: str = Field("", max_length=2000)


class TaskIn(BaseModel):
    request: str = Field(min_length=1, max_length=20_000)
    kind: str | None = None
    project_id: str | None = None
    title: str | None = Field(None, max_length=120)
    limits: dict[str, float] | None = None
    idempotency_key: str | None = Field(None, max_length=120)


class TaskInputIn(BaseModel):
    answer: str = Field(min_length=1, max_length=20_000)


class DecideIn(BaseModel):
    approve: bool
    note: str = Field("", max_length=500)
    grant: dict | None = None  # {"scope": {...}, "max_uses": n, "ttl_hours": h}


class ResearchIn(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    urls: list[str] = Field(default_factory=list, max_length=10)
    project_id: str | None = None
    freshness_days: int | None = Field(None, ge=1, le=3650)


class MemoryIn(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    category: str = "preference"
    subject: str = Field("", max_length=200)
    project_id: str | None = None
    ttl_hours: float | None = Field(None, gt=0, le=24 * 3650)


class MemoryPatch(BaseModel):
    content: str | None = Field(None, max_length=8000)
    subject: str | None = Field(None, max_length=200)
    category: str | None = None
    status: str | None = None


class ResolveIn(BaseModel):
    keep: Literal["new", "old", "both"]


class WipeIn(BaseModel):
    confirm: str


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=2000)
    kind: str = "general"


class FileIn(BaseModel):
    path: str = Field(max_length=500)
    content: str = Field(max_length=5_000_000)
    preview_only: bool = False


class CheckpointIn(BaseModel):
    message: str = Field("Checkpoint", max_length=200)


class RollbackIn(BaseModel):
    commit: str = Field(pattern=r"^[0-9a-f]{7,40}$")


class ToolRunIn(BaseModel):
    tool: str
    args: dict = Field(default_factory=dict)
    project_id: str | None = None


class ControlsIn(BaseModel):
    autonomy_mode: int | None = Field(None, ge=0, le=3)
    agents_paused: bool | None = None
    internet_enabled: bool | None = None
    workers_enabled: bool | None = None
    cloud_models_enabled: bool | None = None
    disabled_tools: list[str] | None = None


class ConsentIn(BaseModel):
    provider_consent: dict[str, str]


class PreferenceIn(BaseModel):
    order: list[str]


class BudgetIn(BaseModel):
    daily_usd: float = Field(ge=0, le=1000)
    monthly_usd: float = Field(ge=0, le=10000)


class PriceIn(BaseModel):
    provider_model: str = Field(pattern=r"^[a-z_]+:[\w.\-/]+$")
    input_per_m: float = Field(ge=0)
    output_per_m: float = Field(ge=0)


class ScheduleIn(BaseModel):
    name: str = Field(max_length=120)
    request: str = Field(max_length=2000)
    kind: Literal["research", "direct", "data", "document"] = "research"
    project_id: str | None = None
    interval_minutes: int | None = Field(None, ge=60)
    daily_at: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    budget_usd: float = Field(0.05, ge=0, le=5)


class VentureIn(BaseModel):
    name: str = Field(max_length=120)
    hypothesis: str = Field(max_length=4000)
    budget_usd: float = Field(0, ge=0)
    project_id: str | None = None


class EntryIn(BaseModel):
    kind: str
    amount: float
    currency: str = Field("INR", max_length=3)
    basis: str
    note: str = Field("", max_length=2000)
    evidence: str = Field("", max_length=2000)


class StatusIn(BaseModel):
    status: str


class RetentionIn(BaseModel):
    conversation_retention_days: int = Field(ge=0, le=3650)


class SyncIn(BaseModel):
    """Offline outbox from the Android app. Every item carries a client id so
    replays are idempotent."""
    messages: list[dict] = Field(default_factory=list, max_length=200)
    tasks: list[dict] = Field(default_factory=list, max_length=100)
    memories: list[dict] = Field(default_factory=list, max_length=200)


# ---------------------------------------------------------------- framework
Handler = Callable[[Request, Principal | None], Any]


def _json(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(json.loads(json.dumps(data, default=str)), status_code=status)


async def _body(request: Request, model: type[BaseModel]) -> BaseModel:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise HTTPError(413, "Request too large")
    try:
        return model.model_validate_json(raw or b"{}")
    except ValidationError as e:
        raise HTTPError(422, json.dumps(e.errors(include_url=False, include_input=False), default=str)) from e


def build(app: App | None = None) -> Starlette:
    core = app or App()
    general_limit = RateLimiter(rate_per_s=20, burst=80)
    login_limit = RateLimiter(rate_per_s=5 / 60, burst=5)
    routes: list[Route] = []

    def route(method: str, path: str, auth: bool = True):
        def deco(fn: Handler):
            async def endpoint(request: Request):
                principal = None
                if auth:
                    header = request.headers.get("authorization", "")
                    token = header[7:] if header.lower().startswith("bearer ") else ""  # never accept tokens in URLs (they leak into logs)
                    if not token:
                        return _json({"error": "Authentication required"}, 401)
                    try:
                        principal = core.auth.authenticate(token)
                    except AuthError as e:
                        return _json({"error": str(e)}, 401)
                try:
                    result = await fn(request, principal)
                except HTTPError as e:
                    return _json({"error": e.message}, e.status)
                except (AuthError,) as e:
                    return _json({"error": str(e)}, 403)
                except (KeyError,) as e:
                    return _json({"error": f"Not found: {e}"}, 404)
                except (ValueError, WorkspaceError, InvalidTransition, MemoryRejected, ToolError, NetworkPolicyError,
                        ResearchUnavailable, BudgetExceeded, PermissionError) as e:
                    return _json({"error": redact(str(e))}, 400)
                except Exception:  # noqa: BLE001
                    log.exception("unhandled error on %s %s", method, path)
                    return _json({"error": "Internal error (see server log)"}, 500)
                if isinstance(result, Response):
                    return result
                return _json(result)
            routes.append(Route(path, endpoint, methods=[method]))
            return fn
        return deco

    actor = lambda p: p.username if p else "anonymous"  # noqa: E731

    # ------------------------------------------------------------ public
    @route("GET", "/health", auth=False)
    async def health(request, p):
        return {"ok": True, "version": core.version}

    @route("GET", "/api/setup", auth=False)
    async def setup_status(request, p):
        return {"owner_exists": core.auth.owner_exists()}

    @route("POST", "/api/setup", auth=False)
    async def setup(request, p):
        b = await _body(request, SetupIn)
        uid = core.auth.create_owner(b.username, b.password, b.setup_code)
        return {"user_id": uid}

    @route("POST", "/api/auth/login", auth=False)
    async def login(request, p):
        ip = request.client.host if request.client else "?"
        if not login_limit.allow(ip):
            raise HTTPError(429, "Too many login attempts; wait a minute")
        b = await _body(request, LoginIn)
        try:
            return core.auth.login(b.username, b.password, b.device_name, b.totp)
        except AuthError as e:
            raise HTTPError(401, str(e)) from e

    # -------------------------------------------------------------- auth
    @route("POST", "/api/auth/logout")
    async def logout(request, p):
        core.auth.revoke(p.user_id, p.session_id, actor(p))
        return {"ok": True}

    @route("GET", "/api/auth/sessions")
    async def sessions(request, p):
        return {"current": p.session_id, "sessions": core.auth.sessions(p.user_id)}

    @route("DELETE", "/api/auth/sessions/{sid}")
    async def revoke_session(request, p):
        core.auth.revoke(p.user_id, request.path_params["sid"], actor(p))
        return {"ok": True}

    @route("POST", "/api/auth/revoke-others")
    async def revoke_others(request, p):
        return {"revoked": core.auth.revoke_all(p.user_id, actor(p), except_session=p.session_id)}

    @route("POST", "/api/auth/password")
    async def password(request, p):
        b = await _body(request, PasswordIn)
        core.auth.change_password(p.user_id, b.old_password, b.new_password, actor(p))
        return {"ok": True}

    @route("POST", "/api/auth/totp/begin")
    async def totp_begin(request, p):
        secret = core.auth.begin_totp(p.user_id)
        return {"secret": secret, "otpauth_uri": f"otpauth://totp/SASTRA:{p.username}?secret={secret}&issuer=SASTRA"}

    @route("POST", "/api/auth/totp/confirm")
    async def totp_confirm(request, p):
        b = await _body(request, CodeIn)
        core.auth.confirm_totp(p.user_id, b.code, actor(p))
        return {"ok": True}

    # ------------------------------------------------------------ status
    @route("GET", "/api/status")
    async def status(request, p):
        return core.status()

    # -------------------------------------------------------------- chat
    @route("GET", "/api/conversations")
    async def conversations(request, p):
        return {"conversations": core.chat.list()}

    @route("POST", "/api/conversations")
    async def new_conversation(request, p):
        b = await _body(request, ConversationIn)
        return core.chat.create(b.title, b.project_id, b.privacy)

    @route("GET", "/api/conversations/{cid}/messages")
    async def conv_messages(request, p):
        return {"messages": core.chat.messages(request.path_params["cid"])}

    @route("DELETE", "/api/conversations/{cid}")
    async def del_conversation(request, p):
        core.chat.delete(request.path_params["cid"], actor(p))
        return {"ok": True}

    @route("POST", "/api/chat")
    async def chat(request, p):
        b = await _body(request, ChatIn)
        gen = core.chat.send(b.message, conversation_id=b.conversation_id, project_id=b.project_id, actor=actor(p),
                             route=b.route, client_id=b.client_id)
        if request.query_params.get("stream", "1") == "0":
            events = list(gen)
            text = "".join(e.get("text", "") for e in events if e["event"] == "delta")
            return {"events": [e for e in events if e["event"] != "delta"], "text": text}

        def sse():
            try:
                for evt in gen:
                    yield f"event: {evt['event']}\ndata: {json.dumps(evt, default=str)}\n\n"
            except Exception as e:  # noqa: BLE001
                log.exception("chat stream failed")
                yield f"event: error\ndata: {json.dumps({'error': redact(str(e))})}\n\n"
        return StreamingResponse(sse(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @route("POST", "/api/messages/{mid}/feedback")
    async def feedback(request, p):
        b = await _body(request, FeedbackIn)
        return {"lesson_id": core.learning.feedback(request.path_params["mid"], b.rating, b.note, actor(p))}

    # ------------------------------------------------------------- tasks
    @route("GET", "/api/tasks")
    async def tasks(request, p):
        q = request.query_params
        return {"tasks": core.tasks.list(q.get("state"), q.get("project_id"), int(q.get("limit", 100)))}

    @route("POST", "/api/tasks")
    async def create_task(request, p):
        b = await _body(request, TaskIn)
        kind = b.kind or classify(b.request)
        if kind not in KINDS:
            raise HTTPError(422, f"kind must be one of {KINDS}")
        return core.tasks.create(b.request, kind=kind, mode=int(core.controls.mode), title=b.title, project_id=b.project_id,
                                 limits=b.limits, idempotency_key=b.idempotency_key, actor=actor(p))

    @route("GET", "/api/tasks/{tid}")
    async def get_task(request, p):
        tid = request.path_params["tid"]
        return {"task": core.tasks.get(tid), "events": core.tasks.events(tid),
                "approvals": core.db.query("SELECT * FROM approvals WHERE task_id = ? ORDER BY created_at", (tid,))}

    @route("POST", "/api/tasks/{tid}/cancel")
    async def cancel_task(request, p):
        return core.tasks.cancel(request.path_params["tid"], actor(p))

    @route("POST", "/api/tasks/{tid}/retry")
    async def retry_task(request, p):
        return core.tasks.retry(request.path_params["tid"], actor(p))

    @route("POST", "/api/tasks/{tid}/input")
    async def task_input(request, p):
        b = await _body(request, TaskInputIn)
        tid = request.path_params["tid"]
        t = core.tasks.get(tid)
        if t["state"] != "NEEDS_USER_INPUT":
            raise HTTPError(409, "Task is not waiting for input")
        new_req = t["request"] + "\n\nOwner's answer: " + b.answer
        core.db.update("tasks", tid, {"request": new_req, "kind": classify(new_req) if t["kind"] == "direct" else t["kind"]})
        return core.tasks.transition(tid, "PLANNING", message="Owner answered", next_run_at=0, plan="[]", step_index=0)

    # --------------------------------------------------------- approvals
    @route("GET", "/api/approvals")
    async def approvals(request, p):
        return {"pending": core.approvals.pending(), "recent": core.approvals.list(50), "grants": core.approvals.grants()}

    @route("POST", "/api/approvals/{aid}/decide")
    async def decide(request, p):
        b = await _body(request, DecideIn)
        row = core.approvals.decide(request.path_params["aid"], b.approve, actor(p), b.note, b.grant)
        if row.get("task_id"):
            t = core.tasks.get(row["task_id"])
            if t["state"] == "AWAITING_APPROVAL":
                if b.approve:
                    core.tasks.transition(t["id"], "RUNNING", message="Approved by owner", next_run_at=0)
                else:
                    core.tasks.transition(t["id"], "FAILED", message="Owner rejected the action", error="Rejected by owner")
        return row

    @route("DELETE", "/api/grants/{gid}")
    async def revoke_grant(request, p):
        core.approvals.revoke_grant(request.path_params["gid"], actor(p))
        return {"ok": True}

    # ---------------------------------------------------------- research
    @route("POST", "/api/research")
    async def research(request, p):
        b = await _body(request, ResearchIn)
        req = b.question + ("\nSources to include: " + ", ".join(b.urls) if b.urls else "")
        t = core.tasks.create(req, kind="research", mode=int(core.controls.mode), project_id=b.project_id, actor=actor(p),
                              title="Research: " + b.question[:100])
        plan = [{"id": "research", "type": "tool", "tool": "research_report",
                 "args": {"question": b.question, "urls": b.urls, "freshness_days": b.freshness_days}}]
        core.db.update("tasks", t["id"], {"plan": json.dumps(plan)})
        core.tasks.transition(t["id"], "PLANNING")
        core.tasks.transition(t["id"], "RUNNING", message="Executing research plan", step_index=0)
        return core.tasks.get(t["id"])

    @route("GET", "/api/research")
    async def research_list(request, p):
        return {"reports": core.research.list_reports(request.query_params.get("project_id"))}

    @route("GET", "/api/research/{rid}")
    async def research_get(request, p):
        row = core.db.one("SELECT * FROM research_reports WHERE id = ?", (request.path_params["rid"],))
        if not row:
            raise KeyError("report")
        row["sources"] = json.loads(row["sources"])
        return row

    # ------------------------------------------------------------ memory
    @route("GET", "/api/memory")
    async def memory(request, p):
        q = request.query_params
        if q.get("q"):
            hits = core.memory.search(q["q"], q.get("project_id"), limit=int(q.get("limit", 20)))
            return {"results": [{**h.record, "score": h.score} for h in hits]}
        return {"memories": core.memory.list(q.get("project_id"), q.get("category"), q.get("status")),
                "categories": CATEGORIES, "stats": core.memory.stats()}

    @route("POST", "/api/memory")
    async def memory_add(request, p):
        b = await _body(request, MemoryIn)
        return core.memory.add(b.content, b.category, source="owner", subject=b.subject, project_id=b.project_id,
                               ttl_s=b.ttl_hours * 3600 if b.ttl_hours else None, actor=actor(p))

    @route("PATCH", "/api/memory/{mid}")
    async def memory_patch(request, p):
        b = await _body(request, MemoryPatch)
        return core.memory.update(request.path_params["mid"], actor(p), **b.model_dump(exclude_none=True))

    @route("DELETE", "/api/memory/{mid}")
    async def memory_delete(request, p):
        return {"deleted": core.memory.delete(request.path_params["mid"], actor(p))}

    @route("POST", "/api/memory/{mid}/approve")
    async def memory_approve(request, p):
        return core.memory.approve(request.path_params["mid"], actor(p))

    @route("POST", "/api/memory/{mid}/resolve")
    async def memory_resolve(request, p):
        b = await _body(request, ResolveIn)
        core.memory.resolve_conflict(request.path_params["mid"], b.keep, actor(p))
        return {"ok": True}

    @route("GET", "/api/memory/export")
    async def memory_export(request, p):
        data = core.memory.export(request.query_params.get("project_id"))
        core.audit.record(actor(p), "memory.export", None, count=data["count"])
        return Response(json.dumps(data, default=str, ensure_ascii=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": "attachment; filename=sastra-memory.json"})

    @route("POST", "/api/memory/wipe")
    async def memory_wipe(request, p):
        b = await _body(request, WipeIn)
        return {"deleted": core.memory.wipe_all(actor(p), b.confirm)}

    @route("PUT", "/api/memory/retention")
    async def retention(request, p):
        b = await _body(request, RetentionIn)
        core.kv.set("conversation_retention_days", b.conversation_retention_days)
        core.audit.record(actor(p), "privacy.retention", None, days=b.conversation_retention_days)
        return {"conversation_retention_days": b.conversation_retention_days}

    # ---------------------------------------------------------- projects
    @route("GET", "/api/projects")
    async def projects(request, p):
        return {"projects": core.projects.list()}

    @route("POST", "/api/projects")
    async def create_project(request, p):
        b = await _body(request, ProjectIn)
        return core.projects.create(b.name, b.description, b.kind, actor(p))

    @route("GET", "/api/projects/{pid}")
    async def project(request, p):
        pid = request.path_params["pid"]
        return {"project": core.projects.get(pid), "files": core.projects.tree(pid), "log": core.projects.log(pid, 20),
                "artifacts": core.projects.list_artifacts(pid)}

    @route("GET", "/api/projects/{pid}/file")
    async def read_file(request, p):
        pid = request.path_params["pid"]
        return {"path": request.query_params["path"], "content": core.projects.read(pid, request.query_params["path"])}

    @route("PUT", "/api/projects/{pid}/file")
    async def write_file(request, p):
        b = await _body(request, FileIn)
        pid = request.path_params["pid"]
        if b.preview_only:
            return {"diff": core.projects.preview_write(pid, b.path, b.content)}
        core.projects.checkpoint(pid, f"Before owner edit of {b.path}", actor(p))
        return core.projects.write(pid, b.path, b.content, actor(p))

    @route("POST", "/api/projects/{pid}/checkpoint")
    async def checkpoint(request, p):
        b = await _body(request, CheckpointIn)
        return {"commit": core.projects.checkpoint(request.path_params["pid"], b.message, actor(p))}

    @route("POST", "/api/projects/{pid}/rollback")
    async def rollback(request, p):
        b = await _body(request, RollbackIn)
        return {"commit": core.projects.rollback(request.path_params["pid"], b.commit, actor(p))}

    @route("GET", "/api/projects/{pid}/diff")
    async def diff(request, p):
        return {"diff": core.projects.diff(request.path_params["pid"], request.query_params.get("commit"))}

    @route("GET", "/api/artifacts")
    async def artifacts(request, p):
        return {"artifacts": core.projects.list_artifacts(request.query_params.get("project_id"))}

    @route("GET", "/api/artifacts/{aid}/download")
    async def artifact_download(request, p):
        row, data = core.projects.load_artifact(request.path_params["aid"])
        core.audit.record(actor(p), "artifact.download", row["id"])
        safe = "".join(c for c in row["name"] if c.isalnum() or c in "._-") or "artifact"
        return Response(data, media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{safe}"', "X-SHA256": row["sha256"]})

    # ------------------------------------------------------------- tools
    @route("GET", "/api/tools")
    async def tools(request, p):
        disabled = set(core.controls.get("disabled_tools") or [])
        return {"tools": [{**t.declaration(), "enabled": t.name not in disabled} for t in core.registry.tools.values()]}

    @route("POST", "/api/tools/run")
    async def run_tool(request, p):
        """Owner-initiated direct tool call (role 'operator'); approvals still apply."""
        from ..tools.registry import ApprovalRequired, CallContext
        b = await _body(request, ToolRunIn)
        ctx = CallContext(actor=actor(p), role="operator", project_id=b.project_id, services=core)
        try:
            return {"output": core.gateway_actions.invoke(b.tool, b.args, ctx)}
        except ApprovalRequired as e:
            return _json({"approval_required": e.approval_id, "message": str(e)}, 202)

    # ----------------------------------------------------------- controls
    @route("GET", "/api/controls")
    async def controls(request, p):
        return core.controls.snapshot()

    @route("PUT", "/api/controls")
    async def set_controls(request, p):
        b = await _body(request, ControlsIn)
        for k, v in b.model_dump(exclude_none=True).items():
            if k == "disabled_tools":
                unknown = [t for t in v if t not in core.registry.tools]
                if unknown:
                    raise HTTPError(422, f"Unknown tools: {unknown}")
            core.controls.set(k, v, actor(p))
        return core.controls.snapshot()

    @route("POST", "/api/controls/panic")
    async def panic(request, p):
        core.controls.panic(actor(p))
        return core.controls.snapshot()

    @route("POST", "/api/controls/resume")
    async def resume(request, p):
        core.controls.resume(actor(p))
        return core.controls.snapshot()

    # ------------------------------------------------------------- models
    @route("GET", "/api/models")
    async def models(request, p):
        return {"providers": core.gateway.health(), "preference": core.gateway.preference,
                "consent": core.controls.get("provider_consent")}

    @route("PUT", "/api/models/preference")
    async def model_pref(request, p):
        b = await _body(request, PreferenceIn)
        core.gateway.set_preference(b.order)
        core.audit.record(actor(p), "models.preference", None, order=b.order)
        return {"preference": core.gateway.preference}

    @route("PUT", "/api/models/consent")
    async def model_consent(request, p):
        b = await _body(request, ConsentIn)
        core.controls.set("provider_consent", {**(core.controls.get("provider_consent") or {}), **b.provider_consent}, actor(p))
        return {"consent": core.controls.get("provider_consent")}

    # ------------------------------------------------------------- budget
    @route("GET", "/api/budget")
    async def budget(request, p):
        return {**core.budget.status(), "pricing": core.budget.pricing, "request_pricing": core.budget.request_pricing}

    @route("PUT", "/api/budget")
    async def set_budget(request, p):
        b = await _body(request, BudgetIn)
        core.budget.set_limits(b.daily_usd, b.monthly_usd)
        core.audit.record(actor(p), "budget.limits", None, daily=b.daily_usd, monthly=b.monthly_usd)
        return core.budget.status()

    @route("PUT", "/api/budget/pricing")
    async def set_price(request, p):
        b = await _body(request, PriceIn)
        core.budget.set_price(b.provider_model, b.input_per_m, b.output_per_m)
        core.audit.record(actor(p), "budget.price", b.provider_model, inp=b.input_per_m, out=b.output_per_m)
        return {"pricing": core.budget.pricing}

    @route("GET", "/api/budget/report")
    async def budget_report(request, p):
        return core.budget.report(int(request.query_params.get("days", 30)))

    # ------------------------------------------------- privacy & security
    @route("GET", "/api/privacy")
    async def privacy(request, p):
        return {**core.privacy.dashboard(int(request.query_params.get("days", 30))),
                "consent": core.controls.get("provider_consent"),
                "retention_days": core.kv.get("conversation_retention_days", core.settings.conversation_retention_days),
                "encryption_at_rest": core.cipher.enabled}

    @route("GET", "/api/security/events")
    async def sec_events(request, p):
        return {"events": core.events.list(request.query_params.get("unacked") == "1")}

    @route("POST", "/api/security/events/{eid}/ack")
    async def sec_ack(request, p):
        core.events.acknowledge(request.path_params["eid"])
        return {"ok": True}

    @route("GET", "/api/audit")
    async def audit(request, p):
        q = request.query_params
        return {"entries": core.audit.recent(int(q.get("limit", 200)), q.get("prefix"))}

    @route("GET", "/api/audit/verify")
    async def audit_verify(request, p):
        ok, bad = core.audit.verify()
        return {"intact": ok, "first_bad_seq": bad}

    # ----------------------------------------------------------- schedules
    @route("GET", "/api/schedules")
    async def schedules(request, p):
        return {"schedules": core.scheduler.list(), "active": int(core.controls.mode) >= 3}

    @route("POST", "/api/schedules")
    async def create_schedule(request, p):
        b = await _body(request, ScheduleIn)
        sid = core.scheduler.create(b.name, b.request, b.kind, b.project_id, b.interval_minutes, b.daily_at, b.budget_usd, actor(p))
        return {"id": sid}

    @route("DELETE", "/api/schedules/{sid}")
    async def delete_schedule(request, p):
        core.scheduler.delete(request.path_params["sid"], actor(p))
        return {"ok": True}

    # ------------------------------------------------------------ learning
    @route("GET", "/api/learning")
    async def learning(request, p):
        return core.learning.list()

    @route("POST", "/api/learning/{lid}/approve")
    async def learning_approve(request, p):
        return {"version": core.learning.apply(request.path_params["lid"], actor(p), by_owner=True)}

    @route("POST", "/api/learning/{lid}/reject")
    async def learning_reject(request, p):
        core.learning.reject(request.path_params["lid"], actor(p))
        return {"ok": True}

    @route("POST", "/api/learning/rollback/{target}")
    async def learning_rollback(request, p):
        return {"version": core.learning.rollback(request.path_params["target"], actor(p))}

    # ------------------------------------------------------------ ventures
    @route("GET", "/api/ventures")
    async def ventures(request, p):
        return {"ventures": core.ventures.list()}

    @route("POST", "/api/ventures")
    async def create_venture(request, p):
        b = await _body(request, VentureIn)
        return core.ventures.create(b.name, b.hypothesis, b.budget_usd, b.project_id, actor(p))

    @route("GET", "/api/ventures/{vid}")
    async def venture(request, p):
        return core.ventures.summary(request.path_params["vid"])

    @route("POST", "/api/ventures/{vid}/entries")
    async def venture_entry(request, p):
        b = await _body(request, EntryIn)
        return {"id": core.ventures.add_entry(request.path_params["vid"], b.kind, b.amount, b.currency, b.basis, b.note,
                                              b.evidence, actor(p))}

    @route("PUT", "/api/ventures/{vid}/status")
    async def venture_status(request, p):
        b = await _body(request, StatusIn)
        core.ventures.set_status(request.path_params["vid"], b.status, actor(p))
        return {"ok": True}

    # ------------------------------------------------- metrics & notices
    @route("GET", "/api/metrics")
    async def metrics(request, p):
        return core.metrics.summary(int(request.query_params.get("days", 7)))

    @route("GET", "/api/notifications")
    async def notifications(request, p):
        q = request.query_params
        return {"notifications": core.notifications.list(float(q.get("since", 0)), q.get("unread") == "1")}

    @route("POST", "/api/notifications/{nid}/read")
    async def notif_read(request, p):
        core.notifications.mark_read(request.path_params["nid"])
        return {"ok": True}

    # -------------------------------------------------------------- backup
    @route("POST", "/api/backup")
    async def backup(request, p):
        out = create_backup(core)
        return {k: v for k, v in out.items() if k != "file"}

    @route("GET", "/api/backups")
    async def backups(request, p):
        return {"backups": list_backups(core)}

    # ---------------------------------------------------------------- sync
    @route("POST", "/api/sync")
    async def sync(request, p):
        b = await _body(request, SyncIn)
        out = {"messages": [], "tasks": [], "memories": [], "errors": []}
        for m in b.messages:  # offline user messages: answer them now (non-streaming)
            try:
                events = list(core.chat.send(str(m["text"]), conversation_id=m.get("conversation_id"),
                                             project_id=m.get("project_id"), actor=actor(p), client_id=m.get("client_id")))
                text = "".join(e.get("text", "") for e in events if e["event"] == "delta")
                meta = next(e for e in events if e["event"] == "meta")
                out["messages"].append({"client_id": m.get("client_id"), "conversation_id": meta["conversation_id"], "reply": text})
            except Exception as e:  # noqa: BLE001
                out["errors"].append({"client_id": m.get("client_id"), "error": redact(str(e))})
        for t in b.tasks:
            try:
                req = str(t["request"])
                task = core.tasks.create(req, kind=t.get("kind") or classify(req), mode=int(core.controls.mode),
                                         project_id=t.get("project_id"), idempotency_key="sync:" + str(t.get("client_id")),
                                         actor=actor(p))
                out["tasks"].append({"client_id": t.get("client_id"), "task_id": task["id"]})
            except Exception as e:  # noqa: BLE001
                out["errors"].append({"client_id": t.get("client_id"), "error": redact(str(e))})
        for m in b.memories:
            try:
                rec = core.memory.add(str(m["content"]), m.get("category", "preference"), source="owner",
                                      subject=m.get("subject", ""), project_id=m.get("project_id"), actor=actor(p))
                out["memories"].append({"client_id": m.get("client_id"), "id": rec["id"], "status": rec["status"]})
            except Exception as e:  # noqa: BLE001
                out["errors"].append({"client_id": m.get("client_id"), "error": redact(str(e))})
        return out

    # ------------------------------------------------------------ web console
    async def console(request: Request):
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    routes.append(Route("/", console, methods=["GET"]))

    class Guard(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            ip = request.client.host if request.client else "?"
            if not general_limit.allow(ip):
                return _json({"error": "Rate limit exceeded"}, 429)
            cl = request.headers.get("content-length")
            if cl and cl.isdigit() and int(cl) > MAX_BODY:
                return _json({"error": "Request too large"}, 413)
            resp = await call_next(request)
            resp.headers.setdefault("X-Content-Type-Options", "nosniff")
            resp.headers.setdefault("X-Frame-Options", "DENY")
            resp.headers.setdefault("Referrer-Policy", "no-referrer")
            resp.headers.setdefault("Permissions-Policy", "camera=(), geolocation=(), microphone=()")
            if request.url.path == "/":
                resp.headers.setdefault("Content-Security-Policy",
                                        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                                        "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
            else:
                resp.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
                resp.headers.setdefault("Cache-Control", "no-store")
            return resp

    middleware = [Middleware(Guard)]
    if core.settings.cors_origins:
        middleware.append(Middleware(CORSMiddleware, allow_origins=core.settings.cors_origins,
                                     allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["authorization", "content-type"]))
    star = Starlette(routes=routes, middleware=middleware)
    star.state.core = core
    return star
