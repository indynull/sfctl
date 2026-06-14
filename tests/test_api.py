"""Tests for API client, cookie management, and domain matching."""

from __future__ import annotations

import types
from pathlib import Path

import httpx
import pytest

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "task_sample.json"
TASK_ID = "t-EXAMPLE001"


class TestExtractTaskId:
    def test_bare_id(self):
        from sfctl.api import extract_task_id

        assert extract_task_id(TASK_ID) == TASK_ID

    def test_full_url(self):
        from sfctl.api import extract_task_id

        url = f"https://starfleet.teachx.ai/tasks/{TASK_ID}"
        assert extract_task_id(url) == TASK_ID

    def test_invalid(self):
        from sfctl.api import extract_task_id

        with pytest.raises(SystemExit):
            extract_task_id("no-task-here")


class TestDomainMatching:
    def test_exact_match(self):
        from sfctl.api import _domain_matches

        assert _domain_matches("starfleet-backend.teachx.ai", "starfleet-backend.teachx.ai")

    def test_dot_prefix_match(self):
        from sfctl.api import _domain_matches

        assert _domain_matches(".teachx.ai", "starfleet-backend.teachx.ai")

    def test_dot_prefix_exact_without_dot(self):
        from sfctl.api import _domain_matches

        assert _domain_matches(".teachx.ai", "teachx.ai")

    def test_no_match_different_subdomain(self):
        from sfctl.api import _domain_matches

        assert not _domain_matches("autoqa.teachx.ai", "starfleet-backend.teachx.ai")

    def test_no_match_different_domain(self):
        from sfctl.api import _domain_matches

        assert not _domain_matches(".google.com", "starfleet-backend.teachx.ai")


class TestCheckResponse:
    def test_401_raises_access_error(self):
        from sfctl.api import AccessError, _check_response

        resp = httpx.Response(401, request=httpx.Request("GET", "http://x"))
        with pytest.raises(AccessError, match="Not authorized"):
            _check_response(resp, "Test")

    def test_403_raises_auth_error(self):
        from sfctl.api import AuthError, _check_response

        resp = httpx.Response(403, request=httpx.Request("GET", "http://x"))
        with pytest.raises(AuthError):
            _check_response(resp, "Test")

    def test_500_raises_http_error(self):
        from sfctl.api import _check_response

        resp = httpx.Response(500, request=httpx.Request("GET", "http://x"))
        with pytest.raises(httpx.HTTPStatusError):
            _check_response(resp, "Test")

    def test_200_ok(self):
        from sfctl.api import _check_response

        resp = httpx.Response(200, request=httpx.Request("GET", "http://x"))
        _check_response(resp, "Test")


