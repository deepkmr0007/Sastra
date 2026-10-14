"""Governed memory.

Categories (separate, inspectable, deletable):
  preference   user-approved preferences and instructions
  project      project specifications, goals, decisions
  summary      conversation summaries and task history
  fact         verified facts and references (with provenance)
  procedure    reusable procedures / successful workflows
  experimental lessons awaiting validation (never used for decisions until approved)
  working      temporary working context (auto-expires)

Governance rules enforced here:
  * secrets and sensitive identifiers are refused, never stored
  * content from untrusted sources that looks like an injection is quarantined
  * exact and near duplicates are merged
  * contradictions on the same subject are flagged for the owner (or superseded
    when the owner states the new value directly)
  * every record carries source, provenance and timestamps
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable

from ..audit import AuditLog
from ..db import Database, dumps, loads, new_id, now
from ..security.redact import find_sensitive
from ..security.untrusted import normalize, scan_injection

CATEGORIES = ("preference", "project", "summary", "fact", "procedure", "experimental", "working")
STATUSES = ("active", "conflict", "quarantined", "pending_validation", "superseded", "rejected")
TRUSTED_SOURCES = ("owner", "owner_confirmed")

_STOP = set("a an the and or but if of to in on at for with by from is are was were be been it this that these those i you he she we they my your our me us as do does did not no yes so than then there here what which who whom how why when where can could should would will shall may might must have has had".split())
_TOKEN = re.compile(r"\w+", re.UNICODE)


class MemoryRejected(Exception):
    pass


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(normalize(text).lower()) if t not in _STOP and len(t) > 1]


def _hash(text: str) -> str:
    return hashlib.sha256(" ".join(normalize(text).lower().split()).encode()).hexdigest()


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


@dataclass
class SearchHit:
    record: dict
    score: float
    keyword: float
    semantic: float


class MemoryStore:
    def __init__(self, db: Database, audit: AuditLog, embedder: Callable[[list[str]], list[list[float]] | None] | None = None,
                 working_ttl_hours: int = 24):
        self.db = db
        self.audit = audit
        self.embedder = embedder
        self.working_ttl_s = working_ttl_hours * 3600

    # ---------------------------------------------------------------- write
    def add(self, content: str, category: str, *, source: str, subject: str = "", project_id: str | None = None,
            provenance: dict | None = None, confidence: float = 1.0, ttl_s: float | None = None,
            actor: str = "system") -> dict:
        if category not in CATEGORIES:
            raise ValueError(f"Unknown category {category}")
        content = normalize(content).strip()
        if not content:
            raise ValueError("Empty memory")
        if len(content) > 8000:
            raise ValueError("Memory too long (8000 chars max); store a summary or an artifact instead")
        sensitive = find_sensitive(content + " " + subject)
        if sensitive:
            self.audit.record(actor, "memory.refused_sensitive", None, kinds=sensitive)
            raise MemoryRejected(f"Refused: looks like sensitive data ({', '.join(sensitive)}). Secrets and ID numbers are never stored.")

        status = "active"
        if source not in TRUSTED_SOURCES:
            inj = scan_injection(content)
            if inj.suspicious:
                status = "quarantined"
        if category == "experimental" and status == "active":
            status = "pending_validation"

        h = _hash(content)
        scope = self._scope_rows(project_id, category)
        for row in scope:
            if row["content_hash"] == h and row["status"] in ("active", "pending_validation", "conflict"):
                self.db.update("memories", row["id"], {"updated_at": now()})
                return self.get(row["id"])
        toks = set(tokenize(content))
        for row in scope:
            if row["status"] == "active" and _jaccard(toks, set(tokenize(row["content"]))) >= 0.85:
                merged_prov = {**loads(row["provenance"], {}), "merged_from": (provenance or {}) | {"source": source}}
                self.db.update("memories", row["id"], {"updated_at": now(), "provenance": dumps(merged_prov),
                                                       "confidence": max(row["confidence"], confidence)})
                return self.get(row["id"])

        contradicts = None
        subj = subject.strip().lower()
        if subj and status == "active":
            for row in scope:
                if row["subject"].lower() == subj and row["status"] == "active":
                    if source in TRUSTED_SOURCES:
                        self.db.update("memories", row["id"], {"status": "superseded", "updated_at": now()})
                    else:
                        status = "conflict"
                    contradicts = row["id"]
                    break

        expires = None
        if category == "working":
            expires = now() + (ttl_s or self.working_ttl_s)
        elif ttl_s:
            expires = now() + ttl_s

        emb = None
        if self.embedder and status == "active":
            try:
                vecs = self.embedder([content])
                emb = dumps(vecs[0]) if vecs else None
            except Exception:  # noqa: BLE001 - embeddings are optional
                emb = None

        mid = new_id()
        t = now()
        self.db.insert("memories", {"id": mid, "category": category, "project_id": project_id, "subject": subject.strip(),
                                    "content": content, "content_hash": h, "source": source,
                                    "provenance": dumps(provenance or {}), "status": status, "confidence": confidence,
                                    "embedding": emb, "contradicts_id": contradicts, "created_at": t, "updated_at": t,
                                    "expires_at": expires})
        self.audit.record(actor, "memory.add", mid, category=category, status=status, source=source)
        return self.get(mid)

    def _scope_rows(self, project_id: str | None, category: str) -> list[dict]:
        if project_id is None:
            return self.db.query("SELECT * FROM memories WHERE project_id IS NULL AND category = ?", (category,))
        return self.db.query("SELECT * FROM memories WHERE project_id = ? AND category = ?", (project_id, category))

    def update(self, mid: str, actor: str, **fields) -> dict:
        allowed = {k: v for k, v in fields.items() if k in ("content", "subject", "category", "confidence", "status", "project_id")}
        if "content" in allowed:
            if find_sensitive(allowed["content"]):
                raise MemoryRejected("Refused: content looks like sensitive data")
            allowed["content_hash"] = _hash(allowed["content"])
            allowed["embedding"] = None
        if "status" in allowed and allowed["status"] not in STATUSES:
            raise ValueError("Bad status")
        allowed["updated_at"] = now()
        self.db.update("memories", mid, allowed)
        self.audit.record(actor, "memory.update", mid, fields=sorted(allowed))
        return self.get(mid)

    def resolve_conflict(self, mid: str, keep: str, actor: str) -> None:
        rec = self.get(mid)
        if not rec or rec["status"] != "conflict":
            raise ValueError("Not a conflict record")
        old = rec["contradicts_id"]
        if keep == "new":
            self.db.update("memories", mid, {"status": "active", "updated_at": now()})
            if old:
                self.db.update("memories", old, {"status": "superseded", "updated_at": now()})
        elif keep == "old":
            self.db.update("memories", mid, {"status": "rejected", "updated_at": now()})
        elif keep == "both":
            self.db.update("memories", mid, {"status": "active", "updated_at": now()})
        else:
            raise ValueError("keep must be new|old|both")
        self.audit.record(actor, "memory.resolve_conflict", mid, keep=keep)

    def approve(self, mid: str, actor: str) -> dict:
        """Owner approves a quarantined or pending_validation record."""
        self.db.update("memories", mid, {"status": "active", "source": "owner_confirmed", "updated_at": now()})
        self.audit.record(actor, "memory.approve", mid)
        return self.get(mid)

    def delete(self, mid: str, actor: str) -> bool:
        n = self.db.execute("DELETE FROM memories WHERE id = ?", (mid,))
        self.audit.record(actor, "memory.delete", mid)
        return n > 0

    def delete_where(self, actor: str, project_id: str | None = None, category: str | None = None) -> int:
        clauses, params = [], []
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if category:
            clauses.append("category = ?")
            params.append(category)
        if not clauses:
            raise ValueError("Refusing to delete all memory without a filter; use wipe_all")
        n = self.db.execute("DELETE FROM memories WHERE " + " AND ".join(clauses), params)
        self.audit.record(actor, "memory.bulk_delete", None, project_id=project_id, category=category, count=n)
        return n

    def wipe_all(self, actor: str, confirm: str) -> int:
        if confirm != "DELETE ALL MEMORY":
            raise ValueError("Confirmation phrase mismatch")
        n = self.db.execute("DELETE FROM memories")
        self.audit.record(actor, "memory.wipe_all", None, count=n)
        return n

    def purge_expired(self) -> int:
        return self.db.execute("DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at < ?", (now(),))

    # ----------------------------------------------------------------- read
    def get(self, mid: str) -> dict | None:
        row = self.db.one("SELECT * FROM memories WHERE id = ?", (mid,))
        return self._public(row) if row else None

    @staticmethod
    def _public(row: dict) -> dict:
        r = dict(row)
        r["provenance"] = loads(r.get("provenance"), {})
        r.pop("embedding", None)
        return r

    def list(self, project_id: str | None = None, category: str | None = None, status: str | None = None,
             include_global: bool = True, limit: int = 200) -> list[dict]:
        clauses, params = ["(expires_at IS NULL OR expires_at > ?)"], [now()]
        if project_id:
            clauses.append("(project_id = ?" + (" OR project_id IS NULL)" if include_global else ")"))
            params.append(project_id)
        if category:
            clauses.append("category = ?")
            params.append(category)
        if status:
            clauses.append("status = ?")
            params.append(status)
        rows = self.db.query("SELECT * FROM memories WHERE " + " AND ".join(clauses) + " ORDER BY updated_at DESC LIMIT ?",
                             [*params, limit])
        return [self._public(r) for r in rows]

    def search(self, query: str, project_id: str | None = None, categories: list[str] | None = None,
               limit: int = 8, min_score: float = 0.05) -> list[SearchHit]:
        """Hybrid retrieval: BM25 keyword + optional embedding cosine + recency + confidence.
        Project isolation: a project sees its own records plus global ones, never another project's."""
        params: list = [now()]
        sql = "SELECT * FROM memories WHERE status = 'active' AND (expires_at IS NULL OR expires_at > ?)"
        if project_id:
            sql += " AND (project_id = ? OR project_id IS NULL)"
            params.append(project_id)
        else:
            sql += " AND project_id IS NULL"
        if categories:
            sql += " AND category IN (" + ",".join("?" for _ in categories) + ")"
            params.extend(categories)
        rows = self.db.query(sql, params)
        if not rows:
            return []
        q_toks = tokenize(query)
        docs = [tokenize(r["subject"] + " " + r["content"]) for r in rows]
        n = len(docs)
        avgdl = sum(len(d) for d in docs) / n or 1.0
        df = Counter(t for d in docs for t in set(d))
        k1, b = 1.5, 0.75
        bm25 = []
        for d in docs:
            tf = Counter(d)
            s = 0.0
            for t in set(q_toks):
                if t not in tf:
                    continue
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / avgdl))
            bm25.append(s)
        top = max(bm25) or 1.0
        q_vec = None
        if self.embedder and any(r["embedding"] for r in rows):
            try:
                vecs = self.embedder([query])
                q_vec = vecs[0] if vecs else None
            except Exception:  # noqa: BLE001
                q_vec = None
        hits = []
        t_now = now()
        for r, kw in zip(rows, bm25, strict=True):
            kw_n = kw / top
            sem = _cosine(q_vec, loads(r["embedding"], [])) if q_vec and r["embedding"] else 0.0
            age_days = (t_now - r["updated_at"]) / 86400
            recency = math.exp(-age_days / 180)
            relevance = (0.6 * kw_n + 0.4 * sem) if q_vec else kw_n
            score = relevance * (0.85 + 0.15 * recency) * (0.5 + 0.5 * r["confidence"])
            if r["category"] == "preference" and relevance > 0:
                score *= 1.1
            if score >= min_score:
                hits.append(SearchHit(self._public(r), round(score, 4), round(kw_n, 4), round(sem, 4)))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]

    def context_block(self, query: str, project_id: str | None, limit: int = 6) -> str:
        hits = self.search(query, project_id, limit=limit)
        if not hits:
            return ""
        lines = [f"- [{h.record['category']}] {h.record['content']} (source: {h.record['source']})" for h in hits]
        return "<relevant_memory>\n" + "\n".join(lines) + "\n</relevant_memory>"

    def export(self, project_id: str | None = None) -> dict:
        rows = self.list(project_id=project_id, include_global=project_id is None, limit=100000)
        return {"exported_at": now(), "count": len(rows), "memories": rows}

    def stats(self) -> dict:
        rows = self.db.query("SELECT category, status, COUNT(*) AS n FROM memories GROUP BY category, status")
        return {"by_category_status": rows}
