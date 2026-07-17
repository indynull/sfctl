# sfctl

Starfleet Control -- task review and evaluation CLI. Built with [Textual](https://textual.textualize.io/).

Review model responses, traces, and code diffs. Tabbed per-model views (Response / Trace / Diffs), an Overview page with revision history and inline summary editing. Structured annotations with per-context scoring and snippet yanking. Supports code review and project proposal task types.

### Highlights

- **Syntax-highlighted diffs** -- tree-sitter based highlighting matched to your Textual theme, with background shading for added/removed/hunk lines and real source line numbers
- **Fuzzy search** -- fzf-style file finder (`ctrl+f`) and event finder (`ctrl+g`) with grep toggle for searching diff content or event payloads
- **Snippet yanking** -- select lines in a diff and press `y` to yank into your summary with language-specific code fences and source references
- **Reviewer comments** -- press `n` from anywhere to add a note (with optional snippet), accumulated in a Comments tab with raw/rendered toggle and clipboard copy
- **Per-context voting** -- `+`/`-` scores track separately for response quality, code quality, and overall ranking
- **Revision history** -- Overview tab shows diffs between revisions, inline feedback, and justification changes

### Supported languages

Python, JavaScript, TypeScript, TSX, Rust, Go, Java, C, C++, Ruby, PHP, Kotlin, JSON, YAML, TOML, Markdown, HTML, CSS, XML, SQL, Bash.

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

On first run, you'll be prompted to select a browser profile for Starfleet cookies. The selection is saved to your OS config directory (`~/.config/starfleet/` on Linux, `~/Library/Application Support/starfleet/` on macOS, `%APPDATA%\starfleet\` on Windows).

### Authentication

Auth resolution order (first wins):

1. `STARFLEET_ACCESS_TOKEN` environment variable  
2. `-t` / `--token` (saved to config as `access_token`)  
3. Saved `access_token` in config  
4. Browser cookies (`-c` / saved profile / interactive picker)

**Windows:** browser cookie databases are often locked or unavailable to
`browser-cookie3` (DPAPI / browser running). Prefer an access token:

```
set STARFLEET_ACCESS_TOKEN=your-token-here   # cmd
# or PowerShell: $env:STARFLEET_ACCESS_TOKEN = "your-token-here"
sfctl t-abc123def
```

Or: `sfctl t-abc123def -t your-token-here` (persists in config).

Copy the token from the browser: DevTools → Application → Cookies → Starfleet
API host → `accessToken`. Clear with `sfctl --clear-config access_token`.

### Options

```
sfctl t-abc123def -t mytoken             # access token (saved to config)
sfctl t-abc123def -c /path/to/Cookies   # explicit cookie file
sfctl t-abc123def -v                     # verbose output
sfctl t-abc123def --dump                 # dump raw JSON and exit
sfctl -f tests/fixtures/task_sample.json # load from fixture (offline)
sfctl --show-config                      # print config
sfctl --set api_base https://staging...  # set config value
sfctl --clear-config cookie_file         # clear a config key
sfctl --clear-config access_token        # clear saved token
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
| `0` | Overview (review, history, feedback) |
| `1` `2` `3` | Switch to model A/B/C |
| `m` | Switch to model view (proposals) |
| `tab` / `shift+tab` | Next/previous tab within a view |
| `e` | Expand/collapse all in current tab |
| `+` `-` | Vote (context-aware: code on diffs, response on response tab, overall elsewhere) |
| `y` | Yank selected diff snippet into summary |
| `n` | Add a reviewer comment (note) |
| `ctrl+e` | Edit summary |
| `ctrl+n` | Edit comments (raw markdown) |
| `ctrl+f` | Fuzzy file search / grep diff content (toggle with `ctrl+f`) |
| `ctrl+g` | Fuzzy event search / grep event content (toggle with `ctrl+g`) |
| `c` | Copy review to clipboard |
| `C` | Copy comments to clipboard |
| `r` | Refresh data from API |
| `ctrl+r` | Reset local annotations to server state |
| `?` | Help |
| `q` | Quit |

The command palette (`ctrl+\`) provides fuzzy search across views, diffs, actions, and themes.

## Development

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

