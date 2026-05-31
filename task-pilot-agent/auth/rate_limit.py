from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict


class RateLimitError(RuntimeError):
    pass


@dataclass
class InMemoryRateLimiter:
    max_attempts: int
    window_seconds: int
    _attempts: Dict[str, Deque[float]] = field(default_factory=lambda: defaultdict(deque))

    def check(self, key: str, *, now: float | None = None) -> None:
        timestamp = float(now if now is not None else time.time())
        bucket = self._attempts[key or "unknown"]
        cutoff = timestamp - self.window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= self.max_attempts:
            raise RateLimitError("too many requests")
        bucket.append(timestamp)

    def reset(self) -> None:
        self._attempts.clear()
