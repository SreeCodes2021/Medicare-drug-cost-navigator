from __future__ import annotations

import argparse
import json
import sys

from medicare_navigator.qa.chat_client import (
    DEFAULT_BASE_URL,
    build_grading_bundle,
    check_health,
    invoke_chat,
)


def _cmd_health(args: argparse.Namespace) -> int:
    try:
        data = check_health(base_url=args.base_url, timeout=args.timeout)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "base_url": args.base_url}), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "base_url": args.base_url, "health": data}, indent=2))
    return 0


def _cmd_send(args: argparse.Namespace) -> int:
    filters = None
    if args.filters_json:
        filters = json.loads(args.filters_json)

    try:
        bundle = invoke_chat(
            args.message,
            session_id=args.session_id,
            filters=filters,
            base_url=args.base_url,
            timeout=args.timeout,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                    "base_url": args.base_url,
                    "hint": "Start the API: uvicorn medicare_navigator.api.app:app --reload --port 8000",
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1

    print(json.dumps({"ok": True, **bundle}, indent=2, default=str))
    return 0


def _cmd_grade_input(args: argparse.Namespace) -> int:
    """Normalize pasted /api/chat JSON into a grading bundle."""
    raw = json.loads(sys.stdin.read() if args.message is None else args.message)
    if "response" in raw and "grading" not in raw:
        user_message = args.user_message or ""
        bundle = build_grading_bundle(user_message, raw)
        print(json.dumps({"ok": True, **bundle}, indent=2, default=str))
        return 0
    if "grading" in raw:
        print(json.dumps({"ok": True, **raw}, indent=2, default=str))
        return 0
    print(json.dumps({"ok": False, "error": "Unrecognized JSON shape"}), file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Invoke the Medicare navigator chat API for chat-QA grading."
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"API base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument("--timeout", type=float, default=120.0, help="HTTP timeout in seconds")

    sub = parser.add_subparsers(dest="command", required=True)

    health = sub.add_parser("health", help="Check /api/health")
    health.set_defaults(func=_cmd_health)

    send = sub.add_parser("send", help="Send a chat message and return grading bundle JSON")
    send.add_argument("--message", "-m", required=True, help="User message to send")
    send.add_argument("--session-id", help="Continue an existing session")
    send.add_argument("--filters-json", help='Optional filters JSON, e.g. \'{"plan_id":"H1234-045"}\'')
    send.set_defaults(func=_cmd_send)

    grade_input = sub.add_parser(
        "grade-input",
        help="Convert pasted /api/chat JSON on stdin into a grading bundle",
    )
    grade_input.add_argument("--user-message", help="Original user question for the pasted response")
    grade_input.add_argument("message", nargs="?", help="Optional JSON string instead of stdin")
    grade_input.set_defaults(func=_cmd_grade_input)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
