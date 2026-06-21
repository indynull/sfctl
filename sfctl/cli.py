"""CLI entry point for Starfleet Control."""

import argparse
import json
import sys

from sfctl.config import _config_path, load_config, save_config, update_config


def _run_analyze(args):
    """Run the analyze subcommand."""
    from dataclasses import asdict
    from pathlib import Path

    if args.fixture:
        fixture_path = Path(args.fixture)
        if not fixture_path.exists():
            print(f"Fixture file not found: {args.fixture}", file=sys.stderr)
            raise SystemExit(1)
        data = json.loads(fixture_path.read_text())
    else:
        from sfctl.api import AccessError, AuthError, fetch_data, resolve_cookies

        cookies, _ = resolve_cookies(args.cookie_file, args.verbose, token_arg=args.token)
        try:
            data = fetch_data(args.task, cookies)
        except (AccessError, AuthError) as e:
            print(f"\nError: {e}", file=sys.stderr)
            raise SystemExit(1) from None

    from sfctl.analysis import analyze_task

    result = analyze_task(data)

    if args.json:
        json.dump(asdict(result), sys.stdout, indent=2, default=str)
        print()
        return

    from sfctl.app import StarfleetApp

    task_arg = args.task or data.get("task", {}).get("taskId", "fixture")
    cookies_val = None if args.fixture else cookies
    StarfleetApp(task_arg, data, cookies=cookies_val, analysis=result).run()


def _build_analyze_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sfctl analyze",
        description="Analyze a task for quality signals and AI detection",
    )
    p.add_argument("task", nargs="?", default=None, help="Task ID")
    p.add_argument("-f", "--fixture", default=None, help="Load from fixture file")
    p.add_argument("-c", "--cookie-file", default=None, help="Path to Cookies file")
    p.add_argument("-t", "--token", default=None, help="Access token")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--json", action="store_true", help="Output analysis as JSON")
    return p


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "analyze":
        ap = _build_analyze_parser()
        args = ap.parse_args(sys.argv[2:])
        if not args.task and not args.fixture:
            ap.error("task is required (or use --fixture)")
        _run_analyze(args)
        return

    parser = argparse.ArgumentParser(
        description="Starfleet Control -- task review and evaluation CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  sfctl t-abc123def
  sfctl t-abc123def -t mytoken123
  sfctl t-abc123def -c /path/to/Cookies -v
  sfctl analyze t-abc123def
  sfctl analyze t-abc123def --json
  sfctl --fixture tests/fixtures/task_sample.json
  sfctl --show-config
  sfctl --set api_base https://staging.example.com
  sfctl --clear-config cookie_file
""",
    )
    parser.add_argument(
        "task", nargs="?", default=None, help="Task ID (e.g. t-abc123def or full URL)"
    )
    parser.add_argument(
        "-c", "--cookie-file", default=None, help="Path to browser Cookies file (saved to config)"
    )
    parser.add_argument(
        "-t",
        "--token",
        default=None,
        help="Access token for the Starfleet API (saved to config)",
    )
    parser.add_argument(
        "-f", "--fixture", default=None, help="Load from a JSON fixture file instead of the API"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Show progress messages")
    parser.add_argument(
        "--dump", action="store_true", help="Dump raw JSON response to stdout and exit"
    )
    parser.add_argument("--show-config", action="store_true", help="Print current config and exit")
    parser.add_argument(
        "--set",
        nargs=2,
        metavar=("KEY", "VALUE"),
        action="append",
        default=[],
        help="Set a config key (e.g. --set theme dracula)",
    )
    parser.add_argument(
        "--clear-config",
        nargs="*",
        metavar="KEY",
        help="Clear config keys (or entire config if no keys given)",
    )
    args = parser.parse_args()

    if args.clear_config is not None:
        if args.clear_config:
            config = load_config()
            for key in args.clear_config:
                config.pop(key, None)
            save_config(config)
            print(f"Cleared keys: {', '.join(args.clear_config)}")
        else:
            save_config({})
            print("Config cleared.")
        return

    if args.set:
        for key, value in args.set:
            update_config(**{key: value})
            print(f"  {key} = {value}")
        print(f"Saved to {_config_path()}")
        if not args.task:
            return

    if args.show_config:
        config = load_config()
        print(f"Config file: {_config_path()}")
        if not config:
            print("  (empty)")
        else:
            for k, v in sorted(config.items()):
                print(f"  {k} = {v}")
        return

    if args.fixture:
        from pathlib import Path

        fixture_path = Path(args.fixture)
        if not fixture_path.exists():
            print(f"Fixture file not found: {args.fixture}", file=sys.stderr)
            raise SystemExit(1)
        data = json.loads(fixture_path.read_text())
        task_arg = args.task or data.get("task", {}).get("taskId", "fixture")
        if args.verbose:
            print(f"Loaded fixture from {args.fixture}")

        if args.dump:
            json.dump(data, sys.stdout, indent=2)
            print()
            return

        from sfctl.app import StarfleetApp

        StarfleetApp(task_arg, data).run()
        return

    if not args.task:
        parser.error("task is required (or use --fixture / --show-config / --set / --clear-config)")

    from sfctl.api import AccessError, AuthError, fetch_data, resolve_cookies

    cookies, using_token = resolve_cookies(args.cookie_file, args.verbose, token_arg=args.token)

    if args.verbose:
        names = sorted(cookies.keys())
        print(f"Loaded {len(names)} cookies: {', '.join(names)}")
        print(f"Fetching task {args.task}...")

    try:
        data = fetch_data(args.task, cookies)
    except AccessError as e:
        print(f"\nError: {e}", file=sys.stderr)
        raise SystemExit(1) from None
    except AuthError as e:
        print(f"\nError: {e}", file=sys.stderr)
        if using_token:
            raise SystemExit(1) from None
        answer = (
            input("\nWould you like to pick a different cookie profile? [y/N] ").strip().lower()
        )
        if answer in ("y", "yes"):
            from sfctl.api import _load_cookies, interactive_cookie_setup

            selected = interactive_cookie_setup()
            cookies = _load_cookies(selected.func, selected.path)
            data = fetch_data(args.task, cookies)
        else:
            raise SystemExit(1) from None

    if args.dump:
        json.dump(data, sys.stdout, indent=2)
        print()
        return

    if args.verbose:
        print("Launching TUI...")

    from sfctl.app import StarfleetApp

    StarfleetApp(args.task, data, cookies=cookies).run()
