"""CLI for explicit, durable messages to existing sessions in this profile."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from . import outbox


def setup(parser):
    sub = parser.add_subparsers(dest="action", required=True)
    send = sub.add_parser("send", help="Queue a message for an exact existing session")
    send.add_argument("--session-id", required=True)
    send.add_argument("--dedupe-key", required=True, help="Stable identity for this send; reuse on retry")
    send.add_argument("--file", default="-", help="Message file, or - for stdin")
    status = sub.add_parser("status", help="Read a queued message's receipt status")
    status.add_argument("request_id")


def run(args):
    try:
        if args.action == "send":
            text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
            result = outbox.send(session_id=args.session_id, payload=text, dedupe_key=args.dedupe_key)
        else:
            result = outbox.get(args.request_id)
            if result is None:
                raise ValueError("request not found in this profile")
        print(json.dumps(result, sort_keys=True))
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "failure", "error": str(exc)}))
        return 1
    return 0


def handler(args):
    raise SystemExit(run(args))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    setup(parser)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
