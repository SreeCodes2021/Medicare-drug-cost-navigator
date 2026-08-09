from __future__ import annotations

import argparse
import json
import sys

from medicare_navigator.ui_test.browser_flows import run_browser_flow
from medicare_navigator.ui_test.checks import BROWSER_FLOW_NAMES, DEFAULT_BASE_URL, run_checks


def _cmd_run(args: argparse.Namespace) -> int:
    groups = set(args.groups.split(",")) if args.groups else {"static", "api", "chat"}
    offline = getattr(args, "offline", False)
    report = run_checks(
        groups=groups,
        offline=args.offline,
        base_url=args.base_url,
        timeout=args.timeout,
    )
    payload = report.to_dict()
    print(json.dumps(payload, indent=2))
    return 0 if report.passed else 1


def _cmd_browser(args: argparse.Namespace) -> int:
    try:
        result = run_browser_flow(
            args.flow,
            base_url=args.base_url,
            timeout_ms=int(args.timeout * 1000),
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "flow": args.flow,
                    "error": str(exc),
                    "hint": "pip install -e '.[browser]' && playwright install chromium",
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1

    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.ok else 1


def _cmd_list(args: argparse.Namespace) -> int:
    from medicare_navigator.ui_test.checks import (
        CHAT_RESPONSE_UI_FIELDS,
        GUIDED_SMOKE_FLOWS,
        JS_REFERENCED_ELEMENT_IDS,
        REQUIRED_API_PATHS,
        REQUIRED_ELEMENT_IDS,
        REQUIRED_STATIC_PATHS,
        SMOKE_MESSAGES,
    )

    print(
        json.dumps(
            {
                "groups": ["static", "api", "chat", "guided"],
                "browser_flows": list(BROWSER_FLOW_NAMES),
                "static_paths": REQUIRED_STATIC_PATHS,
                "api_paths": REQUIRED_API_PATHS,
                "element_ids": REQUIRED_ELEMENT_IDS,
                "js_referenced_element_ids": JS_REFERENCED_ELEMENT_IDS,
                "chat_response_fields": CHAT_RESPONSE_UI_FIELDS,
                "smoke_messages": SMOKE_MESSAGES,
                "guided_smoke_flows": GUIDED_SMOKE_FLOWS,
            },
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run UI contract and smoke checks for the Medicare Drug Cost Navigator."
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Live API base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use in-process FastAPI TestClient (no running server required)",
    )
    parser.add_argument("--timeout", type=float, default=120.0, help="HTTP timeout in seconds")

    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run UI checks (default: static + api + chat)")
    run.add_argument(
        "--groups",
        help="Comma-separated groups: static,api,chat,guided (default: all except guided)",
    )
    run.add_argument(
        "--offline",
        action="store_true",
        help="Use in-process FastAPI TestClient (no running server required)",
    )
    run.set_defaults(func=_cmd_run)

    browser = sub.add_parser("browser", help="Run a Playwright portal flow against a live server")
    browser.add_argument(
        "--flow",
        required=True,
        choices=list(BROWSER_FLOW_NAMES),
        help="Portal surface to exercise",
    )
    browser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Live API base URL (default: {DEFAULT_BASE_URL})",
    )
    browser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="HTTP timeout in seconds",
    )
    browser.set_defaults(func=_cmd_browser)

    list_cmd = sub.add_parser("list", help="List UI contracts and smoke cases")
    list_cmd.set_defaults(func=_cmd_list)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
