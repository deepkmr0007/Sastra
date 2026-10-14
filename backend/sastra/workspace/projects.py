"""Project workspaces: isolated directories with Git history, checkpoints,
rollback, patch previews, secret scanning and versioned artifacts."""
from __future__ import annotations

import difflib
import hashlib
import mimetypes
import os
import re
import shutil
import subprocess
from pathlib import Path

from ..audit import AuditLog
from ..db import Database, new_id, now
from ..security.crypto import DataCipher
from ..security.redact import _SECRET_PATTERNS

MAX_FILE_BYTES = 5_000_000
PROJECT_KINDS = ("general", "software", "research", "venture", "android")
_GIT_ENV = {"GIT_AUTHOR_NAME": "SASTRA", "GIT_AUTHOR_EMAIL": "sastra@localhost", "GIT_COMMITTER_NAME": "SASTRA",
            "GIT_COMMITTER_EMAIL": "sastra@localhost", "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"}


class WorkspaceError(Exception):
    pass


class Projects:
    def __init__(self, db: Database, audit: AuditLog, root: Path, artifacts_root: Path, cipher: DataCipher):
        self.db, self.audit, self.root, self.artifacts_root, self.cipher = db, audit, root, artifacts_root, cipher
        root.mkdir(parents=True, exist_ok=True)
        artifacts_root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ projects
    def create(self, name: str, description: str = "", kind: str = "general", actor: str = "owner") -> dict:
        if kind not in PROJECT_KINDS:
            raise ValueError(f"kind must be one of {PROJECT_KINDS}")
        pid = new_id()
        self.db.insert("projects", {"id": pid, "name": name.strip()[:120], "description": description[:2000], "kind": kind,
                                    "created_at": now(), "archived": 0})
        d = self.dir(pid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "README.md").write_text(f"# {name}\n\n{description}\n")
        (d / ".gitignore").write_text(".env\n*.key\n__pycache__/\nnode_modules/\nbuild/\n")
        self._git(pid, "init", "-q")
        self.checkpoint(pid, "Project created", actor=actor)
        self.audit.record(actor, "project.create", pid, name=name, kind=kind)
        return self.get(pid)

    def get(self, pid: str) -> dict:
        row = self.db.one("SELECT * FROM projects WHERE id = ?", (pid,))
        if not row:
            raise KeyError(pid)
        return row

    def list(self, include_archived: bool = False) -> list[dict]:
        q = "SELECT * FROM projects" + ("" if include_archived else " WHERE archived = 0") + " ORDER BY created_at DESC"
        return self.db.query(q)

    def archive(self, pid: str, actor: str) -> None:
        self.db.update("projects", pid, {"archived": 1})
        self.audit.record(actor, "project.archive", pid)

    def delete(self, pid: str, actor: str) -> None:
        """Irreversible: removes files, artifacts and project-scoped memory."""
        self.get(pid)
        shutil.rmtree(self.dir(pid), ignore_errors=True)
        for a in self.db.query("SELECT path FROM artifacts WHERE project_id = ?", (pid,)):
            Path(a["path"]).unlink(missing_ok=True)
        for table in ("artifacts", "memories", "research_reports"):
            self.db.execute(f"DELETE FROM {table} WHERE project_id = ?", (pid,))
        self.db.execute("DELETE FROM projects WHERE id = ?", (pid,))
        self.audit.record(actor, "project.delete", pid)

    def dir(self, pid: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", pid):
            raise WorkspaceError("Bad project id")
        return self.root / pid

    # --------------------------------------------------------------- files
    def resolve(self, pid: str, rel: str) -> Path:
        base = self.dir(pid).resolve()
        rel = rel.replace("\\", "/")
        if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
            raise WorkspaceError("Absolute paths are not allowed")
        if not rel or rel.startswith(".git/") or rel == ".git" or "\x00" in rel:
            raise WorkspaceError("Path not allowed")
        p = (base / rel).resolve()
        if base != p and base not in p.parents:
            raise WorkspaceError("Path escapes the project workspace")
        return p

    def tree(self, pid: str, max_entries: int = 2000) -> list[dict]:
        base = self.dir(pid)
        out = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "__pycache__")]
            for f in filenames:
                p = Path(dirpath) / f
                if p.is_symlink():
                    continue
                out.append({"path": str(p.relative_to(base)), "size": p.stat().st_size})
                if len(out) >= max_entries:
                    return out
        return sorted(out, key=lambda x: x["path"])

    def read(self, pid: str, rel: str) -> str:
        p = self.resolve(pid, rel)
        if not p.is_file() or p.is_symlink():
            raise WorkspaceError("File not found")
        if p.stat().st_size > MAX_FILE_BYTES:
            raise WorkspaceError("File too large to read in full")
        return p.read_text(encoding="utf-8", errors="replace")

    def preview_write(self, pid: str, rel: str, content: str) -> str:
        p = self.resolve(pid, rel)
        old = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
        return "".join(difflib.unified_diff(old.splitlines(True), content.splitlines(True), f"a/{rel}", f"b/{rel}"))

    def write(self, pid: str, rel: str, content: str, actor: str) -> dict:
        if len(content.encode()) > MAX_FILE_BYTES:
            raise WorkspaceError("File too large")
        diff = self.preview_write(pid, rel, content)
        p = self.resolve(pid, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        self.audit.record(actor, "project.write", pid, path=rel, bytes=len(content))
        return {"path": rel, "diff": diff}

    def remove(self, pid: str, rel: str, actor: str) -> None:
        p = self.resolve(pid, rel)
        if p.is_file():
            p.unlink()
        self.audit.record(actor, "project.remove_file", pid, path=rel)

    # ----------------------------------------------------------------- git
    def _git(self, pid: str, *args: str) -> str:
        if not shutil.which("git"):
            raise WorkspaceError("git is not installed")
        r = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "protocol.allow=never", *args],  # noqa: S603,S607
                           cwd=self.dir(pid), capture_output=True, text=True, timeout=60,
                           env={**_GIT_ENV, "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.dir(pid))})
        if r.returncode != 0:
            raise WorkspaceError(f"git {args[0]} failed: {r.stderr.strip()[:500]}")
        return r.stdout

    def checkpoint(self, pid: str, message: str, actor: str = "owner") -> str | None:
        self._git(pid, "add", "-A")
        status = self._git(pid, "status", "--porcelain")
        if not status.strip():
            return None
        self._git(pid, "commit", "-q", "-m", message[:200])
        sha = self._git(pid, "rev-parse", "HEAD").strip()
        self.audit.record(actor, "project.checkpoint", pid, commit=sha, message=message)
        return sha

    def log(self, pid: str, limit: int = 50) -> list[dict]:
        out = self._git(pid, "log", f"-{limit}", "--pretty=format:%H%x1f%ct%x1f%s")
        return [dict(zip(("commit", "ts", "message"), ln.split("\x1f"), strict=False)) for ln in out.splitlines() if ln]

    def diff(self, pid: str, commit: str | None = None) -> str:
        if commit and not re.fullmatch(r"[0-9a-f]{7,40}", commit):
            raise WorkspaceError("Bad commit id")
        return self._git(pid, "diff", commit) if commit else self._git(pid, "diff", "HEAD")

    def rollback(self, pid: str, commit: str, actor: str) -> str:
        """Restore files to a checkpoint. Current state is checkpointed first, so rollback itself is reversible."""
        if not re.fullmatch(r"[0-9a-f]{7,40}", commit):
            raise WorkspaceError("Bad commit id")
        self.checkpoint(pid, "Auto-checkpoint before rollback", actor)
        self._git(pid, "checkout", commit, "--", ".")
        sha = self.checkpoint(pid, f"Rollback to {commit[:10]}", actor)
        self.audit.record(actor, "project.rollback", pid, to=commit)
        return sha or commit

    # ------------------------------------------------------------- scanning
    def scan_secrets(self, pid: str) -> list[dict]:
        findings = []
        for entry in self.tree(pid):
            if entry["size"] > 1_000_000:
                continue
            rel = entry["path"]
            if rel.endswith((".env", ".pem", ".key", ".p12", ".jks", ".keystore")):
                findings.append({"path": rel, "line": 0, "kind": "sensitive_file_type"})
            try:
                text = self.read(pid, rel)
            except WorkspaceError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                for name, pat in _SECRET_PATTERNS:
                    if pat.search(line):
                        findings.append({"path": rel, "line": i, "kind": name})
        return findings

    # ------------------------------------------------------------ artifacts
    def store_artifact(self, pid: str | None, rel_or_bytes, name: str, actor: str, task_id: str | None = None,
                       encrypt: bool = False) -> dict:
        data = rel_or_bytes if isinstance(rel_or_bytes, (bytes, bytearray)) else self.resolve(pid, rel_or_bytes).read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        version = (self.db.scalar("SELECT MAX(version) FROM artifacts WHERE name = ? AND COALESCE(project_id,'') = ?",
                                  (name, pid or "")) or 0) + 1
        aid = new_id()
        blob = self.cipher.encrypt_bytes(bytes(data)) if encrypt else bytes(data)
        path = self.artifacts_root / f"{aid}.bin"
        path.write_bytes(blob)
        os.chmod(path, 0o600)
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        self.db.insert("artifacts", {"id": aid, "project_id": pid, "task_id": task_id, "name": name, "path": str(path),
                                     "sha256": sha, "size": len(data), "version": version, "encrypted": int(encrypt),
                                     "mime": mime, "created_at": now()})
        self.audit.record(actor, "artifact.store", aid, name=name, sha256=sha, version=version)
        return self.db.one("SELECT id, project_id, task_id, name, sha256, size, version, encrypted, mime, created_at FROM artifacts WHERE id = ?", (aid,))

    def load_artifact(self, aid: str) -> tuple[dict, bytes]:
        row = self.db.one("SELECT * FROM artifacts WHERE id = ?", (aid,))
        if not row:
            raise KeyError(aid)
        blob = Path(row["path"]).read_bytes()
        data = self.cipher.decrypt_bytes(blob) if row["encrypted"] else blob
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise WorkspaceError("Artifact integrity check failed")
        return row, data

    def list_artifacts(self, pid: str | None = None) -> list[dict]:
        cols = "id, project_id, task_id, name, sha256, size, version, encrypted, mime, created_at"
        if pid:
            return self.db.query(f"SELECT {cols} FROM artifacts WHERE project_id = ? ORDER BY created_at DESC", (pid,))
        return self.db.query(f"SELECT {cols} FROM artifacts ORDER BY created_at DESC LIMIT 200")
