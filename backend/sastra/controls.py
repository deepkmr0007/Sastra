"""Owner controls: autonomy mode, kill switches, provider data-sharing consent.

These are enforced in code by the action gateway, model router and worker —
never by prompting the model.
"""
from __future__ import annotations

from enum import IntEnum

from .audit import AuditLog
from .db import KV


class Mode(IntEnum):
    ADVISORY = 0    # answer, research, draft — no external actions
    ASSISTED = 1    # safe local/reversible operations within approved scopes
    DELEGATED = 2   # bounded multi-step tasks; pauses for consequential actions
    SCHEDULED = 3   # approved recurring/background workflows


DATA_CLASSES = ("public", "normal", "private", "secret")
# 'secret' never leaves the server; 'private' only to providers marked local.

DEFAULTS = {
    "autonomy_mode": int(Mode.ASSISTED),
    "agents_paused": False,
    "internet_enabled": True,
    "workers_enabled": True,
    "cloud_models_enabled": True,
    # provider -> highest data class it may receive
    "provider_consent": {"offline": "secret", "local": "private", "anthropic": "normal", "openai_compat": "normal"},
    "disabled_tools": [],
}


class Controls:
    def __init__(self, kv: KV, audit: AuditLog):
        self.kv = kv
        self.audit = audit

    def get(self, key: str):
        return self.kv.get(key, DEFAULTS.get(key))

    def set(self, key: str, value, actor: str) -> None:
        if key not in DEFAULTS:
            raise KeyError(key)
        if key == "autonomy_mode":
            value = int(Mode(int(value)))
        if key == "provider_consent":
            for v in dict(value).values():
                if v not in DATA_CLASSES and v != "none":
                    raise ValueError(f"Unknown data class {v}")
        old = self.get(key)
        self.kv.set(key, value)
        self.audit.record(actor, "controls.set", key, old=old, new=value)

    def snapshot(self) -> dict:
        return {k: self.get(k) for k in DEFAULTS}

    @property
    def mode(self) -> Mode:
        return Mode(int(self.get("autonomy_mode")))

    def panic(self, actor: str) -> None:
        """One switch: pause agents, stop workers, cut internet and cloud models."""
        for key, val in (("agents_paused", True), ("workers_enabled", False),
                         ("internet_enabled", False), ("cloud_models_enabled", False)):
            self.kv.set(key, val)
        self.audit.record(actor, "controls.panic", None)

    def resume(self, actor: str) -> None:
        for key, val in (("agents_paused", False), ("workers_enabled", True),
                         ("internet_enabled", True), ("cloud_models_enabled", True)):
            self.kv.set(key, val)
        self.audit.record(actor, "controls.resume", None)

    def provider_allows(self, provider: str, data_class: str) -> bool:
        consent = (self.get("provider_consent") or {}).get(provider, "none")
        if consent == "none":
            return False
        return DATA_CLASSES.index(data_class) <= DATA_CLASSES.index(consent)
