"""Command palette provider for the Starfleet TUI."""

from __future__ import annotations

from textual.command import DiscoveryHit, Hit, Hits, Provider


class NavigationProvider(Provider):
    """Command palette provider for view navigation and theme switching."""

    async def discover(self) -> Hits:
        from sfctl.app import StarfleetApp

        app = self.app
        if not isinstance(app, StarfleetApp):
            return
        for label, section_id in app.nav_items():
            yield DiscoveryHit(label, lambda sid=section_id: app.run_worker(app.go_to(sid)), label)
        for label, mi, fn in app.diff_items():
            yield DiscoveryHit(label, lambda m=mi, f=fn: app.run_worker(app.go_to_diff(m, f)), label)
        for action_label, action_cb in [
            ("Justification: Edit", lambda: app.run_worker(app.action_edit_justification())),
            ("Refresh Data", lambda: app.action_refresh_data()),
            ("Reset: Clear Local Scores & Justification", lambda: app.action_reset_local()),
            ("Help: Show Shortcuts", lambda: app.action_help()),
        ]:
            yield DiscoveryHit(action_label, action_cb, action_label)

    async def search(self, query: str) -> Hits:
        from sfctl.app import StarfleetApp

        app = self.app
        if not isinstance(app, StarfleetApp):
            return

        matcher = self.matcher(query)

        for label, section_id in app.nav_items():
            score = matcher.match(label)
            if score > 0:
                yield Hit(score, label, lambda sid=section_id: app.run_worker(app.go_to(sid)), label)

        for label, mi, fn in app.diff_items():
            score = matcher.match(label)
            if score > 0:
                yield Hit(score, label, lambda m=mi, f=fn: app.run_worker(app.go_to_diff(m, f)), label)

        for action_label, action_cb in [
            ("Justification: Edit", lambda: app.run_worker(app.action_edit_justification())),
            ("Refresh Data", lambda: app.action_refresh_data()),
            ("Reset: Clear Local Scores & Justification", lambda: app.action_reset_local()),
            ("Help: Show Shortcuts", lambda: app.action_help()),
        ]:
            score = matcher.match(action_label)
            if score > 0:
                yield Hit(score, action_label, action_cb, action_label)

        for theme_name in app.available_themes:
            label = f"Theme: {theme_name}"
            score = matcher.match(label)
            if score > 0:
                yield Hit(score, label, lambda t=theme_name: app.set_theme(t), label)
