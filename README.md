# sftui

Terminal UI for reviewing Starfleet preference tasks. Built with [Textual](https://textual.textualize.io/).

Displays model traces, code diffs (with syntax highlighting and real line numbers), and reviewer feedback side-by-side. Supports local scoring with A/B/C model ranking, justification editing, and snippet yanking.

## Install

```
pip install git+https://github.com/YOUR_USER/sftui.git
```

Or with [uv](https://docs.astral.sh/uv/):

```
uv tool install git+https://github.com/YOUR_USER/sftui.git
```

## Usage

```
sftui t-abc123def
```

On first run, you'll be prompted to select a browser profile for Starfleet cookies. The selection is saved to your OS config directory (`~/.config/starfleet/` on Linux, `~/Library/Application Support/starfleet/` on macOS).

### Options

```
sftui t-abc123def -c /path/to/Cookies   # explicit cookie file
sftui t-abc123def -v                     # verbose output
sftui -f tests/fixtures/task_sample.json # load from fixture (offline)
sftui --show-config                      # print config
sftui --set api_base https://staging...  # set config value
sftui --clear-config cookie_file         # clear a config key
```

### Fixture mode

Load from a captured JSON file instead of hitting the API:

```
sftui --fixture tests/fixtures/task_sample.json
```

This is useful for offline development or testing UI changes without auth.

## Keyboard shortcuts

| Key | Action |
|---|---|
| `1` `2` `3` | Switch to model A/B/C |
| `]` `[` | Next/previous model |
| `f` | Feedback |
| `+` `-` | Upvote/downvote (context-aware: overall/response/code) |
| `j` | Edit justification |
| `y` | Yank selected diff snippet into justification |
| `c` | Copy rankings and justification to clipboard |
| `ctrl+f` | Fuzzy file search in current model |
| `r` | Refresh data from API |
| `ctrl+r` | Reset local scores and justification |
| `?` | Help |
| `q` | Quit |

The command palette (`ctrl+\`) provides fuzzy search across views, diffs, actions, and themes.

## Architecture

```
sftui/
  api.py        async httpx client, cookie discovery, auth error handling
  app.py        main Textual app, keybindings, model switching
  cli.py        CLI entry point, --fixture / --set / --clear-config
  commands.py   command palette provider
  config.py     platformdirs-based config/data persistence
  models.py     Pydantic models (API) + dataclasses (app state)
  parsing.py    content parsing, diff extraction, trace formatting
  scoring.py    local score and justification persistence
  screens.py    modal screens (justification editor, yank, file search)
  widgets.py    DiffDisplay (Pygments highlighting), LazyCollapsible, trace widgets
```

## Development

```
git clone https://github.com/YOUR_USER/sftui.git
cd sftui
uv sync --group dev
```

### Run tests

```
uv run pytest tests/ -v
```

66 tests covering model validation, content parsing, scoring persistence, config management, feedback dedup, and API helpers.

### Lint and format

```
uv run ruff check sftui/ tests/
uv run ruff format sftui/ tests/
uv run mypy sftui/
```

Requires Python 3.13+.