"""Voting controller — manages annotations, scores, and vote UI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import Horizontal
from textual.widgets import Button, Static

from sfctl.constants import ARROW_DOWN, ARROW_UP
from sfctl.ids import (
    Context,
    model_id,
    model_letter,
    model_tabs_id,
    tab_diffs_id,
    tab_response_id,
    tab_trace_id,
    vote_down_id,
    vote_label_id,
    vote_up_id,
)
from sfctl.models import Annotation

if TYPE_CHECKING:
    from sfctl.app import StarfleetApp


class VotingController:
    """Composition helper that owns vote state and vote UI updates."""

    def __init__(self, app: StarfleetApp) -> None:
        self._app = app

    def vote_bar(self, idx: int, context: str) -> Horizontal:
        """Small inline up/down vote buttons for a model tab."""
        s = self._app.scores[idx]
        score = getattr(s, context, 0)
        sign = f"+{score}" if score > 0 else str(score)
        return Horizontal(
            Button(f"{ARROW_UP}", id=vote_up_id(idx, context), classes="vote-btn"),
            Static(sign, classes="vote-score", id=vote_label_id(idx, context)),
            Button(f"{ARROW_DOWN}", id=vote_down_id(idx, context), classes="vote-btn"),
            classes="vote-bar",
        )

    def refresh_vote_labels(self, idx: int) -> None:
        if idx >= len(self._app.scores):
            return
        s = self._app.scores[idx]
        for context in Context:
            label = self._app.query_one_optional(f"#{vote_label_id(idx, context)}", Static)
            if label:
                score = getattr(s, context, 0)
                sign = f"+{score}" if score > 0 else str(score)
                label.update(sign)

    def handle_button(self, btn_id: str) -> None:
        """Handle a vote button press by its widget ID."""
        if not btn_id.startswith("vote-"):
            return
        parts = btn_id.split("-")
        if len(parts) < 4:
            return
        direction = parts[1]
        idx = int(parts[2])
        context = "-".join(parts[3:])
        delta = 1 if direction == "up" else -1
        self.apply_vote(idx, context, delta)

    def detect_vote_context(self) -> str:
        mid = model_id(self._app.current_model_index)
        try:
            from textual.widgets import TabbedContent
            tabs = self._app.query_one(f"#{model_tabs_id(mid)}", TabbedContent)
            active = tabs.active
            if active == tab_response_id(mid):
                return Context.RESPONSE
            if active in (tab_trace_id(mid), tab_diffs_id(mid)):
                return Context.CODE
        except Exception:
            pass
        return Context.OVERALL

    def apply_vote(self, idx: int, context: str, delta: int) -> None:
        annotation = Annotation(context=context, sentiment=delta)
        self.add_annotation(idx, annotation)
        score = getattr(self._app.scores[idx], context)
        sign = f"+{score}" if score > 0 else str(score)
        arrow = ARROW_UP if delta > 0 else ARROW_DOWN
        color = "green" if delta > 0 else "red"
        self._app._status(f"[{color}]{arrow}[/] {model_letter(idx)} {context}: {sign}")

    def vote(self, delta: int) -> None:
        idx = self._app.current_model_index
        if not self._app._is_on_model_view() or idx >= len(self._app.models):
            return
        self.apply_vote(idx, context=self.detect_vote_context(), delta=delta)

    def add_annotation(self, model_index: int, annotation: Annotation) -> None:
        """Append an annotation for a model, persist, and refresh UI."""
        self._app.review.add_annotation(model_index, annotation)
        self._app.scores = self._app.review.scores
