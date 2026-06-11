"""Starfleet API client and cookie management."""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import httpx

from sfctl.config import HEADERS, _config_path, get_api_base, update_config
from sfctl.models import (
    ContentResponse,
    CookieProfile,
    FeedbackResponse,
    HistoryEntry,
    TaskResponse,
)


def extract_task_id(task: str) -> str:
    match = re.search(r"(t-[\w]+)", task)
    if not match:
        print(f"Could not extract task ID from: {task!r}", file=sys.stderr)
        sys.exit(1)
    return match.group(1)


class AuthError(Exception):
    """Raised when the API returns an authentication error."""


def _check_response(resp: httpx.Response, label: str) -> None:
    """Raise AuthError on 401/403, or raise for other HTTP errors."""
    if resp.status_code in (401, 403):
        raise AuthError(
            f"{label}: HTTP {resp.status_code}. Your cookies may be expired or from the wrong profile.\n"
            f"Run: sfctl --clear-config cookie_file"
        )
    resp.raise_for_status()


_MAX_RETRIES = 3
_RETRY_STATUSES = frozenset({502, 503, 504})


async def _request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    label: str,
    **kwargs,
) -> httpx.Response:
    """Make a request with retries on transient server errors."""
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = await client.request(method, url, **kwargs)
            if resp.status_code not in _RETRY_STATUSES:
                _check_response(resp, label)
                return resp
            last_exc = httpx.HTTPStatusError(
                f"{label}: HTTP {resp.status_code}",
                request=resp.request,
                response=resp,
            )
        except httpx.TransportError as exc:
            last_exc = exc
        if attempt < _MAX_RETRIES - 1:
            await asyncio.sleep(1 * (attempt + 1))
    # Final attempt failed -- raise with context
    if isinstance(last_exc, httpx.HTTPStatusError):
        _check_response(last_exc.response, label)
    raise last_exc  # type: ignore[misc]


async def _fetch_data_async(task_id: str, cookies: dict[str, str]) -> dict:
    """Fetch all task data concurrently using httpx with retries."""
    api_base = get_api_base()
    base = f"{api_base}/tasks/{task_id}"

    async with httpx.AsyncClient(headers=HEADERS, cookies=cookies, timeout=30) as client:
        r_task, r_history, r_feedback, r_content = await asyncio.gather(
            _request_with_retry(client, "GET", base, "Task fetch"),
            _request_with_retry(
                client,
                "POST",
                f"{base}/history",
                "History fetch",
                json={},
                headers={"Content-Type": "application/json"},
            ),
            _request_with_retry(
                client, "GET", f"{api_base}/labeling/tasks/{task_id}/feedback", "Feedback fetch"
            ),
            _request_with_retry(
                client,
                "GET",
                f"{base}/content",
                "Content fetch",
                headers={"Content-Type": "application/json"},
            ),
        )

    task_resp = r_task.json()
    history = r_history.json()
    feedback = r_feedback.json()
    content = r_content.json()

    TaskResponse.model_validate(task_resp)
    if isinstance(history, list):
        for h in history:
            HistoryEntry.model_validate(h)
    else:
        HistoryEntry.model_validate(history)
    FeedbackResponse.model_validate(feedback)
    ContentResponse.model_validate(content)

    return {"task": task_resp, "history": history, "feedback": feedback, "content": content}


def fetch_data(task: str, cookies: dict[str, str]) -> dict:
    """Fetch task data, running async requests concurrently under the hood."""
    task_id = extract_task_id(task)
    return asyncio.run(_fetch_data_async(task_id, cookies))



_BROWSERS = [
    ("Chrome", "chrome"),
    ("Brave", "brave"),
    ("Edge", "edge"),
    ("Chromium", "chromium"),
    ("Firefox", "firefox"),
    ("Opera", "opera"),
    ("Vivaldi", "vivaldi"),
]


def _chromium_cookie_patterns() -> list[tuple[str, str, list[str]]]:
    """Return (label, func_name, glob_patterns) for each Chromium-based browser per OS."""
    if sys.platform == "darwin":
        base = "~/Library/Application Support"
        return [
            ("Chrome", "chrome", [f"{base}/Google/Chrome/*/Cookies"]),
            ("Brave", "brave", [f"{base}/BraveSoftware/Brave-Browser/*/Cookies"]),
            ("Edge", "edge", [f"{base}/Microsoft Edge/*/Cookies"]),
            ("Chromium", "chromium", [f"{base}/Chromium/*/Cookies"]),
            ("Opera", "opera", [f"{base}/com.operasoftware.Opera/Cookies"]),
            ("Vivaldi", "vivaldi", [f"{base}/Vivaldi/*/Cookies"]),
        ]
    elif sys.platform == "win32":
        import os

        local = os.environ.get("LOCALAPPDATA", "")
        return [
            (
                "Chrome",
                "chrome",
                [
                    f"{local}\\Google\\Chrome\\User Data\\*\\Cookies",
                    f"{local}\\Google\\Chrome\\User Data\\*\\Network\\Cookies",
                ],
            ),
            ("Brave", "brave", [f"{local}\\BraveSoftware\\Brave-Browser\\User Data\\*\\Cookies"]),
            ("Edge", "edge", [f"{local}\\Microsoft\\Edge\\User Data\\*\\Cookies"]),
            ("Chromium", "chromium", [f"{local}\\Chromium\\User Data\\*\\Cookies"]),
            ("Opera", "opera", [f"{local}\\Opera Software\\Opera Stable\\Cookies"]),
            ("Vivaldi", "vivaldi", [f"{local}\\Vivaldi\\User Data\\*\\Cookies"]),
        ]
    else:
        return [
            ("Chrome", "chrome", ["~/.config/google-chrome/*/Cookies"]),
            ("Brave", "brave", ["~/.config/BraveSoftware/Brave-Browser/*/Cookies"]),
            ("Edge", "edge", ["~/.config/microsoft-edge/*/Cookies"]),
            ("Chromium", "chromium", ["~/.config/chromium/*/Cookies"]),
            ("Opera", "opera", ["~/.config/opera/Cookies"]),
            ("Vivaldi", "vivaldi", ["~/.config/vivaldi/*/Cookies"]),
        ]


