# AGENTS.md -- sfctl (Starfleet Control)

Starfleet Control (`sfctl`) is a [Textual](https://github.com/Textualize/textual)
TUI for reviewing model outputs across task types: code review (3-model ranking),
arena ranking (clarity checklist), and project proposals. Python 3.12+.

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
2. **`uv run pytest tests/ -q`** and **`uv run ruff check sfctl/`** must pass
   before claiming work is done.
3. Commit with a clear imperative message (lowercase, no period).
4. One logical change per commit when practical.
5. GPG signing is disabled: `git -c commit.gpgsign=false commit ...`
6. Do not leave unstaged related edits after claiming work is done.
7. No emojis in code, comments, or commit messages.

---

## 3. Architecture

```
sfctl/
  app.py            # Textual App -- main TUI, bindings, compose, navigation
  app.tcss          # Layout, focus, panel styles (Textual CSS)
  cli.py            # CLI entry point (print() allowed here; also interactive setup I/O in api.py)
  ids.py            # Widget IDs, CSS classes, enum constants
  commands.py       # Ctrl+P command palette provider
  models.py         # Dataclasses / Pydantic models (ModelData, ParsedContent, ...)
  task_types.py     # TaskType enum + detect_task_type()
  handlers/
    __init__.py     # handler_for_type() factory
    base.py         # TaskHandler ABC -- interface for task-type-specific behavior
    ranking.py      # RankingHandler (code_review: models, vote bars, diffs, shared files)
    arena.py        # ArenaHandler (arena_ranking: clarity checklist, multi-justification)
    proposal.py     # ProposalHandler (project proposals: rubrics, issues, trace)
  diff.py           # Diff parsing + content extraction (code review / arena)
  arena.py          # Arena checklist + multi-field justification parsing
  proposal.py       # Parser for project-proposal tasks
  diff_compare.py   # Cross-model shared-file compare (ranking)
  cq_viewport.py    # 80-column response width preview helpers (arena)
  ranking.py        # Model summary text helpers
  scoring.py        # Local scores, annotations, justification persistence
  history.py        # History entry normalization, ranking display, change detection
  formatting.py     # Timestamp, duration, sanitize helpers
  fuzzy.py          # Fuzzy matching for search modals
  config.py         # OS config/data dirs via platformdirs (config.json)
  session.py        # Local session history tracking
  api.py            # HTTP API client (httpx) + cookie/token resolution
  constants.py      # Shared constants (EM_DASH, arrows, etc.)
  screens.py        # Modal screens (help, search, yank, comments, shared compare)
  widgets.py        # Custom widgets (DiffDisplay, LazyCollapsible, SplitHandle)
  search.py         # SearchController (diff/event search, grep navigation)
  editor.py         # EditorController (justification, comments, clipboard)
  voting.py         # VotingController (vote bars, score management)
```

### Data flow

`models -> parsers (diff/arena/proposal) -> handlers -> app.py`. Screens and
controllers delegate to app; no file I/O or JSON parsing in screen/controller code.

### Task-type handlers

`app.py` delegates type-specific behavior to a `TaskHandler` subclass (one per
supported task type) via `self.handler`. The app owns widget lifecycle,
navigation, and bindings. The handler owns parsing, content rendering, history
diffing, scoreboard text, and which actions are hidden.

To add a new task type: create a handler in `sfctl/handlers/`, a parser module
if needed, add a `TaskType` enum value + detection rule, and register in
`handler_for_type()`. Prefer extending the handler interface (`hidden_actions`,
`check_action`, `extra_overview_tabs`, response chrome hooks) over branching on
`TaskType` / `isinstance` in `app.py`. Some global bindings still live in the app
(e.g. `w` for arena 80-col preview) when they must be registered at the App
level; keep that surface small.

### Composition controllers

`app.py` delegates to three controllers to keep the class manageable:

| Controller | File | Owns |
|------------|------|------|
| `VotingController` | `voting.py` | Vote bars, score changes, annotations |
| `SearchController` | `search.py` | Diff/event search modals, grep navigation |
| `EditorController` | `editor.py` | Justification (classic single + arena multi-field), comments, clipboard, violation notes |

Controllers receive `self` (the app) and call `self._app.*` for widget access.
They use `self._app._status(msg)` for user feedback (not `notify()`).

---

## 4. Code conventions

### 4.1 Style

- `snake_case` functions/variables, `PascalCase` classes, `UPPER_SNAKE` constants.
- `from __future__ import annotations` in every module.
- Type-annotate public signatures. Use `X | None`, lowercase generics.
- No `print()` except in `cli.py` and interactive setup paths in `api.py`
  (cookie profile picker, token/cookie resolution progress). TUI code must never
  print.
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
| `app.py` | TUI App, bindings, compose, navigation | Trace JSON parsing, API calls (except refresh worker wiring) |
| `screens.py` | Modal screens, search modals | Domain parsing of API payloads |
| `widgets.py` | Custom Textual widgets | Business logic beyond display |
| `formatting.py` | Pure text helpers (timestamps, sanitize) | Widget creation, API calls |
| `handlers/*.py` | Task-type-specific parsing, rendering, history | Direct widget queries outside populate / width-apply methods |
| Controllers (`voting.py`, `search.py`, `editor.py`) | Delegation logic for their concern | Direct JSON parsing, file I/O |

### 4.4 Imports

Module-level imports at the top (stdlib, third-party, local), after
`from __future__ import annotations`.

- Use `TYPE_CHECKING` for type-only imports to break cycles.
- Function-level imports allowed for heavy optional deps (`deep_translator`,
  `openpyxl`, `redlines`) and deferred handler/feature imports.

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
| Code review | `CODE_REVIEW` | N (typically 3 A/B/C) | Diffs, traces, vote bars, ranking, shared-file compare |
| Arena ranking | `ARENA_RANKING` | N (typically 3) | Same as code review plus editable CQ checklist (mark any rule via `v`), 3 editable justifications, 80-col response preview |
| Project proposal | `PROJECT_PROPOSAL` | 1 | Rubrics, issues, trace, diffs |
| Unsupported | `UNKNOWN` | 0 | Minimal overview message only |

Detection logic in `task_types.py`:

- Arena: `"Model Traces"` item + `response_clarity_checklist` question
- Code review: `"Model Traces"` item (without arena checklist)
- Proposal: `coding_question` + `rubrics` questions
- Else: `UNKNOWN`

Parsing in `diff.py` / `arena.py` / `proposal.py`. TUI behavior in
`handlers/ranking.py`, `handlers/arena.py`, `handlers/proposal.py`.

`handler_for_type()` maps proposal and arena explicitly; other types (including
`CODE_REVIEW`) use `RankingHandler`. `UNKNOWN` still gets a ranking handler for
parse plumbing, but compose short-circuits to an unsupported-type message.

---

## 6. TUI and keyboard UX

Keyboard-first. Every feature must be reachable with keys and/or Ctrl+P.

### 6.1 Bindings

Defined in `app.py` `BINDINGS`. Use `check_action()` to context-gate:
return `False` to hide from Footer when not applicable. Handlers may hide
actions via `hidden_actions()` and override `check_action()`.

| Key | Action | Context |
|-----|--------|---------|
| `0` | Overview | Always (in unified: focus overview section) |
| `1`/`2`/`3` | Model A/B/C | Ranking / arena (in unified: focus column) |
| `m` | Model | Proposals |
| `u` | Toggle split (unified) view | When `handler.supports_split` |
| `t` | Toggle translate (system locale) | Always (prompt + responses) |
| `w` | Toggle 80-col response preview | Arena ranking only |
| `v` | Mark CQ checklist violation | Arena (full rule catalog; works on empty tasks) |
| `s` | Cross-model shared-file compare | Ranking when shared files exist |
| `e` | Toggle expand/collapse | Model or overview view |
| `f` | Toggle maximize/restore pane | Any view |
| `+`/`-` | Vote up/down | Model view (ranking / arena) |
| `y` | Yank diff snippet | Model / diff focus (arena → Code quality) |
| `n` / `ctrl+n` | Add / edit reviewer notes | Review flows |
| `ctrl+e` | Edit justification | Overview (arena: cycle response / code / overall; esc saves) |
| `c` / `C` | Copy review / comments | When available |
| `ctrl+f` | Search diffs | Model view |
| `ctrl+g` | Search events | Model view |
| `r` | Refresh data from API | When session cookies present |
| `ctrl+r` | Reset local annotations | Always |
| `@` | Toggle emails in history | Always |
| `tab`/`shift+tab` | Next/prev tab | Any tabbed view (including unified overview) |
| `escape` | Exit editor / fullscreen / 80-col preview | Context-dependent |
| `?` | Help modal | Always |
| `q` | Quit | Always |

### 6.2 Focus model

| Input | Role |
|-------|------|
| **Tab / Shift+Tab** | Cycle tabs within the active `TabbedContent` |
| **0** / **1-3** / **m** | Switch focus between overview and model panes |
| **Arrow keys**, PgUp/PgDn | Scroll inside focused `ScrollableContainer` |
| **Enter / Space** | Activate control or selected item |
| **Esc** | Dismiss modal / exit view modes |

- Focus order follows `compose()` DOM order.
- After switching panes, `.focus()` the target scroll container so keyboard
  scrolling works immediately.
- Bindings that need context (e.g. vote requires model view) use `check_action`
  and `refresh_bindings()` -- return `False` to hide from Footer when inert.

### 6.3 Unified view

The unified (split) view shows all models side-by-side with an overview section
below. Available when `handler.supports_split` (ranking, arena, proposals with
a model column).

- Each column is an independently scrollable `ScrollableContainer`.
- For ranking/arena jobs, each column has its own `TabbedContent`
  (Response/Trace/Diffs).
- Trace, diffs, and history tabs are **deferred** -- content mounts on first
  tab activation via `_deferred_tabs`.
- `_split_focus` tracks which column is active (-1 = overview, 0+ = model index).
- `_split_model_count()` returns the correct column count per task type.
- The `.split-active` CSS class marks the focused column with a top outline.
- Navigation stays within the unified view; `u` toggles back to individual views.
- The overview area has its own `TabbedContent` with Current and history tabs.
- Cross-model file compare is opened with `s` (not an overview tab).
- A `SplitHandle` resizes the models strip versus the overview below.

### 6.4 Command palette

`commands.py` `NavigationProvider` provides Ctrl+P entries. Add new actions
here when adding major features.

### 6.5 Status bar

Use `self._status(msg)` (or `self.call_from_thread(self._status, msg)` from
threaded workers) instead of `self.notify()`. The status bar is a single-line
`Static` widget above the Footer that auto-clears after 4 seconds.

### 6.6 User-facing language and casing

Ship product copy only -- no design-process notes, no internal jargon, no
abbreviations except conventional ones (`Diff` / `Diffs`, `Esc`, `Ctrl+…`,
model letters A/B/C). Prefer full words in sentences (`Code Quality`, not
`CQ`; `Copy Snippet`, not `Yank`).

**Casing by surface** (apply app-wide; shared-file compare is the reference):

| Surface | Casing | Examples |
|---------|--------|----------|
| Footer / command palette / bindings | **Title Case** | Compare Files, Shared, Unique, Pairs, Copy Snippet |
| Section / card kind words | **Title Case** | Shared · loc, Pair · site, Letter · loc |
| Badges / triage chips | **lowercase**, same token everywhere | `new`, `same`, `share`, `diff`, `solo` |
| Counts and stats | **lowercase** | `5 sites`, `2 split`, `3 unique` |
| Banners and body prose | **Sentence case** | Alternate designs — read A, then B, then C |
| Help modal section headers | **Title Case** | File List, Reading the Detail, Keys |
| Status messages | **Sentence case** (or Title Case when echoing a binding) | Filter: Shared |

**Shared-compare tokens** (do not invent synonyms across list/header/help):

| Token | Meaning |
|-------|---------|
| `new` | Multi-model new file (or solo new path) |
| `same` | Identical patches / full agreement at a site |
| `share` | Shared edit path (agreement plus unique cards) |
| `diff` | Same base site, different designs (split) |
| `solo` | Only one model touched the path |
| unique (stat) | Change at a site in only one model |
| split (stat) | Same site, different designs |
| pair (stat) | Exactly two models match at a site |

List-row badges, detail-header badges, and help legends must use the **same**
chip words (`same` not "identical" in one place and "same" in another; `diff`
not "diverge"). Section titles may still say **Shared** / **Pair** (Title
Case kind labels) above those chips.

### 6.7 Translation

Global toggle (`t`) translates all translatable content to the system locale
(prompt, model responses, split responses). Uses `deep-translator` (Google
Translate) with paragraph-boundary chunking for the 5000-char limit. The target
language is detected via `locale.getdefaultlocale()`. Translations are cached in
`self._translated` (keyed by model index or `"prompt"`). The
`_apply_translations()` / `_restore_originals()` methods handle swapping all
widgets.

### 6.8 Keyboard-only checklist (new UI / modals)

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

### 6.9 Performance (deferred loading)

Mount expensive content lazily. The `_deferred_tabs` dict maps tab pane IDs to
tuples describing what to build on first activation:

| Kind | Tuple | Built by |
|------|-------|----------|
| `"trace"` | `("trace", model_idx, vote_bars)` | `_mount_trace_content` |
| `"diffs"` | `("diffs", model_idx, vote_bars)` | `_mount_diffs_content` |
| `"history"` | `("history", orig_idx, changed)` | `_populate_history_entry` |
| `"proposal-trace"` | `("proposal-trace",)` | `_mount_trace_content` (proposal) |
| `"proposal-diffs"` | `("proposal-diffs",)` | mounts `LazyCollapsible` for each file |

Use `Static(RichMarkdown(...))` instead of Textual `Markdown(...)` widget for
content display when practical. The Textual `Markdown` widget creates a
sub-widget tree per element; `RichMarkdown` renders as a single Rich renderable.
Editable overview justifications use Textual `Markdown` previews paired with
`TextArea` editors (classic: one pair; arena: three pairs under
`just-preview-*` / `just-editor-*` ids).

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
| `history_detail_widgets(entry, changed)` | Justification / checklist below diffs |
| `scoreboard_parts()` | Type-specific scoreboard text |
| `extra_overview_tabs()` | Extra overview panes (unused by ranking/arena) |
| `shared_file_compares()` | Cross-model file compare data (`s` modal) |
| `hidden_actions()` | Action names to hide from Footer |
| `response_chrome_widgets` / `response_wrap_classes` | Per-type response chrome |

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
| Config paths | platformdirs |
| Cookies | browser-cookie3 |
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
- Prefer asserting overview **pane IDs** (e.g. `tab-current`, `tab-shared`,
  `tab-entry-N`) over raw `tab_count` when extra overview tabs may be present.
