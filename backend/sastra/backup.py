"""Encrypted backup and restore (database + workspaces + artifacts).

Backups are AES-256-GCM encrypted with SASTRA_DATA_KEY. Without a key, backup
refuses to run (fail closed) — an unencrypted copy of everything is exactly
what an attacker wants. Keep the key somewhere other than the server.
"""
from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
import time
from pathlib import Path

from .security.crypto import DataCipher

MAGIC = b"SASTRA-BACKUP-1\n"


def create_backup(app, dest_dir: Path | None = None) -> dict:
    cipher: DataCipher = app.cipher
    if not cipher.enabled:
        raise RuntimeError("Set SASTRA_DATA_KEY to enable encrypted backups")
    dest_dir = dest_dir or app.settings.backups_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        app.db.backup_to(tmp_p / "sastra.db")
        manifest = {"created_at": time.time(), "version": 1, "app_version": app.version}
        (tmp_p / "manifest.json").write_text(json.dumps(manifest))
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tar.add(tmp_p / "sastra.db", arcname="sastra.db")
            tar.add(tmp_p / "manifest.json", arcname="manifest.json")
            for name, path in (("workspaces", app.settings.workspaces_dir), ("artifacts", app.settings.artifacts_dir)):
                if path.exists():
                    tar.add(path, arcname=name)
        blob = MAGIC + cipher.encrypt_bytes(buf.getvalue(), aad=MAGIC)
    name = time.strftime("sastra-%Y%m%d-%H%M%S.bak", time.gmtime())
    out = dest_dir / name
    out.write_bytes(blob)
    out.chmod(0o600)
    sha = hashlib.sha256(blob).hexdigest()
    app.audit.record("system", "backup.create", name, sha256=sha, bytes=len(blob))
    return {"file": str(out), "name": name, "sha256": sha, "bytes": len(blob)}


def restore_backup(backup_file: Path, data_dir: Path, key_b64: str) -> dict:
    """Restore into data_dir. Run with the server STOPPED (CLI: python -m sastra restore)."""
    blob = backup_file.read_bytes()
    if not blob.startswith(MAGIC):
        raise ValueError("Not a SASTRA backup")
    raw = DataCipher(key_b64).decrypt_bytes(blob[len(MAGIC):], aad=MAGIC)
    data_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for child in ("sastra.db", "workspaces", "artifacts"):
        p = data_dir / child
        if p.exists():
            p.rename(data_dir / f"{child}.pre-restore-{stamp}")
    for suffix in ("-wal", "-shm"):
        (data_dir / f"sastra.db{suffix}").unlink(missing_ok=True)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        for m in tar.getmembers():
            target = (data_dir / m.name).resolve()
            if data_dir.resolve() not in target.parents and target != data_dir.resolve():
                raise ValueError(f"Unsafe path in backup: {m.name}")
            if m.issym() or m.islnk() or m.isdev():
                raise ValueError(f"Links/devices not allowed in backup: {m.name}")
        tar.extractall(data_dir, filter="data")
    manifest = json.loads((data_dir / "manifest.json").read_text())
    (data_dir / "manifest.json").unlink()
    return {"restored_from": str(backup_file), "manifest": manifest, "previous_data_kept_with_suffix": f".pre-restore-{stamp}"}


def list_backups(app) -> list[dict]:
    d = app.settings.backups_dir
    if not d.exists():
        return []
    return [{"name": p.name, "bytes": p.stat().st_size, "created_at": p.stat().st_mtime}
            for p in sorted(d.glob("*.bak"), reverse=True)]

