"""Human approval queue for risky tool calls.

A risky call is parked as `pending`. A person approves or rejects it (CLI
here; a Slack or Teams button would call the same two methods), and only
then can the gateway execute it. Approvals expire, and each can be used once.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone


class ApprovalError(Exception):
    pass


class ApprovalQueue:
    def __init__(self, path: str = ":memory:", ttl: timedelta = timedelta(hours=1)):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._ttl = ttl
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS approvals (
                   id TEXT PRIMARY KEY, agent TEXT, tool TEXT, args TEXT,
                   status TEXT, created TEXT, decided_by TEXT)"""
        )
        self._db.commit()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def request(self, agent: str, tool: str, args: dict) -> str:
        aid = uuid.uuid4().hex[:12]
        with self._lock:
            self._db.execute(
                "INSERT INTO approvals VALUES (?,?,?,?, 'pending', ?, NULL)",
                (aid, agent, tool, json.dumps(args, sort_keys=True), self._now().isoformat()),
            )
            self._db.commit()
        return aid

    def pending(self) -> list[dict]:
        rows = self._db.execute(
            "SELECT id, agent, tool, args, created FROM approvals WHERE status='pending' ORDER BY created"
        ).fetchall()
        return [
            {"id": r[0], "agent": r[1], "tool": r[2], "args": json.loads(r[3]), "created": r[4]}
            for r in rows
        ]

    def decide(self, approval_id: str, approve: bool, decided_by: str) -> None:
        with self._lock:
            cur = self._db.execute(
                "UPDATE approvals SET status=?, decided_by=? WHERE id=? AND status='pending'",
                ("approved" if approve else "rejected", decided_by, approval_id),
            )
            self._db.commit()
        if cur.rowcount != 1:
            raise ApprovalError(f"approval {approval_id} is not pending")

    def consume(self, approval_id: str, agent: str, tool: str, args: dict) -> str:
        """Mark an approval used. Returns who approved it.

        The call must match exactly what was approved, so an agent cannot get
        approval for one set of arguments and then run another.
        """
        with self._lock:
            row = self._db.execute(
                "SELECT agent, tool, args, status, created, decided_by FROM approvals WHERE id=?",
                (approval_id,),
            ).fetchone()
            if row is None:
                raise ApprovalError(f"unknown approval {approval_id}")
            r_agent, r_tool, r_args, status, created, decided_by = row
            if status != "approved":
                raise ApprovalError(f"approval {approval_id} is {status}")
            if self._now() - datetime.fromisoformat(created) > self._ttl:
                raise ApprovalError(f"approval {approval_id} has expired")
            if (r_agent, r_tool, r_args) != (agent, tool, json.dumps(args, sort_keys=True)):
                raise ApprovalError("call does not match what was approved")
            self._db.execute("UPDATE approvals SET status='used' WHERE id=?", (approval_id,))
            self._db.commit()
            return decided_by
