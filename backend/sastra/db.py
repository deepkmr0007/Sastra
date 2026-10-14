"""Thin database layer.

SQLite (WAL) is the default and the tested path: a single-owner personal system
needs zero-ops storage that also works on a small VPS or inside Termux.
PostgreSQL is supported through the same portable SQL (``?`` placeholders are
rewritten to ``%s``) when ``psycopg`` is installed — see docs/ADR.md (ADR-003).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def new_id() -> str:
    return uuid.uuid4().hex


def now() -> float:
    return time.time()


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


def loads(raw: str | None, default: Any = None) -> Any:
    if raw in (None, ""):
        return default
    return json.loads(raw)


class Database:
    def __init__(self, url: str):
        self.url = url
        self._lock = threading.RLock()
        self._local = threading.local()
        if url.startswith("sqlite:///"):
            self.dialect = "sqlite"
            self.path = url[len("sqlite:///"):]
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            # One shared connection guarded by a lock: simple and correct for a
            # single-owner server; WAL keeps the worker and API from blocking reads.
            self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA secure_delete=ON")
        elif url.startswith(("postgres://", "postgresql://")):
            self.dialect = "postgres"
            try:
                import psycopg  # type: ignore
                from psycopg.rows import dict_row  # type: ignore
            except ImportError as e:  # pragma: no cover - optional path
                raise RuntimeError("PostgreSQL URL given but psycopg is not installed: pip install 'psycopg[binary]'") from e
            self._conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)
        else:
            raise ValueError("Unsupported DATABASE_URL (use sqlite:///path or postgresql://...)")

    # ------------------------------------------------------------------ core
    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.dialect == "postgres" else sql

    def execute(self, sql: str, params: tuple | list = ()) -> int:
        with self._lock:
            cur = self._conn.execute(self._sql(sql), tuple(params))
            return cur.rowcount

    def query(self, sql: str, params: tuple | list = ()) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(self._sql(sql), tuple(params))
            rows = cur.fetchall()
        return [dict(r) for r in rows]

    def one(self, sql: str, params: tuple | list = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: tuple | list = ()) -> Any:
        row = self.one(sql, params)
        return next(iter(row.values())) if row else None

    @contextmanager
    def transaction(self) -> Iterator["Database"]:
        with self._lock:
            depth = getattr(self._local, "depth", 0)
            if depth == 0:
                self._conn.execute("BEGIN IMMEDIATE" if self.dialect == "sqlite" else "BEGIN")
            self._local.depth = depth + 1
            try:
                yield self
            except BaseException:
                self._local.depth = depth
                if depth == 0:
                    self._conn.execute("ROLLBACK")
                raise
            else:
                self._local.depth = depth
                if depth == 0:
                    self._conn.execute("COMMIT")

    def insert(self, table: str, row: dict) -> None:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        self.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(row.values()))

    def update(self, table: str, row_id: str, fields: dict, id_col: str = "id") -> int:
        if not fields:
            return 0
        sets = ", ".join(f"{k} = ?" for k in fields)
        return self.execute(f"UPDATE {table} SET {sets} WHERE {id_col} = ?", [*fields.values(), row_id])

    # ------------------------------------------------------------ migrations
    def migrate(self) -> list[str]:
        self.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at DOUBLE PRECISION NOT NULL)")
        done = {r["name"] for r in self.query("SELECT name FROM schema_migrations")}
        applied = []
        for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if f.name in done:
                continue
            statements = [s.strip() for s in f.read_text().split(";")]
            with self.transaction():
                for stmt in statements:
                    lines = [ln for ln in stmt.splitlines() if not ln.strip().startswith("--")]
                    body = "\n".join(lines).strip()
                    if body:
                        self.execute(body)
                self.insert("schema_migrations", {"name": f.name, "applied_at": now()})
            applied.append(f.name)
        return applied

    def backup_to(self, dest: Path) -> None:
        """Consistent online copy (SQLite only)."""
        if self.dialect != "sqlite":
            raise RuntimeError("Use pg_dump for PostgreSQL backups (see docs/DEPLOYMENT.md)")
        with self._lock:
            target = sqlite3.connect(str(dest))
            try:
                self._conn.backup(target)
            finally:
                target.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class KV:
    """Small typed settings store (autonomy mode, kill switches, consents)."""

    def __init__(self, db: Database):
        self.db = db

    def get(self, key: str, default: Any = None) -> Any:
        row = self.db.one("SELECT value FROM settings_kv WHERE key = ?", (key,))
        return loads(row["value"]) if row else default

    def set(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO settings_kv (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, dumps(value), now()),
        )
