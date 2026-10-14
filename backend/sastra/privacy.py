"""Privacy ledger: every byte that leaves the server is recorded with its
destination, provider, data class, purpose and the provider's retention note.
Powers the privacy dashboard ("what stays local, what leaves, who gets it").
"""
from __future__ import annotations

from .db import Database, new_id, now

RETENTION_NOTES = {
    "offline": "Never leaves the server.",
    "local": "Sent to a model runtime you host (e.g. Ollama/llama.cpp); stays on your hardware.",
    "anthropic": "Anthropic API — see Anthropic's commercial terms for API data retention.",
    "openai_compat": "Third-party OpenAI-compatible API — check that provider's retention policy.",
    "brave": "Search query text sent to Brave Search API.",
    "tavily": "Search query text sent to Tavily.",
    "searxng": "Search query sent to your SearXNG instance.",
    "web": "HTTP GET to a public website (URL only; no personal data in body).",
    "webhook": "Payload you approved, sent to the webhook URL you approved.",
}


class PrivacyLedger:
    def __init__(self, db: Database):
        self.db = db

    def record(self, destination: str, provider: str, data_class: str, bytes_out: int, purpose: str,
               task_id: str | None = None) -> None:
        self.db.insert("data_flows", {"id": new_id(), "ts": now(), "destination": destination[:300], "provider": provider,
                                      "data_class": data_class, "bytes_out": int(bytes_out), "purpose": purpose[:200],
                                      "retention_note": RETENTION_NOTES.get(provider, "See provider terms."),
                                      "task_id": task_id})

    def dashboard(self, days: int = 30) -> dict:
        since = now() - days * 86400
        by_provider = self.db.query(
            "SELECT provider, data_class, COUNT(*) AS events, SUM(bytes_out) AS bytes_out, MAX(retention_note) AS retention_note "
            "FROM data_flows WHERE ts >= ? GROUP BY provider, data_class ORDER BY bytes_out DESC", (since,))
        recent = self.db.query("SELECT * FROM data_flows ORDER BY ts DESC LIMIT 50")
        return {
            "days": days,
            "stays_local": ["Conversations and messages (database on your server/phone)", "Memory records",
                            "Task history, audit log, artifacts", "Anything classified 'secret'"],
            "leaves_device": by_provider,
            "recent_flows": recent,
        }
