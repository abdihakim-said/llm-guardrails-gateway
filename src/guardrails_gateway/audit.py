"""Append-only audit log with a hash chain.

Each JSON line carries the SHA-256 of the previous line, so deleting or
editing an entry breaks verification. This makes tampering detectable, not
impossible; ship the log somewhere write-once (e.g. S3 Object Lock) too.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64


class AuditLog:
    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._prev = self._last_hash()

    def _last_hash(self) -> str:
        if not self._path.exists():
            return GENESIS
        last = GENESIS
        with self._path.open() as f:
            for line in f:
                if line.strip():
                    last = json.loads(line)["hash"]
        return last

    def record(self, event: str, **fields) -> dict:
        with self._lock:
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": event,
                **fields,
                "prev": self._prev,
            }
            body = json.dumps(entry, sort_keys=True, default=str)
            entry["hash"] = hashlib.sha256(body.encode()).hexdigest()
            with self._path.open("a") as f:
                f.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
            self._prev = entry["hash"]
            return entry


def verify(path: str | Path) -> tuple[bool, int]:
    """Return (ok, number_of_valid_entries_before_the_first_break)."""
    prev = GENESIS
    count = 0
    with Path(path).open() as f:
        for line in f:
            if not line.strip():
                continue
            entry = json.loads(line)
            claimed = entry.pop("hash")
            if entry.get("prev") != prev:
                return False, count
            body = json.dumps(entry, sort_keys=True, default=str)
            if hashlib.sha256(body.encode()).hexdigest() != claimed:
                return False, count
            prev = claimed
            count += 1
    return True, count
