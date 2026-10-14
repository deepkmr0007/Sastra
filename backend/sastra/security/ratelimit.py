"""In-process token-bucket rate limiter (per client key)."""
from __future__ import annotations

import threading
import time


class RateLimiter:
    def __init__(self, rate_per_s: float, burst: int):
        self.rate = rate_per_s
        self.burst = burst
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, cost: float = 1.0) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (float(self.burst), now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens >= cost:
                self._buckets[key] = (tokens - cost, now)
                return True
            self._buckets[key] = (tokens, now)
            return False