class TestRequestWithRetry:
    @pytest.fixture(autouse=True)
    def _fast_retry(self, monkeypatch):
        """Eliminate retry sleep in tests."""
        import sfctl.api as api_mod

        async def _nosleep(_seconds):
            pass

        monkeypatch.setattr(api_mod.asyncio, "sleep", _nosleep)
    @pytest.mark.asyncio
    async def test_success_first_try(self):
        from sfctl.api import _request_with_retry

        transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True}))
        async with httpx.AsyncClient(transport=transport) as client:
            resp = await _request_with_retry(client, "GET", "http://x/ok", "Test")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_retry_on_502(self):
        from sfctl.api import _request_with_retry

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
    async def test_access_error_no_retry(self):
        from sfctl.api import AccessError, _request_with_retry

        transport = httpx.MockTransport(lambda r: httpx.Response(401, request=r))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(AccessError):
                await _request_with_retry(client, "GET", "http://x/auth", "Test")

    @pytest.mark.asyncio
    async def test_transport_error_retries(self):
        from sfctl.api import _request_with_retry

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
        from sfctl.api import _request_with_retry

        transport = httpx.MockTransport(lambda r: httpx.Response(502, request=r))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await _request_with_retry(client, "GET", "http://x/fail", "Test")

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_transport(self):
        from sfctl.api import _request_with_retry

        async def handler(request):
            raise httpx.ConnectError("down")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx.ConnectError):
                await _request_with_retry(client, "GET", "http://x/fail", "Test")

    @pytest.mark.asyncio
    async def test_502_then_401(self):
        from sfctl.api import AccessError, _request_with_retry

        call_count = 0

        async def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(502, request=request)
            return httpx.Response(401, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(AccessError):
                await _request_with_retry(client, "GET", "http://x", "Test")


class TestTraceUrl:
    def test_standard_trace_ref(self):
        from sfctl.api import _trace_url

        url = _trace_url(
            "https://starfleet-backend.teachx.ai",
            "coding-question/prod-hades-worker-abc/hds-123/trace.json",
        )
        assert url == "https://starfleet-backend.teachx.ai/coding-question/trace/prod-hades-worker-abc%2Fhds-123"

    def test_no_prefix(self):
        from sfctl.api import _trace_url

        url = _trace_url("https://api.example.com", "worker-x/sess-y/trace.json")
        assert url == "https://api.example.com/coding-question/trace/worker-x%2Fsess-y"

    def test_bare_path(self):
        from sfctl.api import _trace_url

        url = _trace_url("https://api.example.com", "coding-question/w1/s1")
        assert url == "https://api.example.com/coding-question/trace/w1%2Fs1"


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

        import sfctl.api as api_mod

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

        import sfctl.api as api_mod

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
        import sfctl.api as api_mod

        async def fake_fetch(task_id, cookies):
            return fixture_data

        monkeypatch.setattr(api_mod, "_fetch_data_async", fake_fetch)
        result = api_mod.fetch_data(TASK_ID, {})
        assert result == fixture_data


class TestCookiePatterns:
    def test_chromium_patterns_returns_list(self):
        from sfctl.api import _chromium_cookie_patterns

        result = _chromium_cookie_patterns()
        assert isinstance(result, list)
        assert len(result) >= 4

    def test_firefox_patterns_returns_list(self):
        from sfctl.api import _firefox_cookie_patterns

        result = _firefox_cookie_patterns()
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_chromium_darwin(self, monkeypatch):
        from sfctl.api import _chromium_cookie_patterns

        monkeypatch.setattr("sys.platform", "darwin")
        result = _chromium_cookie_patterns()
        assert any("Library" in p for _, _, pats in result for p in pats)

    def test_chromium_win32(self, monkeypatch):
        from sfctl.api import _chromium_cookie_patterns

        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\test\\AppData\\Local")
        result = _chromium_cookie_patterns()
        assert any("Google" in p for _, _, pats in result for p in pats)

    def test_chromium_linux(self, monkeypatch):
        from sfctl.api import _chromium_cookie_patterns

        monkeypatch.setattr("sys.platform", "linux")
        result = _chromium_cookie_patterns()
        assert any("google-chrome" in p for _, _, pats in result for p in pats)

    def test_firefox_darwin(self, monkeypatch):
        from sfctl.api import _firefox_cookie_patterns

        monkeypatch.setattr("sys.platform", "darwin")
        result = _firefox_cookie_patterns()
        assert any("Firefox" in p for _, _, pats in result for p in pats)

    def test_firefox_win32(self, monkeypatch):
        from sfctl.api import _firefox_cookie_patterns

        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setenv("APPDATA", "C:\\Users\\test\\AppData\\Roaming")
        result = _firefox_cookie_patterns()
        assert any("Mozilla" in p for _, _, pats in result for p in pats)

    def test_firefox_linux(self, monkeypatch):
        from sfctl.api import _firefox_cookie_patterns

        monkeypatch.setattr("sys.platform", "linux")
        result = _firefox_cookie_patterns()
        assert any("mozilla" in p for _, _, pats in result for p in pats)


class TestFindCookieProfiles:
    def test_returns_list(self):
        from sfctl.api import find_cookie_profiles

        assert isinstance(find_cookie_profiles(), list)

    def test_dedup(self, tmp_path, monkeypatch):
        from sfctl import api as api_mod

        cookie_file = tmp_path / "profile" / "Cookies"
        cookie_file.parent.mkdir()
        cookie_file.touch()

        monkeypatch.setattr(
            api_mod,
            "_chromium_cookie_patterns",
            lambda: [
                ("Chrome", "chrome", [str(cookie_file)]),
                ("Chrome", "chrome", [str(cookie_file)]),
            ],
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
        monkeypatch.setattr("sfctl.api.browser_cookie3", fake_bc3)

    def test_filters_by_domain(self, monkeypatch):
        from sfctl import api as api_mod

        class C:
            def __init__(self, name, value, domain):
                self.name, self.value, self.domain = name, value, domain

        self._fake_bc3(
            monkeypatch,
            [
                C("session", "abc", ".teachx.ai"),
                C("other", "xyz", ".google.com"),
                C("tracker", None, ".teachx.ai"),
            ],
        )
        result = api_mod._load_cookies("chrome", "/fake/path")
        assert result == {"session": "abc"}

    def test_no_cookie_file(self, monkeypatch):
        from sfctl import api as api_mod

        class C:
            def __init__(self, name, value, domain):
                self.name, self.value, self.domain = name, value, domain

        self._fake_bc3(monkeypatch, [C("tok", "val", "starfleet-backend.teachx.ai")])
        result = api_mod._load_cookies("firefox")
        assert result == {"tok": "val"}


class TestResolveCookies:
    def test_env_token(self, monkeypatch):
        from sfctl import api as api_mod

        monkeypatch.setenv("STARFLEET_ACCESS_TOKEN", "my-secret-token")
        cookies, is_token = api_mod.resolve_cookies(None)
        assert cookies == {"accessToken": "my-secret-token"}
        assert is_token is True

    def test_env_token_verbose(self, monkeypatch, capsys):
        from sfctl import api as api_mod

        monkeypatch.setenv("STARFLEET_ACCESS_TOKEN", "tok123")
        cookies, is_token = api_mod.resolve_cookies(None, verbose=True)
        assert cookies == {"accessToken": "tok123"}
        assert is_token is True
        assert "STARFLEET_ACCESS_TOKEN" in capsys.readouterr().out

    def test_env_token_overrides_cli_flag(self, monkeypatch):
        from sfctl import api as api_mod

        monkeypatch.setenv("STARFLEET_ACCESS_TOKEN", "env-token")
        monkeypatch.setattr(api_mod, "_load_cookies", lambda fn, cf=None: {"a": "1"})
        cookies, is_token = api_mod.resolve_cookies("/fake/cookies")
        assert cookies == {"accessToken": "env-token"}
        assert is_token is True

    def test_token_arg(self, monkeypatch, tmp_path):
        from sfctl import api as api_mod
        from sfctl import config

        monkeypatch.setattr(config, "_config_path", lambda: tmp_path / "config.json")
        cookies, is_token = api_mod.resolve_cookies(None, token_arg="cli-token")
        assert cookies == {"accessToken": "cli-token"}
        assert is_token is True

    def test_token_arg_saved_to_config(self, monkeypatch, tmp_path):
        from sfctl import api as api_mod
        from sfctl import config

        cfg_path = tmp_path / "config.json"
        monkeypatch.setattr(config, "_config_path", lambda: cfg_path)
        api_mod.resolve_cookies(None, token_arg="saved-tok")
        assert config.load_config().get("access_token") == "saved-tok"

    def test_token_arg_verbose(self, monkeypatch, capsys, tmp_path):
        from sfctl import api as api_mod
        from sfctl import config

        monkeypatch.setattr(config, "_config_path", lambda: tmp_path / "config.json")
        cookies, is_token = api_mod.resolve_cookies(None, verbose=True, token_arg="v-tok")
        assert cookies == {"accessToken": "v-tok"}
        assert is_token is True
        assert "--token" in capsys.readouterr().out

    def test_env_token_overrides_token_arg(self, monkeypatch):
        from sfctl import api as api_mod

        monkeypatch.setenv("STARFLEET_ACCESS_TOKEN", "env-wins")
        cookies, is_token = api_mod.resolve_cookies(None, token_arg="cli-loses")
        assert cookies == {"accessToken": "env-wins"}
        assert is_token is True

    def test_saved_token_in_config(self, monkeypatch, tmp_path):
        from sfctl import api as api_mod
        from sfctl import config

        monkeypatch.setattr(config, "_config_path", lambda: tmp_path / "config.json")
        config.save_config({"access_token": "from-config"})
        cookies, is_token = api_mod.resolve_cookies(None)
        assert cookies == {"accessToken": "from-config"}
        assert is_token is True

    def test_cli_flag(self, monkeypatch):
        from sfctl import api as api_mod

        monkeypatch.setattr(api_mod, "_load_cookies", lambda fn, cf=None: {"a": "1"})
        cookies, is_token = api_mod.resolve_cookies("/fake/cookies", verbose=True)
        assert cookies == {"a": "1"}
        assert is_token is False

    def test_saved_path(self, tmp_path, monkeypatch):
        from sfctl import api as api_mod
        from sfctl import config

        cookie_path = tmp_path / "Cookies"
        cookie_path.touch()
        config.save_config({"browser": "firefox", "cookie_file": str(cookie_path)})
        monkeypatch.setattr(api_mod, "_load_cookies", lambda fn, cf=None: {"b": "2"})
        cookies, is_token = api_mod.resolve_cookies(None, verbose=True)
        assert cookies == {"b": "2"}
        assert is_token is False

    def test_fallback_interactive(self, monkeypatch):
        from sfctl import api as api_mod
        from sfctl.models import CookieProfile

        monkeypatch.setattr(
            api_mod,
            "interactive_cookie_setup",
            lambda: CookieProfile("/fake", "Chrome", "chrome"),
        )
        monkeypatch.setattr(api_mod, "_load_cookies", lambda fn, cf=None: {"c": "3"})
        cookies, is_token = api_mod.resolve_cookies(None)
        assert cookies == {"c": "3"}
        assert is_token is False


class TestInteractiveCookieSetup:
    def test_no_profiles_exits(self, monkeypatch):
        from sfctl import api as api_mod

        monkeypatch.setattr(api_mod, "find_cookie_profiles", lambda: [])
        with pytest.raises(SystemExit):
            api_mod.interactive_cookie_setup()

    def test_selects_profile(self, monkeypatch):
        from sfctl import api as api_mod
        from sfctl.models import CookieProfile

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
        from sfctl import api as api_mod
        from sfctl.models import CookieProfile

        profiles = [CookieProfile("/p1", "Chrome", "chrome")]
        monkeypatch.setattr(api_mod, "find_cookie_profiles", lambda: profiles)
        monkeypatch.setattr("builtins.input", lambda prompt: "1")
        monkeypatch.setattr(
            api_mod,
            "_load_cookies",
            lambda fn, cf=None: (_ for _ in ()).throw(Exception("fail")),
        )
        result = api_mod.interactive_cookie_setup()
        assert result == profiles[0]

    def test_invalid_then_valid_choice(self, monkeypatch):
        from sfctl import api as api_mod
        from sfctl.models import CookieProfile

        profiles = [CookieProfile("/p1", "Chrome", "chrome")]
        monkeypatch.setattr(api_mod, "find_cookie_profiles", lambda: profiles)
        inputs = iter(["abc", "99", "1"])
        monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
        monkeypatch.setattr(api_mod, "_load_cookies", lambda fn, cf=None: {"s": "v"})
        result = api_mod.interactive_cookie_setup()
        assert result == profiles[0]

    def test_empty_cookies_warning(self, monkeypatch, capsys):
        from sfctl import api as api_mod
        from sfctl.models import CookieProfile

        profiles = [CookieProfile("/p1", "Chrome", "chrome")]
        monkeypatch.setattr(api_mod, "find_cookie_profiles", lambda: profiles)
        monkeypatch.setattr("builtins.input", lambda prompt: "1")
        monkeypatch.setattr(api_mod, "_load_cookies", lambda fn, cf=None: {})
        api_mod.interactive_cookie_setup()
        assert "no starfleet cookies" in capsys.readouterr().out
