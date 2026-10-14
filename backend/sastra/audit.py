"""Tamper-evident audit log.

Each record stores the SHA-256 of (previous hash + canonical record). Editing or
deleting any past row breaks the chain, which ``verify`` detects. Details are
redacted before they are written.
"""
from __future__ import annotations

import hashlib
import threading
from typing import Any

from .db import Database, dumps, new_id, now
from .security.redact import redact

GENESIS = "0" * 64


class AuditLog:
    def __init__(self, db: Database):
        self.db = db
        self._lock = threading.Lock()

    @staticmethod
    def _hash(prev: str, seq: int, ts: float, actor: str, action: str, target: str | None, detail: str) -> str:
        canonical = dumps([prev, seq, round(ts, 6), actor, action, target, detail])
        return hashlib.sha256(canonical.encode()).hexdigest()

    def record(self, actor: str, action: str, target: str | None = None, **detail: Any) -> int:
        detail_s = redact(dumps(detail))
        with self._lock, self.db.transaction():
            last = self.db.one("SELECT seq, hash FROM audit_log ORDER BY seq DESC LIMIT 1")
            seq = (last["seq"] + 1) if last else 1
            prev = last["hash"] if last else GENESIS
            ts = round(now(), 6)
            h = self._hash(prev, seq, ts, actor, action, target, detail_s)
            self.db.insert("audit_log", {"seq": seq, "ts": ts, "actor": actor, "action": action,
                                         "target": target, "detail": detail_s, "prev_hash": prev, "hash": h})
        return seq

    def verify(self) -> tuple[bool, int | None]:
        """Return (ok, first_bad_seq)."""
        prev = GENESIS
        expected_seq = 1
        for row in self.db.query("SELECT * FROM audit_log ORDER BY seq"):
            if row["seq"] != expected_seq or row["prev_hash"] != prev:
                return False, row["seq"]
            h = self._hash(prev, row["seq"], row["ts"], row["actor"], row["action"], row["target"], row["detail"])
            if h != row["hash"]:
                return False, row["seq"]
            prev = h
            expected_seq += 1
        return True, None

    def recent(self, limit: int = 100, action_prefix: str | None = None) -> list[dict]:
        if action_prefix:
            return self.db.query("SELECT * FROM audit_log WHERE action LIKE ? ORDER BY seq DESC LIMIT ?", (action_prefix + "%", limit))
        return self.db.query("SELECT * FROM audit_log ORDER BY seq DESC LIMIT ?", (limit,))


class SecurityEvents:
    def __init__(self, db: Database, audit: AuditLog):
        self.db = db
        self.audit = audit

    def raise_event(self, kind: str, severity: str, detail: str) -> str:
        eid = new_id()
        self.db.insert("security_events", {"id": eid, "ts": now(), "kind": kind, "severity": severity,
                                           "detail": redact(detail), "acknowledged": 0})
        self.audit.record("system", "security." + kind, eid, severity=severity, detail=detail)
        return eid

    def list(self, unacked_only: bool = False) -> list[dict]:
        q = "SELECT * FROM security_events" + (" WHERE acknowledged = 0" if unacked_only else "") + " ORDER BY ts DESC LIMIT 200"
        return self.db.query(q)

    def acknowledge(self, eid: str) -> None:
        self.db.update("security_events", eid, {"acknowledged": 1})
