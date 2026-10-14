"""Application container: builds and wires every service once."""
from __future__ import annotations

from typing import Callable

from . import __version__
from .audit import AuditLog, SecurityEvents
from .auth import AuthService
from .budget import Budget
from .chat import ChatService
from .config import Settings, load_settings
from .controls import Controls
from .db import KV, Database
from .memory.store import MemoryStore
from .metrics import Metrics
from .models.adapters import Adapter
from .models.gateway import ModelGateway, build_adapters
from .privacy import PrivacyLedger
from .research.engine import ResearchEngine
from .research.search import SearchProvider, build_search_providers
from .sandbox.runner import build_runner
from .security.crypto import DataCipher
from .security.netguard import FetchResult, safe_fetch
from .security.untrusted import Canary
from .services import Learning, Notifications, Scheduler, Ventures
from .tasks.orchestrator import Orchestrator
from .tasks.service import TaskService
from .tools.builtin import register_builtin
from .tools.calculator import calculate
from .tools.registry import ActionGateway, Approvals, ToolRegistry
from .workspace.projects import Projects


class App:
    version = __version__

    def __init__(self, settings: Settings | None = None, *, adapters: list[Adapter] | None = None,
                 search_providers: list[SearchProvider] | None = None, fetcher: Callable[[str], FetchResult] | None = None,
                 sandbox="auto"):
        self.settings = settings or load_settings()
        self.settings.ensure_dirs()
        self.db = Database(self.settings.database_url)
        self.db.migrate()
        self.kv = KV(self.db)
        self.cipher = DataCipher(self.settings.data_key_b64)
        self.audit = AuditLog(self.db)
        self.events = SecurityEvents(self.db, self.audit)
        self.controls = Controls(self.kv, self.audit)
        self.metrics = Metrics(self.db)
        self.privacy = PrivacyLedger(self.db)
        self.budget = Budget(self.db, self.kv, self.settings.daily_budget_usd, self.settings.monthly_budget_usd)
        self.canary = Canary()
        self.auth = AuthService(self.db, self.kv, self.audit, self.events, self.settings.jwt_secret,
                                self.settings.session_ttl_hours, self.cipher)
        self.gateway = ModelGateway(adapters if adapters is not None else build_adapters(self.settings, calculate),
                                    self.controls, self.budget, self.privacy, self.metrics, self.kv)
        self.memory = MemoryStore(self.db, self.audit, embedder=lambda texts: self.gateway.embed(texts),
                                  working_ttl_hours=self.settings.working_context_ttl_hours)
        s = self.settings
        self.research = ResearchEngine(
            self.db, self.gateway,
            search_providers if search_providers is not None else build_search_providers(s),
            fetcher or (lambda url: safe_fetch(url, max_bytes=s.fetch_max_bytes, timeout=s.fetch_timeout_s,
                                               respect_robots=s.respect_robots)),
            self.controls, self.budget, self.privacy)
        self.projects = Projects(self.db, self.audit, s.workspaces_dir, s.artifacts_dir, self.cipher)
        self.sandbox = build_runner(s) if sandbox == "auto" else sandbox
        self.notifications = Notifications(self.db)
        self.tasks = TaskService(self.db, self.audit)
        self.scheduler = Scheduler(self.db, self.audit, self.controls)
        self.scheduler.tasks = self.tasks
        self.learning = Learning(self.db, self.kv, self.audit)
        self.ventures = Ventures(self.db, self.audit)
        self.registry = register_builtin(ToolRegistry())
        self.approvals = Approvals(self.db, self.audit)
        self.gateway_actions = ActionGateway(self.registry, self.controls, self.approvals, self.budget, self.db,
                                             self.audit, self.events, self.canary)
        self.orchestrator = Orchestrator(self)
        self.chat = ChatService(self)

    # ----------------------------------------------------------------- status
    def status(self) -> dict:
        health = self.gateway.health()
        reasoning = self.gateway.has_reasoning_model()
        active = self.db.query("SELECT state, COUNT(*) AS n FROM tasks WHERE state NOT IN ('COMPLETED','FAILED','CANCELLED') GROUP BY state")
        running = {r["state"]: r["n"] for r in active}
        if not reasoning:
            model_state = "offline"
        elif any(h["available"] and h["local"] and h["name"] != "offline" for h in health) and \
                self.gateway.preference and self.gateway.preference[0] == "local":
            model_state = "local_model"
        else:
            model_state = "cloud_model"
        return {
            "version": self.version,
            "model_state": model_state,
            "models": health,
            "internet": bool(self.controls.get("internet_enabled")),
            "autonomy_mode": int(self.controls.mode),
            "autonomy_mode_name": self.controls.mode.name,
            "agents_paused": bool(self.controls.get("agents_paused")),
            "workers_enabled": bool(self.controls.get("workers_enabled")),
            "sandbox": {"backend": getattr(self.sandbox, "backend", None), "isolation": getattr(self.sandbox, "isolation", None)},
            "search_providers": [p.name for p in self.research.providers],
            "tasks": running,
            "executing_code": bool(self.db.scalar("SELECT COUNT(*) FROM tool_calls WHERE tool = 'code_run' AND status = 'running'")),
            "browsing": bool(self.db.scalar("SELECT COUNT(*) FROM tool_calls WHERE tool IN ('fetch_page','web_search','research_report') AND status = 'running'")),
            "pending_approvals": len(self.approvals.pending()),
            "unread_notifications": self.db.scalar("SELECT COUNT(*) FROM notifications WHERE read_at IS NULL"),
            "security_alerts": self.db.scalar("SELECT COUNT(*) FROM security_events WHERE acknowledged = 0"),
            "budget": self.budget.status(),
            "encryption_at_rest": self.cipher.enabled,
        }
