"""Command palette provider for the Starfleet TUI."""

from __future__ import annotations

from textual.command import DiscoveryHit, Hit, Hits, Provider


class NavigationProvider(Provider):
    """Command palette provider for view navigation and theme switching."""

    def _items(self):
        from sfctl.app import StarfleetApp

        app = self.app
        if not isinstance(app, StarfleetApp):
            return []
        items: list[tuple[str, object]] = []
        for label, section_id in app.nav_items():
            items.append((label, lambda sid=section_id: app.run_worker(app.go_to(sid))))
        for label, mi, fn in app.diff_items():
            items.append((label, lambda m=mi, f=fn: app.run_worker(app.go_to_diff(m, f))))
        items.extend(
            [
                ("Justification: Edit", lambda: app.run_worker(app.action_edit_justification())),
                ("Refresh Data", lambda: app.action_refresh_data()),
                ("Reset: Clear Local Scores & Justification", lambda: app.action_reset_local()),
                ("View: Toggle Split (side-by-side)", lambda: app.run_worker(app.action_split_view())),
                ("View: Toggle Maximize", lambda: app.action_toggle_maximize()),
                ("View: Toggle Translate", lambda: app.action_translate()),
                ("Help: Show Shortcuts", lambda: app.action_help()),
                (
                    "View: 80-col terminal preview (response)",
                    lambda: app.action_toggle_response_width(),
                ),
            ]
        )
        for theme_name in app.available_themes:
            items.append((f"Theme: {theme_name}", lambda t=theme_name: app.set_theme(t)))
        return items

    async def discover(self) -> Hits:
        for label, cb in self._items():
            yield DiscoveryHit(label, cb, label)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for label, cb in self._items():
            score = matcher.match(label)
            if score > 0:
                yield Hit(score, matcher.highlight(label), cb, label)
