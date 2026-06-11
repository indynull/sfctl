"""Tests for API client, cookie management, and domain matching."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import httpx
import pytest

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "task_sample.json"
TASK_ID = "t-D2F1God7q8QRa48qVJpO1"


class TestExtractTaskId:
    def test_bare_id(self):
        from sftui.api import extract_task_id

        assert extract_task_id(TASK_ID) == TASK_ID

    def test_full_url(self):
        from sftui.api import extract_task_id

        url = f"https://starfleet.teachx.ai/tasks/{TASK_ID}"
        assert extract_task_id(url) == TASK_ID

    def test_invalid(self):
        from sftui.api import extract_task_id

        with pytest.raises(SystemExit):
            extract_task_id("no-task-here")


class TestDomainMatching:
    def test_exact_match(self):
        from sftui.api import _domain_matches

        assert _domain_matches("starfleet-backend.teachx.ai", "starfleet-backend.teachx.ai")

    def test_dot_prefix_match(self):
        from sftui.api import _domain_matches

        assert _domain_matches(".teachx.ai", "starfleet-backend.teachx.ai")

    def test_dot_prefix_exact_without_dot(self):
        from sftui.api import _domain_matches

        assert _domain_matches(".teachx.ai", "teachx.ai")

    def test_no_match_different_subdomain(self):
        from sftui.api import _domain_matches

        assert not _domain_matches("autoqa.teachx.ai", "starfleet-backend.teachx.ai")

    def test_no_match_different_domain(self):
        from sftui.api import _domain_matches

        assert not _domain_matches(".google.com", "starfleet-backend.teachx.ai")


class TestCheckResponse:
    def test_401_raises_auth_error(self):
        from sftui.api import AuthError, _check_response

        resp = httpx.Response(401, request=httpx.Request("GET", "http://x"))
        with pytest.raises(AuthError, match="401"):
            _check_response(resp, "Test")

    def test_403_raises_auth_error(self):
        from sftui.api import AuthError, _check_response

        resp = httpx.Response(403, request=httpx.Request("GET", "http://x"))
        with pytest.raises(AuthError):
            _check_response(resp, "Test")

    def test_500_raises_http_error(self):
        from sftui.api import _check_response

        resp = httpx.Response(500, request=httpx.Request("GET", "http://x"))
        with pytest.raises(httpx.HTTPStatusError):
            _check_response(resp, "Test")

    def test_200_ok(self):
        from sftui.api import _check_response

        resp = httpx.Response(200, request=httpx.Request("GET", "http://x"))
        _check_response(resp, "Test")


class TestRequestWithRetry:
    @pytest.mark.asyncio
    async def test_success_first_try(self):
        from sftui.api import _request_with_retry

        transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True}))
        async with httpx.AsyncClient(transport=transport) as client:
            resp = await _request_with_retry(client, "GET", "http://x/ok", "Test")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_retry_on_502(self):
        from sftui.api import _request_with_retry

        call_count = 0

        async def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return httpx.Response(502, request=request)
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            resp = await _request_with_retry(client, "GET", "http://x/retry", "Test")
            assert resp.status_code == 200
            assert call_count == 3

    @pytest.mark.asyncio
    async def test_auth_error_no_retry(self):
        from sftui.api import AuthError, _request_with_retry

        transport = httpx.MockTransport(lambda r: httpx.Response(401, request=r))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(AuthError):
                await _request_with_retry(client, "GET", "http://x/auth", "Test")

    @pytest.mark.asyncio
    async def test_transport_error_retries(self):
        from sftui.api import _request_with_retry

        call_count = 0

        async def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise httpx.ConnectError("connection refused")
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            resp = await _request_with_retry(client, "GET", "http://x/t", "Test")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_502(self):
        from sftui.api import _request_with_retry

        transport = httpx.MockTransport(lambda r: httpx.Response(502, request=r))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await _request_with_retry(client, "GET", "http://x/fail", "Test")

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_transport(self):
        from sftui.api import _request_with_retry

        async def handler(request):
            raise httpx.ConnectError("down")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx.ConnectError):
                await _request_with_retry(client, "GET", "http://x/fail", "Test")

    @pytest.mark.asyncio
    async def test_502_then_401(self):
        from sftui.api import AuthError, _request_with_retry

        call_count = 0

        async def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(502, request=request)
            return httpx.Response(401, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(AuthError):
                await _request_with_retry(client, "GET", "http://x", "Test")


class TestFetchDataAsync:
    @pytest.mark.asyncio
    async def test_fetch_data_async(self, fixture_data):
        async def handler(request):
            url = str(request.url)
            if url.endswith("/content"):
                return httpx.Response(200, json=fixture_data["content"])
            elif url.endswith("/history"):
                return httpx.Response(200, json=fixture_data["history"])
            elif "feedback" in url:
                return httpx.Response(200, json=fixture_data["feedback"])
            else:
                return httpx.Response(200, json=fixture_data["task"])

        import sftui.api as api_mod

        orig = api_mod.httpx.AsyncClient

        class PatchedClient(orig):
            def __init__(self, **kwargs):
                kwargs.pop("cookies", None)
                super().__init__(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(api_mod.httpx, "AsyncClient", PatchedClient)
        try:
            result = await api_mod._fetch_data_async(TASK_ID, {})
            assert result["task"]["taskId"] == TASK_ID
            assert isinstance(result["history"], list)
        finally:
            monkeypatch.undo()

    @pytest.mark.asyncio
    async def test_history_single_dict(self, fixture_data):
        single_history = fixture_data["history"][0]

        async def handler(request):
            url = str(request.url)
            if url.endswith("/content"):
                return httpx.Response(200, json=fixture_data["content"])
            elif url.endswith("/history"):
                return httpx.Response(200, json=single_history)
            elif "feedback" in url:
                return httpx.Response(200, json=fixture_data["feedback"])
            else:
                return httpx.Response(200, json=fixture_data["task"])

        import sftui.api as api_mod

        orig = api_mod.httpx.AsyncClient

        class PatchedClient(orig):
            def __init__(self, **kwargs):
                kwargs.pop("cookies", None)
                super().__init__(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(api_mod.httpx, "AsyncClient", PatchedClient)
        try:
            result = await api_mod._fetch_data_async(TASK_ID, {})
            assert isinstance(result["history"], dict)
        finally:
            monkeypatch.undo()


class TestFetchData:
    def test_fetch_data_sync(self, fixture_data, monkeypatch):
        import sftui.api as api_mod

        async def fake_fetch(task_id, cookies):
            return fixture_data

        monkeypatch.setattr(api_mod, "_fetch_data_async", fake_fetch)
        result = api_mod.fetch_data(TASK_ID, {})
        assert result == fixture_data


class TestCookiePatterns:
    def test_chromium_patterns_returns_list(self):
        from sftui.api import _chromium_cookie_patterns

        result = _chromium_cookie_patterns()
        assert isinstance(result, list)
        assert len(result) >= 4

    def test_firefox_patterns_returns_list(self):
        from sftui.api import _firefox_cookie_patterns

        result = _firefox_cookie_patterns()
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_chromium_darwin(self, monkeypatch):
        from sftui.api import _chromium_cookie_patterns

        monkeypatch.setattr("sys.platform", "darwin")
        result = _chromium_cookie_patterns()
        assert any("Library" in p for _, _, pats in result for p in pats)

    def test_chromium_win32(self, monkeypatch):
        from sftui.api import _chromium_cookie_patterns

        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\test\\AppData\\Local")
        result = _chromium_cookie_patterns()
        assert any("Google" in p for _, _, pats in result for p in pats)

    def test_chromium_linux(self, monkeypatch):
        from sftui.api import _chromium_cookie_patterns

        monkeypatch.setattr("sys.platform", "linux")
        result = _chromium_cookie_patterns()
        assert any("google-chrome" in p for _, _, pats in result for p in pats)

    def test_firefox_darwin(self, monkeypatch):
        from sftui.api import _firefox_cookie_patterns

        monkeypatch.setattr("sys.platform", "darwin")
        result = _firefox_cookie_patterns()
        assert any("Firefox" in p for _, _, pats in result for p in pats)

    def test_firefox_win32(self, monkeypatch):
        from sftui.api import _firefox_cookie_patterns

        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setenv("APPDATA", "C:\\Users\\test\\AppData\\Roaming")
        result = _firefox_cookie_patterns()
        assert any("Mozilla" in p for _, _, pats in result for p in pats)

    def test_firefox_linux(self, monkeypatch):
        from sftui.api import _firefox_cookie_patterns

        monkeypatch.setattr("sys.platform", "linux")
        result = _firefox_cookie_patterns()
        assert any("mozilla" in p for _, _, pats in result for p in pats)


class TestFindCookieProfiles:
    def test_returns_list(self):
        from sftui.api import find_cookie_profiles

        assert isinstance(find_cookie_profiles(), list)

    def test_dedup(self, tmp_path, monkeypatch):
        from sftui import api as api_mod

        cookie_file = tmp_path / "profile" / "Cookies"
        cookie_file.parent.mkdir()
        cookie_file.touch()

        monkeypatch.setattr(
            api_mod, "_chromium_cookie_patterns",
            lambda: [("Chrome", "chrome", [str(cookie_file)]), ("Chrome", "chrome", [str(cookie_file)])],
        )
        monkeypatch.setattr(api_mod, "_firefox_cookie_patterns", lambda: [])
        profiles = api_mod.find_cookie_profiles()
        assert len(profiles) == 1


class TestLoadCookies:
    def _fake_bc3(self, monkeypatch, cookies):
        class FakeCookie:
            def __init__(self, name, value, domain):
                self.name, self.value, self.domain = name, value, domain

        class FakeJar:
            def __iter__(self_):
                return iter(cookies)

        fake_bc3 = types.ModuleType("browser_cookie3")
        fake_bc3.chrome = lambda cookie_file=None: FakeJar()
        fake_bc3.firefox = lambda cookie_file=None: FakeJar()
        monkeypatch.setitem(sys.modules, "browser_cookie3", fake_bc3)

    def test_filters_by_domain(self, monkeypatch):
        from sftui import api as api_mod

        class C:
            def __init__(self, name, value, domain):
                self.name, self.value, self.domain = name, value, domain

        self._fake_bc3(monkeypatch, [
            C("session", "abc", ".teachx.ai"),
            C("other", "xyz", ".google.com"),
            C("tracker", None, ".teachx.ai"),
        ])
        result = api_mod._load_cookies("chrome", "/fake/path")
        assert result == {"session": "abc"}

    def test_no_cookie_file(self, monkeypatch):
        from sftui import api as api_mod

        class C:
            def __init__(self, name, value, domain):
                self.name, self.value, self.domain = name, value, domain

        self._fake_bc3(monkeypatch, [C("tok", "val", "starfleet-backend.teachx.ai")])
        result = api_mod._load_cookies("firefox")
        assert result == {"tok": "val"}


class TestResolveCookies:
    def test_cli_flag(self, monkeypatch):
        from sftui import api as api_mod

        monkeypatch.setattr(api_mod, "_load_cookies", lambda fn, cf=None: {"a": "1"})
        result = api_mod.resolve_cookies("/fake/cookies", verbose=True)
        assert result == {"a": "1"}

    def test_saved_path(self, tmp_path, monkeypatch):
        from sftui import api as api_mod
        from sftui import config

        cookie_path = tmp_path / "Cookies"
        cookie_path.touch()
        config.save_config({"browser": "firefox", "cookie_file": str(cookie_path)})
        monkeypatch.setattr(api_mod, "_load_cookies", lambda fn, cf=None: {"b": "2"})
        result = api_mod.resolve_cookies(None, verbose=True)
        assert result == {"b": "2"}

    def test_fallback_interactive(self, monkeypatch):
        from sftui import api as api_mod
        from sftui.models import CookieProfile

        monkeypatch.setattr(
            api_mod, "interactive_cookie_setup",
            lambda: CookieProfile("/fake", "Chrome", "chrome"),
        )
        monkeypatch.setattr(api_mod, "_load_cookies", lambda fn, cf=None: {"c": "3"})
        result = api_mod.resolve_cookies(None)
        assert result == {"c": "3"}


class TestInteractiveCookieSetup:
    def test_no_profiles_exits(self, monkeypatch):
        from sftui import api as api_mod

        monkeypatch.setattr(api_mod, "find_cookie_profiles", lambda: [])
        with pytest.raises(SystemExit):
            api_mod.interactive_cookie_setup()

    def test_selects_profile(self, monkeypatch):
        from sftui import api as api_mod
        from sftui.models import CookieProfile

        profiles = [
            CookieProfile("/p1", "Chrome - Default", "chrome"),
            CookieProfile("/p2", "Firefox - default", "firefox"),
        ]
        monkeypatch.setattr(api_mod, "find_cookie_profiles", lambda: profiles)
        monkeypatch.setattr("builtins.input", lambda prompt: "1")
        monkeypatch.setattr(api_mod, "_load_cookies", lambda fn, cf=None: {"s": "v"})
        result = api_mod.interactive_cookie_setup()
        assert result == profiles[0]

    def test_validation_failure_still_saves(self, monkeypatch):
        from sftui import api as api_mod
        from sftui.models import CookieProfile

        profiles = [CookieProfile("/p1", "Chrome", "chrome")]
        monkeypatch.setattr(api_mod, "find_cookie_profiles", lambda: profiles)
        monkeypatch.setattr("builtins.input", lambda prompt: "1")
        monkeypatch.setattr(
            api_mod, "_load_cookies",
            lambda fn, cf=None: (_ for _ in ()).throw(Exception("fail")),
        )
        result = api_mod.interactive_cookie_setup()
        assert result == profiles[0]

    def test_invalid_then_valid_choice(self, monkeypatch):
        from sftui import api as api_mod
        from sftui.models import CookieProfile

        profiles = [CookieProfile("/p1", "Chrome", "chrome")]
        monkeypatch.setattr(api_mod, "find_cookie_profiles", lambda: profiles)
        inputs = iter(["abc", "99", "1"])
        monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
        monkeypatch.setattr(api_mod, "_load_cookies", lambda fn, cf=None: {"s": "v"})
        result = api_mod.interactive_cookie_setup()
        assert result == profiles[0]

    def test_empty_cookies_warning(self, monkeypatch, capsys):
        from sftui import api as api_mod
        from sftui.models import CookieProfile

        profiles = [CookieProfile("/p1", "Chrome", "chrome")]
        monkeypatch.setattr(api_mod, "find_cookie_profiles", lambda: profiles)
        monkeypatch.setattr("builtins.input", lambda prompt: "1")
        monkeypatch.setattr(api_mod, "_load_cookies", lambda fn, cf=None: {})
        api_mod.interactive_cookie_setup()
        assert "no starfleet cookies" in capsys.readouterr().out
