"""Password hashing, encryption at rest, session tokens and TOTP.

* Passwords: scrypt (stdlib), per-user salt, constant-time compare.
* Encryption at rest: AES-256-GCM with a key supplied via SASTRA_DATA_KEY
  (never stored in the database or repository).
* Tokens: HS256 JWT bound to a server-side session row, so they can be revoked.
* TOTP (RFC 6238) as a second factor. Passkeys/WebAuthn are on the roadmap.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time

import jwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("Password must be at least 10 characters")
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), **_SCRYPT)
    return hmac.compare_digest(digest, base64.b64decode(digest_b64))


# ------------------------------------------------------------- at-rest crypto
class DataCipher:
    """AES-GCM envelope. Disabled (raises) when no key is configured."""

    PREFIX = "enc1:"

    def __init__(self, key_b64: str | None):
        self._aes = None
        if key_b64:
            key = base64.b64decode(key_b64)
            if len(key) != 32:
                raise ValueError("SASTRA_DATA_KEY must be 32 bytes, base64-encoded")
            self._aes = AESGCM(key)

    @property
    def enabled(self) -> bool:
        return self._aes is not None

    def encrypt_bytes(self, data: bytes, aad: bytes = b"sastra") -> bytes:
        if not self._aes:
            raise RuntimeError("Encryption key not configured (SASTRA_DATA_KEY)")
        nonce = os.urandom(12)
        return nonce + self._aes.encrypt(nonce, data, aad)

    def decrypt_bytes(self, blob: bytes, aad: bytes = b"sastra") -> bytes:
        if not self._aes:
            raise RuntimeError("Encryption key not configured (SASTRA_DATA_KEY)")
        return self._aes.decrypt(blob[:12], blob[12:], aad)

    def encrypt_str(self, text: str) -> str:
        return self.PREFIX + base64.b64encode(self.encrypt_bytes(text.encode())).decode()

    def decrypt_str(self, token: str) -> str:
        if not token.startswith(self.PREFIX):
            raise ValueError("Not an encrypted value")
        return self.decrypt_bytes(base64.b64decode(token[len(self.PREFIX):])).decode()


def generate_data_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


# ------------------------------------------------------------------ tokens
def issue_token(secret: str, session_id: str, user_id: str, ttl_s: int) -> str:
    now = int(time.time())
    return jwt.encode({"sid": session_id, "sub": user_id, "iat": now, "exp": now + ttl_s, "iss": "sastra"}, secret, algorithm="HS256")


def decode_token(secret: str, token: str) -> dict | None:
    try:
        return jwt.decode(token, secret, algorithms=["HS256"], issuer="sastra")
    except jwt.PyJWTError:
        return None


# -------------------------------------------------------------------- TOTP
def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_code(secret_b32: str, at: float | None = None, step: int = 30, digits: int = 6) -> str:
    padded = secret_b32 + "=" * (-len(secret_b32) % 8)
    key = base64.b32decode(padded.upper())
    counter = int((at if at is not None else time.time()) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()  # noqa: S324  # nosec B324 - RFC 6238 mandates HMAC-SHA1
    offset = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def verify_totp(secret_b32: str, code: str, window: int = 1) -> bool:
    now = time.time()
    return any(hmac.compare_digest(totp_code(secret_b32, now + i * 30), code.strip()) for i in range(-window, window + 1))


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()
