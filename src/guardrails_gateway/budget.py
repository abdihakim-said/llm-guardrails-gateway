"""Per-team monthly budgets, enforced before a call is made.

The flow is reserve -> call -> settle. Reserving the worst-case cost first
means concurrent calls cannot overshoot the budget, and a failed call
releases its reservation.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal


class BudgetExceeded(Exception):
    def __init__(self, team: str, limit: Decimal, spent: Decimal, requested: Decimal):
        super().__init__(
            f"team {team!r} budget exceeded: limit ${limit}, spent/reserved ${spent}, "
            f"this call needs up to ${requested}"
        )
        self.team, self.limit, self.spent, self.requested = team, limit, spent, requested


def current_period(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m")


class BudgetLedger:
    def __init__(self, path: str = ":memory:", limits: dict[str, Decimal] | None = None):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._limits = dict(limits or {})
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS ledger (
                   id TEXT PRIMARY KEY, team TEXT NOT NULL, period TEXT NOT NULL,
                   amount TEXT NOT NULL, state TEXT NOT NULL)"""
        )
        self._db.commit()

    def set_limit(self, team: str, monthly_usd: Decimal) -> None:
        self._limits[team] = Decimal(monthly_usd)

    def spent(self, team: str, period: str | None = None) -> Decimal:
        """Settled spend plus open reservations for the period."""
        rows = self._db.execute(
            "SELECT amount FROM ledger WHERE team=? AND period=? AND state IN ('reserved','settled')",
            (team, period or current_period()),
        ).fetchall()
        return sum((Decimal(a) for (a,) in rows), Decimal(0))

    def reserve(self, team: str, amount: Decimal) -> str:
        if team not in self._limits:
            # Deny by default: a team with no budget cannot spend.
            raise BudgetExceeded(team, Decimal(0), Decimal(0), amount)
        period = current_period()
        with self._lock:
            spent = self.spent(team, period)
            limit = self._limits[team]
            if spent + amount > limit:
                raise BudgetExceeded(team, limit, spent, amount)
            rid = uuid.uuid4().hex
            self._db.execute(
                "INSERT INTO ledger VALUES (?,?,?,?, 'reserved')", (rid, team, period, str(amount))
            )
            self._db.commit()
            return rid

    def settle(self, reservation_id: str, actual: Decimal) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE ledger SET amount=?, state='settled' WHERE id=? AND state='reserved'",
                (str(actual), reservation_id),
            )
            self._db.commit()

    def release(self, reservation_id: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE ledger SET state='released' WHERE id=? AND state='reserved'", (reservation_id,)
            )
            self._db.commit()
