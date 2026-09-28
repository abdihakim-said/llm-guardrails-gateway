"""Command line for the people in the loop.

  guardrails pending  --db approvals.db
  guardrails approve  --db approvals.db <id> --by alice
  guardrails reject   --db approvals.db <id> --by alice
  guardrails verify   audit.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys

from .approvals import ApprovalError, ApprovalQueue
from .audit import verify


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="guardrails")
    sub = p.add_subparsers(dest="cmd", required=True)
    pend = sub.add_parser("pending", help="list tool calls waiting for approval")
    pend.add_argument("--db", required=True)
    for name in ("approve", "reject"):
        s = sub.add_parser(name, help=f"{name} a pending tool call")
        s.add_argument("--db", required=True)
        s.add_argument("approval_id")
        s.add_argument("--by", required=True, help="who is deciding (recorded)")
    ver = sub.add_parser("verify", help="check the audit log hash chain")
    ver.add_argument("path")
    a = p.parse_args(argv)

    if a.cmd == "verify":
        ok, n = verify(a.path)
        print(f"{'OK' if ok else 'TAMPERED'}: {n} valid entries")
        return 0 if ok else 1

    q = ApprovalQueue(a.db)
    if a.cmd == "pending":
        for item in q.pending():
            print(json.dumps(item))
        return 0
    try:
        q.decide(a.approval_id, approve=(a.cmd == "approve"), decided_by=a.by)
    except ApprovalError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"{a.cmd}d {a.approval_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
