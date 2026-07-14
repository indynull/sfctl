# AGENTS.md -- sfctl (Starfleet Control)

Starfleet Control (`sfctl`) is a [Textual](https://github.com/Textualize/textual)
TUI for reviewing model outputs across task types: code review (3-model ranking),
project proposals, and response comparisons (clarity). Python 3.12+.

This file is the contract for humans and coding agents working in the repo.

---

## 1. Quick start

```bash
uv sync
uv run sfctl <task-id-or-fixture.json>
uv run pytest tests/ -q
uv run ruff check sfctl/
```

Install as a CLI tool: `uv tool install .` (use `--force` to update).

Prefer **`uv run ...`** for all project tools so the environment matches the
lockfile. Do not use bare `python`, `pytest`, or `ruff`.

---

## 2. Agent / commit hygiene

1. **`uv run python -c "from sfctl.app import StarfleetApp"`** must pass before
   committing (smoke import check).
2. Commit with a clear imperative message (lowercase, no period).
3. One logical change per commit when practical.
4. GPG signing is disabled: `git -c commit.gpgsign=false commit ...`
5. Do not leave unstaged related edits after claiming work is done.
6. No emojis in code, comments, or commit messages.

---

## 3. Architecture

```
sfctl/
  app.py            # Textual App -- main TUI, bindings, compose, navigation
  app.tcss          # Layout, focus, panel styles (Textual CSS)
  cli.py            # CLI entry point (print() allowed here only)
  ids.py            # Widget IDs, CSS classes, enum constants
  commands.py       # Ctrl+P command palette provider
  models.py         # Dataclasses (ModelData, ParsedContent, ClarityData, ...)
  task_types.py     # TaskType enum + detect_task_type()
  handlers/
    __init__.py     # handler_for_type() factory
    base.py         # TaskHandler ABC -- interface for task-type-specific behavior
    ranking.py      # RankingHandler (code_review: 3 models, vote bars, diffs)
    proposal.py     # ProposalHandler (project proposals: rubrics, issues, trace)
    comparison.py   # ComparisonHandler (response comparison: error tags, preference)
  clarity.py        # Parser for response-comparison tasks
  diff.py           # Diff parsing + content extraction
  proposal.py       # Parser for project-proposal tasks
  analysis.py       # Rule-based analysis engine (signals, flags)
  ranking.py        # Model summary text helpers
  scoring.py        # Score computation
  history.py        # History entry diffing
  formatting.py     # Timestamp, duration, sanitize helpers
  fuzzy.py          # Fuzzy matching for search modals
  config.py         # ~/.sfctl/config.json read/write
  session.py        # Local session history tracking
  api.py            # HTTP API client (httpx)
  clusters.py       # Reviewer clustering / inter-rater analysis
  constants.py      # Shared constants (EM_DASH, etc.)
  screens.py        # Modal screens (help, search, analysis, yank, comments)
  widgets.py        # Custom widgets (DiffDisplay, LazyCollapsible, SplitHandle)
  search.py         # SearchController (diff/event search, grep navigation)
  editor.py         # EditorController (justification, comments, clipboard)
  voting.py         # VotingController (vote bars, score management)
```

### Data flow

`models -> parsers (clarity/diff/proposal) -> handlers -> app.py`. Screens and
controllers delegate to app; no file I/O or JSON parsing in screen/controller code.

### Task-type handlers

`app.py` delegates type-specific behavior to a `TaskHandler` subclass (one per task
type) via `self.handler`. The app owns widget lifecycle, navigation, and bindings.
The handler owns parsing, content rendering, history diffing, and scoreboard text.

To add a new task type: create a handler in `sfctl/handlers/`, a parser module,
add a `TaskType` enum value + detection rule, and register in `handler_for_type()`.
No changes to `app.py` are needed.

### Composition controllers

`app.py` delegates to three controllers to keep the class manageable:

| Controller | File | Owns |
|------------|------|------|
| `VotingController` | `voting.py` | Vote bars, score changes, annotations |
| `SearchController` | `search.py` | Diff/event search modals, grep navigation |
| `EditorController` | `editor.py` | Justification, comments, clipboard |

Controllers receive `self` (the app) and call `self._app.*` for widget access.
They use `self._app._status(msg)` for user feedback (not `notify()`).

---

## 4. Code conventions

### 4.1 Style

- `snake_case` functions/variables, `PascalCase` classes, `UPPER_SNAKE` constants.
- `from __future__ import annotations` in every module.
- Type-annotate public signatures. Use `X | None`, lowercase generics.
- No `print()` except in `cli.py`.
- Initialise all instance attributes in `__init__`.
- No section divider comments (`# --- Section ---`).
- No emojis anywhere.
- Remove dead code; prefer delete over "for later" stubs.

### 4.2 Comments and prose (no process leakage)

**Ship only the product.** Comments, docstrings, and commit messages must
describe what the code is and does now -- not how an agent or human got there.

**Never write** (in code comments or docstrings):

- Design-process narration: "we decided", "I noticed", "for now", "temporary",
  "quick fix", "hack", "TODO(agent)", "until we refactor".
- History / migration chat: "was X", "formerly Y", "moved from", "used to be",
  "instead of the old", "replaces the previous".
- Agent self-talk or meta rules pasted into sources.
- Apologies, debates, or alternatives rejected.

**Do write** (when a comment earns its lines):

- Invariants and contracts: input shape, failure modes, ordering guarantees.
- Non-obvious *why* tied to behaviour.
- Pointers to the owning module for cross-layer calls.

If you are tempted to explain a *change*, put that in the **git commit
message** -- not in the file. After edits, re-read comments on touched lines
and strip any process residue before finishing.

### 4.3 Module purity

A module's top-level contents must match its job. Do not park unrelated
helpers in the nearest open file.

| Module | Allowed | Forbidden |
|--------|---------|-----------|
| `models.py` | Types, dataclasses, Pydantic models | Formatting, I/O, presentation |
| `app.py` | TUI App, bindings, compose, navigation | Trace JSON parsing, API calls |
| `screens.py` | Modal screens, search modals | Domain logic, data parsing |
| `widgets.py` | Custom Textual widgets | Business logic, file I/O |
| `formatting.py` | Pure text helpers (timestamps, sanitize) | Widget creation, API calls |
| `handlers/*.py` | Task-type-specific parsing, rendering, history | Direct widget queries outside populate methods |
| Controllers (`voting.py`, `search.py`, `editor.py`) | Delegation logic for their concern | Direct JSON parsing, file I/O |

### 4.4 Imports

Module-level imports at the top (stdlib, third-party, local), after
`from __future__ import annotations`.

- Use `TYPE_CHECKING` for type-only imports to break cycles.
- Function-level imports allowed for heavy optional deps (`deep_translator`,
  `openpyxl`, `redlines`) and deferred analysis imports.

### 4.5 Error handling

- Catch the **narrowest** exception type the code actually handles.
- **Never** `except Exception: pass` around logic that must succeed for the
  product to work (table population, data loading, core render paths).
  Prefer **no try** so bugs fail loudly in tests and at the terminal.
- TUI **event handlers** (`on_*`, key actions) may catch broadly so one bad
  keypress does not kill the process -- but should log or surface the error,
  not silently produce empty UI that looks successful.
- Widget query helpers (`_swap_widget`, `_update_split_focus`, etc.) catch
  `Exception` because widgets may not exist in all task types -- this is
  expected, not a bug to surface.
- Optional I/O and best-effort chrome (status bar text, config writes) may
  use a narrow try.
- Worker threads (`@work`) that update the UI: on failure, notify the user
  via `_status()`; do not return as if nothing happened.

### 4.6 Ruff

Configured in `pyproject.toml`. Line length 100. Target Python 3.12.
Selected rules: E, F, W, I, UP, B, SIM, RUF.

---

## 5. Task types

| Type | Enum | Models | Features |
|------|------|--------|----------|
| Code review | `CODE_REVIEW` | 3 (A/B/C) | Diffs, traces, vote bars, ranking |
| Project proposal | `PROJECT_PROPOSAL` | 1 | Rubrics, issues, trace, diffs |
| Response comparison | `RESPONSE_COMPARISON` | 2 (A/B) | Error tags, preference, rationale, conversation history |

Detection logic in `task_types.py`. Parsing in `diff.py`, `proposal.py`,
`clarity.py` respectively. TUI behavior in `handlers/ranking.py`,
`handlers/proposal.py`, `handlers/comparison.py`.

---

## 6. TUI and keyboard UX

Keyboard-first. Every feature must be reachable with keys and/or Ctrl+P.

### 6.1 Bindings

Defined in `app.py` `BINDINGS`. Use `check_action()` to context-gate:
return `False` to hide from Footer when not applicable.

| Key | Action | Context |
|-----|--------|---------|
| `0` | Overview | Always (in unified: focus overview section) |
| `1`/`2`/`3` | Model A/B/C | Code review / comparison (in unified: focus column) |
| `m` | Model | Proposals (in unified: focus model column) |
| `u` | Toggle split (unified) view | All task types |
| `t` | Toggle translate (system locale) | Always (global: prompt + responses + context) |
| `a` | Analysis modal | Always |
| `e` | Toggle expand/collapse | Model or overview view |
| `f` | Toggle maximize/restore pane | Any view |
| `+`/`-` | Vote up/down | Model view (code review only) |
| `ctrl+f` | Search diffs | Model view |
| `ctrl+g` | Search events | Model view |
| `tab`/`shift+tab` | Next/prev tab | Any tabbed view (including unified overview) |
| `?` | Help modal | Always |

### 6.2 Focus model

| Input | Role |
|-------|------|
| **Tab / Shift+Tab** | Cycle tabs within the active `TabbedContent` |
| **0** / **1-3** / **m** | Switch focus between overview and model panes |
| **Arrow keys**, PgUp/PgDn | Scroll inside focused `ScrollableContainer` |
| **Enter / Space** | Activate control or selected item |
| **Esc** | Dismiss modal |

- Focus order follows `compose()` DOM order.
- After switching panes, `.focus()` the target scroll container so keyboard
  scrolling works immediately.
- Bindings that need context (e.g. vote requires model view) use `check_action`
  and `refresh_bindings()` -- return `False` to hide from Footer when inert.

### 6.3 Unified view

The unified (split) view shows all models side-by-side with an overview section
below. Works for all task types (code review, proposals, comparisons).

- Each column is an independently scrollable `ScrollableContainer`.
- For ranking jobs, each column has its own `TabbedContent` (Response/Trace/Diffs).
- Trace, diffs, history, and context tabs are **deferred** -- content mounts on
  first tab activation via `_deferred_tabs`.
- `_split_focus` tracks which column is active (-1 = overview, 0+ = model index).
- `_split_model_count()` returns the correct column count per task type.
- The `.split-active` CSS class marks the focused column with a top outline.
- Navigation stays within the unified view; `u` toggles back to individual views.
- The overview area has its own `TabbedContent` with Current + history tabs.

### 6.4 Command palette

`commands.py` `NavigationProvider` provides Ctrl+P entries. Add new actions
here when adding major features.

### 6.5 Status bar

Use `self._status(msg)` (or `self.call_from_thread(self._status, msg)` from
threaded workers) instead of `self.notify()`. The status bar is a single-line
`Static` widget above the Footer that auto-clears after 4 seconds.

### 6.6 Translation

Global toggle (`t`) translates all translatable content to the system locale
(prompt, model responses, split responses, conversation turns). Uses
`deep-translator` (Google Translate) with paragraph-boundary chunking for the
5000-char limit. The target language is detected via `locale.getdefaultlocale()`.
Translations are cached in `self._translated` (keyed by model index, `"prompt"`,
or `"ctx-N"`). The `_apply_translations()` / `_restore_originals()` methods
handle swapping all widgets.

### 6.7 Keyboard-only checklist (new UI / modals)

- [ ] Primary content gets focus when populated.
- [ ] Multi-tab UI uses `TabbedContent` with `tab`/`shift+tab` cycling.
- [ ] Deferred tabs registered in `_deferred_tabs`; populated in
      `on_tabbed_content_tab_activated`.
- [ ] Contextual actions use `check_action` to hide from Footer when inert.
- [ ] Every button is Tab-reachable or has a binding / palette entry.
- [ ] Modals: Esc dismisses. No mouse-only features.
- [ ] `_active_tabbed_content()` returns the correct TabbedContent for unified
      view (model tabs when focused on model, overview tabs when focused on
      overview).

### 6.8 Performance (deferred loading)

Mount expensive content lazily. The `_deferred_tabs` dict maps tab pane IDs to
tuples describing what to build on first activation:

| Kind | Tuple | Built by |
|------|-------|----------|
| `"trace"` | `("trace", model_idx, vote_bars)` | `_mount_trace_content` |
| `"diffs"` | `("diffs", model_idx, vote_bars)` | `_mount_diffs_content` |
| `"history"` | `("history", orig_idx, changed)` | `_populate_history_entry` |
| `"context"` | `("context",)` | `_populate_context_tab` |
| `"proposal-trace"` | `("proposal-trace",)` | `_mount_trace_content` (proposal) |
| `"proposal-diffs"` | `("proposal-diffs",)` | mounts `LazyCollapsible` for each file |

Use `Static(RichMarkdown(...))` instead of Textual `Markdown(...)` widget for
content display. The Textual `Markdown` widget creates a sub-widget tree per
element; `RichMarkdown` renders as a single Rich renderable.

Analysis (`analyze_task`) runs in a `@work(thread=True)` worker after the UI
renders, so startup is not blocked.

---

## 7. Shared helpers (avoid duplication)

The `TaskHandler` base class (`handlers/base.py`) provides shared behavior that
individual handlers override. The app delegates to `self.handler` for all
type-specific work.

| Handler method | Purpose |
|--------|---------|
| `model_header_label(idx)` | Build `"[bold]A[/bold]  [dim]tag[/dim]"` header |
| `response_source(idx)` | Get model response text |
| `prompt_source()` | Get prompt text |
| `model_count` | Column count for unified view |
| `populate_overview(pane)` | Build the Current overview tab |
| `populate_model(container, idx)` | Build a model view |
| `populate_unified_model(scroll, idx)` | Build a model column in unified view |
| `has_changes(prev, curr)` | Detect history entry changes |
| `history_diff_widgets(prev, curr)` | Build change display widgets |
| `scoreboard_parts()` | Type-specific scoreboard text |

These app methods remain shared across all task types:

| App method | Purpose |
|--------|---------|
| `_build_model_tabs(...)` | Mount Response/Trace/Diffs tabs into any container |
| `_register_history_tabs(tabs, history)` | Create empty history tab shells with deferred content |
| `_populate_history_entry(pane, idx, changed)` | Populate a single history tab on first activation |
| `_swap_widget(widget_id, text)` | Update any Static widget with new `RichMarkdown` content |

When adding new per-model content, add it to the handler's `populate_model` and
`populate_unified_model` methods.

---

## 8. Styling

Use Textual design tokens (`$primary`, `$surface`, `$text`, `$text-muted`,
`$accent`, etc.) so themes stay coherent. All layout in `app.tcss`.

Key CSS classes:

| Class | Purpose |
|-------|---------|
| `.view-header` | Docked top header in model columns |
| `.split-col` | Unified view model column |
| `.split-active` | Active column indicator (top outline, no layout shift) |
| `.split-scroll` | Scrollable area within each column |
| `.unified-overview` | Overview section below columns |
| `.unified-section` | Content wrapper in overview |

---

## 9. Dependencies

| Area | Library |
|------|---------|
| TUI | Textual (+ Rich) |
| HTTP | httpx |
| Models | Pydantic v2 |
| Translation | deep-translator |
| Diff rendering | redlines |
| Syntax | tree-sitter (C, C++, Kotlin, PHP, Ruby, TypeScript) |
| Dev | ruff, mypy, pytest, openpyxl |

---

## 10. Testing

- `uv run pytest tests/ -q` for the test suite.
- Async TUI tests: `@pytest.mark.asyncio` + `app.run_test()`.
- Shared fixtures in `tests/conftest.py`.
- Coverage: `uv run pytest tests/ --cov=sfctl --cov-report=term-missing`.
