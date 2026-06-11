# sfctl

Starfleet Control -- task review and evaluation CLI. Built with [Textual](https://textual.textualize.io/).

Review model responses, traces, and code diffs with syntax highlighting and real line numbers. Tabbed per-model views (Response / Trace / Diffs), an Overview page with history and inline summary editing. Structured annotations with per-context scoring and snippet yanking.

## Install

```
pip install git+ssh://git@github.com/indynull/sfctl.git
```

Or with [uv](https://docs.astral.sh/uv/):

```
uv tool install git+ssh://git@github.com/indynull/sfctl.git
```

## Usage

```
sfctl t-abc123def
```

On first run, you'll be prompted to select a browser profile for Starfleet cookies. The selection is saved to your OS config directory (`~/.config/starfleet/` on Linux, `~/Library/Application Support/starfleet/` on macOS).

### Options

```
sfctl t-abc123def -c /path/to/Cookies   # explicit cookie file
sfctl t-abc123def -v                     # verbose output
sfctl t-abc123def --dump                 # dump raw JSON and exit
sfctl -f tests/fixtures/task_sample.json # load from fixture (offline)
sfctl --show-config                      # print config
sfctl --set api_base https://staging...  # set config value
sfctl --clear-config cookie_file         # clear a config key
```

### Fixture mode

Load from a captured JSON file instead of hitting the API:

```
sfctl --fixture tests/fixtures/task_sample.json
```

This is useful for offline development or testing UI changes without auth.

## Keyboard shortcuts

| Key | Action |
|---|---|
| `1` `2` `3` | Switch to model A/B/C |
| `0` | Overview (review, history, feedback) |
| `tab` / `shift+tab` | Next/previous tab within a view |
| `e` | Expand/collapse all in current tab |
| `+` `-` | Vote (context-aware: code on diffs, response on response tab, overall elsewhere) |
| `y` | Yank selected diff snippet into summary |
| `ctrl+e` | Edit summary |
| `ctrl+f` | Fuzzy file search in current model |
| `c` | Copy review to clipboard |
| `r` | Refresh data from API |
| `ctrl+r` | Reset local annotations and scores |
| `?` | Help |
| `q` | Quit |

The command palette (`ctrl+\`) provides fuzzy search across views, diffs, actions, and themes.

## Architecture

```
sfctl/
  api.py          async httpx client, cookie discovery, auth error handling
  app.py          main Textual app, keybindings, model switching
  cli.py          CLI entry point, --fixture / --set / --clear-config / --dump
  commands.py     command palette provider
  config.py       platformdirs-based config/data persistence
  ids.py          widget IDs, CSS classes, enums (Context)
  models.py       Pydantic models (API) + dataclasses (app state)
  parsing.py      content parsing, diff extraction, trace formatting
  ranking.py      pure ranking computation and model identification
  scoring.py      annotation persistence and legacy migration
  screens.py      modal screens (yank, file search, help)
  task_types.py   task type detection from API data
  widgets.py      DiffDisplay (syntax highlighting), LazyCollapsible, trace widgets
```

## Development

```
git clone git@github.com:sfctl/sfctl.git
cd sfctl
uv sync --group dev
```

### Run tests

```
uv run pytest tests/ -v
```

### Lint and format

```
uv run ruff check sfctl/ tests/
uv run ruff format sfctl/ tests/
uv run mypy sfctl/
```

Requires Python 3.14+.