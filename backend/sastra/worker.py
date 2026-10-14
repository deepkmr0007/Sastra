"""Durable background worker.

The queue is the ``tasks`` table itself: workers claim a task by taking a
time-limited lease. If a worker dies, the lease expires and another worker
resumes the task from its persisted step — no Redis required for a
single-owner deployment (ADR-004).
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time

from .db import now

log = logging.getLogger("sastra.worker")


class Worker:
    def __init__(self, app, poll_s: float = 1.0):
        self.app = app
        self.poll_s = poll_s
        self.id = f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident() % 10000}"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_maintenance = 0.0

    def run_once(self) -> bool:
        """Process at most one task step-run. Returns True if work was done."""
        controls = self.app.controls
        for row in self.app.tasks.pending_cancels():
            try:
                self.app.tasks.transition(row["id"], "CANCELLED", message="Cancelled")
            except Exception:  # noqa: BLE001
                pass
        if not controls.get("workers_enabled") or controls.get("agents_paused"):
            return False
        task = self.app.tasks.claim(self.id)
        if not task:
            return False
        try:
            self.app.orchestrator.run(task["id"], self.id)
        except Exception as e:  # noqa: BLE001 — never let one task kill the worker
            log.exception("task %s crashed", task["id"])
            try:
                self.app.tasks.schedule_retry(task["id"], f"internal error: {e}")
            finally:
                self.app.tasks.release(task["id"], self.id)
        return True

    def maintenance(self) -> dict:
        out = {"scheduled": self.app.scheduler.tick(),
               "expired_memory": self.app.memory.purge_expired(),
               "expired_messages": self.app.chat.apply_retention(int(self.app.kv.get("conversation_retention_days",
                                                                                     self.app.settings.conversation_retention_days)))}
        self.app.approvals.pending()  # expires stale approvals
        self._last_maintenance = now()
        return out

    def loop(self) -> None:
        log.info("worker %s started", self.id)
        while not self._stop.is_set():
            try:
                if now() - self._last_maintenance > 60:
                    self.maintenance()
                did = self.run_once()
            except Exception:  # noqa: BLE001
                log.exception("worker loop error")
                did = False
            if not did:
                self._stop.wait(self.poll_s)

    def start(self) -> None:
        self._thread = threading.Thread(target=self.loop, name="sastra-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def drain(self, max_iterations: int = 200) -> int:
        """Run until the queue is idle (used by tests and the CLI)."""
        n = 0
        while n < max_iterations and self.run_once():
            n += 1
        return n
