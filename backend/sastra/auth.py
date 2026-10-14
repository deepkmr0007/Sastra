"""Single-owner authentication: password (+ optional TOTP), revocable device sessions,
brute-force lockout, one-time setup code for first-run owner creation."""
from __future__ import annotations

import secrets
from dataclasses import dataclass

from .audit import AuditLog, SecurityEvents
from .db import KV, Database, new_id, now
from .security import crypto

MAX_FAILED = 5
LOCK_SECONDS = 15 * 60


class AuthError(Exception):
    pass


@dataclass
class Principal:
    user_id: str
    session_id: str
    username: str


class AuthService:
    def __init__(self, db: Database, kv: KV, audit: AuditLog, events: SecurityEvents,
                 jwt_secret: str, session_ttl_hours: int, cipher: crypto.DataCipher):
        self.db, self.kv, self.audit, self.events = db, kv, audit, events
        self.jwt_secret = jwt_secret
        self.ttl_s = session_ttl_hours * 3600
        self.cipher = cipher

    # ------------------------------------------------------------ first run
    def owner_exists(self) -> bool:
        return bool(self.db.scalar("SELECT COUNT(*) FROM users"))

    def setup_code(self) -> str | None:
        """One-time code printed to the server console; required to create the owner
        so a stranger who reaches a fresh server first cannot claim it."""
        if self.owner_exists():
            return None
        code = self.kv.get("setup_code")
        if not code:
            code = secrets.token_hex(4).upper()
            self.kv.set("setup_code", code)
        return code

    def create_owner(self, username: str, password: str, setup_code: str) -> str:
        if self.owner_exists():
            raise AuthError("Owner already exists")
        expected = self.kv.get("setup_code")
        if not expected or not secrets.compare_digest(expected, setup_code.strip().upper()):
            self.events.raise_event("setup_code_rejected", "high", "Owner creation attempted with wrong setup code")
            raise AuthError("Invalid setup code")
        uid = new_id()
        self.db.insert("users", {"id": uid, "username": username.strip(), "password_hash": crypto.hash_password(password),
                                 "totp_secret_enc": None, "failed_logins": 0, "locked_until": None, "created_at": now()})
        self.kv.set("setup_code", None)
        self.audit.record(username, "auth.owner_created", uid)
        return uid

    # ---------------------------------------------------------------- login
    def login(self, username: str, password: str, device_name: str, totp: str | None = None) -> dict:
        user = self.db.one("SELECT * FROM users WHERE username = ?", (username.strip(),))
        if not user:
            crypto.verify_password(password, "scrypt$AAAAAAAAAAAAAAAAAAAAAA==$AAAA")  # equalise timing
            raise AuthError("Invalid credentials")
        if user["locked_until"] and user["locked_until"] > now():
            raise AuthError("Account temporarily locked after repeated failures")
        ok = crypto.verify_password(password, user["password_hash"])
        if ok and user["totp_secret_enc"]:
            secret = self._totp_secret(user)
            ok = bool(totp) and crypto.verify_totp(secret, totp or "")
        if not ok:
            failed = user["failed_logins"] + 1
            fields = {"failed_logins": failed}
            if failed >= MAX_FAILED:
                fields.update(locked_until=now() + LOCK_SECONDS, failed_logins=0)
                self.events.raise_event("account_locked", "high", f"{MAX_FAILED} failed logins for {username}")
            self.db.update("users", user["id"], fields)
            self.audit.record(username, "auth.login_failed", user["id"], device=device_name)
            raise AuthError("Invalid credentials")
        self.db.update("users", user["id"], {"failed_logins": 0, "locked_until": None})
        sid = new_id()
        self.db.insert("sessions", {"id": sid, "user_id": user["id"], "device_name": device_name[:80],
                                    "created_at": now(), "expires_at": now() + self.ttl_s, "last_seen_at": now(), "revoked_at": None})
        self.audit.record(user["username"], "auth.login", sid, device=device_name)
        return {"token": crypto.issue_token(self.jwt_secret, sid, user["id"], self.ttl_s), "session_id": sid,
                "expires_in": self.ttl_s}

    def authenticate(self, token: str) -> Principal:
        claims = crypto.decode_token(self.jwt_secret, token)
        if not claims:
            raise AuthError("Invalid or expired token")
        row = self.db.one("SELECT s.*, u.username FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.id = ?", (claims["sid"],))
        if not row or row["revoked_at"] or row["expires_at"] < now():
            raise AuthError("Session revoked or expired")
        if (now() - (row["last_seen_at"] or 0)) > 60:
            self.db.update("sessions", row["id"], {"last_seen_at": now()})
        return Principal(user_id=row["user_id"], session_id=row["id"], username=row["username"])

    def sessions(self, user_id: str) -> list[dict]:
        return self.db.query("SELECT id, device_name, created_at, expires_at, last_seen_at, revoked_at FROM sessions "
                             "WHERE user_id = ? ORDER BY created_at DESC", (user_id,))

    def revoke(self, user_id: str, session_id: str, actor: str) -> None:
        self.db.execute("UPDATE sessions SET revoked_at = ? WHERE id = ? AND user_id = ?", (now(), session_id, user_id))
        self.audit.record(actor, "auth.session_revoked", session_id)

    def revoke_all(self, user_id: str, actor: str, except_session: str | None = None) -> int:
        n = self.db.execute("UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL AND id != ?",
                            (now(), user_id, except_session or ""))
        self.audit.record(actor, "auth.all_sessions_revoked", user_id, count=n)
        return n

    def change_password(self, user_id: str, old: str, new: str, actor: str) -> None:
        user = self.db.one("SELECT * FROM users WHERE id = ?", (user_id,))
        if not user or not crypto.verify_password(old, user["password_hash"]):
            raise AuthError("Current password is wrong")
        self.db.update("users", user_id, {"password_hash": crypto.hash_password(new)})
        self.audit.record(actor, "auth.password_changed", user_id)

    # ----------------------------------------------------------------- TOTP
    def _totp_secret(self, user: dict) -> str:
        enc = user["totp_secret_enc"]
        return self.cipher.decrypt_str(enc) if enc.startswith(crypto.DataCipher.PREFIX) else enc

    def begin_totp(self, user_id: str) -> str:
        secret = crypto.new_totp_secret()
        self.kv.set(f"pending_totp:{user_id}", secret)
        return secret

    def confirm_totp(self, user_id: str, code: str, actor: str) -> None:
        secret = self.kv.get(f"pending_totp:{user_id}")
        if not secret or not crypto.verify_totp(secret, code):
            raise AuthError("Code did not match")
        if not self.cipher.enabled:
            raise AuthError("Set SASTRA_DATA_KEY first: TOTP secrets are only stored encrypted")
        stored = self.cipher.encrypt_str(secret)
        self.db.update("users", user_id, {"totp_secret_enc": stored})
        self.kv.set(f"pending_totp:{user_id}", None)
        self.audit.record(actor, "auth.totp_enabled", user_id)