def _firefox_cookie_patterns() -> list[tuple[str, str, list[str]]]:
    """Return (label, func_name, glob_patterns) for Firefox-based browsers per OS."""
    if sys.platform == "darwin":
        base = "~/Library/Application Support"
        return [
            ("Firefox", "firefox", [f"{base}/Firefox/Profiles/*/cookies.sqlite"]),
            ("LibreWolf", "librewolf", [f"{base}/librewolf/Profiles/*/cookies.sqlite"]),
        ]
    elif sys.platform == "win32":
        import os

        appdata = os.environ.get("APPDATA", "")
        return [
            ("Firefox", "firefox", [f"{appdata}\\Mozilla\\Firefox\\Profiles\\*\\cookies.sqlite"]),
        ]
    else:
        return [
            (
                "Firefox",
                "firefox",
                [
                    "~/.mozilla/firefox/*/cookies.sqlite",
                    "~/snap/firefox/common/.mozilla/firefox/*/cookies.sqlite",
                ],
            ),
            ("LibreWolf", "librewolf", ["~/.librewolf/*/cookies.sqlite"]),
        ]


def find_cookie_profiles() -> list[CookieProfile]:
    """Discover all browser cookie files across all profiles."""
    import glob
    import os

    all_patterns = _chromium_cookie_patterns() + _firefox_cookie_patterns()
    profiles: list[CookieProfile] = []
    seen: set[str] = set()

    for label, func_name, patterns in all_patterns:
        for pattern in patterns:
            expanded = os.path.expanduser(pattern)
            for match in sorted(glob.glob(expanded)):
                if match in seen:
                    continue
                seen.add(match)
                # Use parent dir name as profile identifier (e.g. "Default", "Profile 1")
                profile_dir = os.path.basename(os.path.dirname(match))
                plabel = f"{label} - {profile_dir}" if profile_dir else label
                profiles.append(CookieProfile(path=match, label=plabel, func=func_name))

    return profiles


_TARGET_HOST = "starfleet-backend.teachx.ai"


def _domain_matches(cookie_domain: str, host: str) -> bool:
    """RFC 6265 domain matching: does this cookie belong to the request host?"""
    if cookie_domain.startswith("."):
        return host == cookie_domain[1:] or host.endswith(cookie_domain)
    return cookie_domain == host


def _load_cookies(func_name: str, cookie_file: str | None = None) -> dict[str, str]:
    """Load cookies and return a dict filtered to the API host domain."""
    import browser_cookie3

    loader = getattr(browser_cookie3, func_name)
    cj = loader(cookie_file=cookie_file) if cookie_file else loader()
    return {
        c.name: c.value
        for c in cj
        if c.value is not None and _domain_matches(c.domain, _TARGET_HOST)
    }


def interactive_cookie_setup() -> CookieProfile:
    """Interactive first-run: let user pick a browser profile, validate, persist."""
    profiles = find_cookie_profiles()
    if not profiles:
        print(
            "No browser profiles found. Use -c /path/to/Cookies to specify manually.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("Select a browser profile for Starfleet cookies:\n")
    for i, p in enumerate(profiles, 1):
        print(f"  [{i}] {p.label}")
        print(f"      {p.path}")
    print()

    while True:
        try:
            choice = input(f"Pick [1-{len(profiles)}]: ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(profiles):
                break
            print(f"Enter a number between 1 and {len(profiles)}.")
        except (ValueError, EOFError):
            print(f"Enter a number between 1 and {len(profiles)}.")

    selected = profiles[idx]

    print(f"\nValidating {selected.label}...")
    try:
        cookies = _load_cookies(selected.func, selected.path)
        if cookies:
            print("Found starfleet cookies.")
        else:
            print("Warning: no starfleet cookies found in this profile, but saving anyway.")
            print("You can re-run with --clear-config cookie_file to pick again.")
    except Exception:
        print("Warning: could not validate cookies, but saving anyway.")

    update_config(cookie_file=selected.path, browser=selected.func)
    print(f"Saved to {_config_path()}\n")
    return selected


def resolve_cookies(cookie_file_arg: str | None, verbose: bool = False) -> dict[str, str]:
    """Resolve cookies: CLI flag > config > interactive setup. Returns dict for httpx."""
    from sfctl.config import load_config

    config = load_config()
    func_name = config.get("browser", "chrome")

    if cookie_file_arg:
        if verbose:
            print(f"Using cookies from CLI flag: {cookie_file_arg}")
        update_config(cookie_file=cookie_file_arg)
        return _load_cookies(func_name, cookie_file_arg)

    saved_path = config.get("cookie_file")
    if saved_path and Path(saved_path).exists():
        if verbose:
            print(f"Using saved cookie path: {saved_path}")
        return _load_cookies(func_name, saved_path)

    selected = interactive_cookie_setup()
    return _load_cookies(selected.func, selected.path)
