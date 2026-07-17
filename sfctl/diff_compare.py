"""N-way parallel patch review for ranking (shared-file compare).

Design
======

Several models solve the **same** coding task. Each produces a git-style
unified patch against a common base (or a pure-add "new file"). Reviewers
need to rank *designs*, not merge them. This module turns those patches into
a file-first, site-first comparison UI.

Pipeline
--------

1. **Corpus** — union of paths any model touched (not intersection).
2. **Per path** — for each model, a patch string or absence.
3. **Path triage** (badge on the file list; Policy A — always from core)::

       solo     — only one model touched the path
       same     — present models have identical full patches (edits)
       new      — every present model introduces the path as a new file
       del      — every present model fully deletes the path
       share    — multi-model edit; multi-present core sites all agree
                  (no diverge/pair among them; partial agreement → diff)
       diff     — multi-model edit; any multi-present diverge/pair, or
                  only unique sites / empty core

4. **Core sites (edits only)** — split each patch into hunks and group by
   **exact old-file start line**. Co-location is point-anchor only; different
   ``old_start`` values are different sites (no core coalesce). Models
   absent at a site leave a null slot.
5. **Designs at a site** — present models are partitioned by **body key**
   (ordered add/del lines). One body key = one design. Relations::

       same    — all present models share one body
       unique  — exactly one model present (``only`` in code)
       pair    — two models share a body; a third differs (3-way only)
       diverge — two or more distinct bodies

6. **Presentation (non-normative)** — optional signal filter, span coalesce,
   and fingerprint merge densify the detail UI only. They never recompute
   path badges or list agreement rates. For each surviving site (clustered
   by location when consecutive), emit designs as tabs/cards with real
   unified hunks — never token-set Shared windows on mid-file edits.
7. **New files** — no base line numbers. Emit one card per model (gallery),
   optionally stripping a common leading prefix so cards open on real code.
8. **Deleted files** — pure delete patches (no remaining file body). Badge
   ``del`` with ``-N`` sizes; card shows the removed body (not an empty stub).

What this is not
----------------

Not a three-way merge tool. Site cards labeled Shared mean *BodyKey agreement
at one base site*, not a merge base and not bag-of-token overlap. Near-identical
new-file compression may show a "Common lines" card; that is presentation only
and never changes the path badge.

Hard limits today: three model slots (A/B/C). The site/design model extends
to N by widening the hunk tuple and coverage alphabet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rich.markup import escape as _rich_escape

from sfctl.constants import DIFF_ADD, DIFF_ADD_STYLE, DIFF_DEL, DIFF_DEL_STYLE
from sfctl.diff import DiffLine, parse_diff_lines
from sfctl.ids import model_letter
from sfctl.models import FileDiff, ModelData

# Generic returns collide across unrelated methods and inflate pair/shared
# sets (e.g. both models add ``return (`` or ``return self.openapi_schema``).
_GENERIC_RETURN = re.compile(
    r"^(return|raise)\s+(self\.\w+\s*$|\(.*)"
)
# Braces / bare pass / import ( that appear in almost every file.
_GENERIC_SHELL = frozenset({"import (", ")", "{", "}", "pass"})
_TRIVIAL_CALL = re.compile(r"^[\w.]+ \(\)$")
# Block-comment body row: " * text" or lone "*" (not pointer code like "*p = 1").
_BLOCK_COMMENT_BODY = re.compile(r"^\*(?:\s|$)")
_PUNCT_ONLY = frozenset("()[]{},.;:")


def _is_comment_only_line(text: str) -> bool:
    """True when the line is only a comment (no code tokens)."""
    s = (text or "").strip()
    if not s:
        return True
    if s.startswith("#!"):
        return False
    if s.startswith("//") or s.startswith("#"):
        return True
    if s.startswith("<!--") or s.startswith("-->") or s == "-->":
        return True
    if s.startswith("/*") or s in ("*/", "*", "/**", "/**/"):
        return True
    return bool(_BLOCK_COMMENT_BODY.match(s) or s.endswith("*/"))


def _is_punct_only(text: str) -> bool:
    s = (text or "").strip()
    return bool(s) and all(c in _PUNCT_ONLY for c in s)


_LONE_TRIPLE_QUOTE = re.compile(r"^(\s*)(\"\"\"|''')\s*$")


def _is_signal_line(text: str) -> bool:
    """Whether a line is useful for cross-model overlap matching."""
    s = text.strip()
    if not s or _is_comment_only_line(text) or s in _GENERIC_SHELL or _is_punct_only(s):
        return False
    # Bare docstring delimiters collide across every Python design card.
    if _LONE_TRIPLE_QUOTE.match(text or ""):
        return False
    return not (_GENERIC_RETURN.match(s) or _TRIVIAL_CALL.match(s))


def _is_no_newline_marker(text: str) -> bool:
    """Git unified-diff EOF marker (often mis-parsed as context)."""
    return (text or "").strip().startswith("\\ No newline")


def is_new_file_patch(diff_text: str) -> bool:
    """True when the patch is pure adds (new file), no real ctx/del.

    Blank trailing lines from split are parsed as empty ``ctx`` and ignored.
    Git ``\\ No newline at end of file`` markers are ignored (not real context).
    Synthetic empty-file markers (``# empty new file``) count as new files.
    """
    if not (diff_text or "").strip():
        return False
    has_add = False
    saw_empty_marker = False
    for dl in parse_diff_lines(diff_text):
        # Blank del lines are encoding noise, not real deletes.
        if dl.kind == "del" and dl.text.replace("\r", "").strip():
            return False
        if dl.kind == "ctx" and dl.text.strip():
            if _is_no_newline_marker(dl.text):
                continue
            if dl.text.strip().lower() in {"# empty new file", "# empty file"}:
                saw_empty_marker = True
                continue
            # Non-blank context => not a pure-add full file.
            return False
        if dl.kind == "add":
            # Blank adds still count (empty new-file body is a pure-add shape).
            has_add = True
    return has_add or saw_empty_marker


def is_deleted_file_patch(diff_text: str) -> bool:
    """True when the patch is a full-file delete (pure dels, no real add/ctx).

    Git ``\\ No newline at end of file`` markers are ignored. Blank context
    rows from split are ignored. Any non-blank add or context line means this
    is an edit, not a full delete.
    """
    if not (diff_text or "").strip():
        return False
    has_del = False
    for dl in parse_diff_lines(diff_text):
        if dl.kind == "add" and dl.text.replace("\r", "").strip():
            return False
        if dl.kind == "ctx" and dl.text.strip():
            if _is_no_newline_marker(dl.text):
                continue
            return False
        if dl.kind == "del" and dl.text.replace("\r", "").strip():
            has_del = True
    return has_del


# Unified hunk header: @@ -old_start,old_count +new_start,new_count @@ label
_HUNK_HDR = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$"
)

_LINE_MAX = 96
_SNIPPET_CONTEXT = 3
_SNIPPET_MAX_ROWS = 220
_SNIPPETS_ALL = 8
_SNIPPETS_FILTER = 12


def _signal_lines(lines: list[str]) -> list[str]:
    return list(dict.fromkeys(ln for ln in lines if _is_signal_line(ln)))


def _signal_set(lines: list[str]) -> set[str]:
    return set(_signal_lines(lines))


def _nonempty_line_count(lines: list[str]) -> int:
    """Count non-blank lines (closer to ``git diff --stat`` insertions)."""
    return sum(1 for ln in lines if ln.strip())


def _plus_minus(n_add: int, n_del: int) -> list[str]:
    """Git-style ``+N`` / ``-M`` tokens; omit zero sides (never ``+0`` / ``-0``)."""
    bits: list[str] = []
    if n_add:
        bits.append(f"+{n_add}")
    if n_del:
        bits.append(f"-{n_del}")
    return bits


_RENAME_FROM = re.compile(r"^rename from (.+)$", re.MULTILINE)
_RENAME_TO = re.compile(r"^rename to (.+)$", re.MULTILINE)
_DIFF_GIT = re.compile(
    r"^diff --git a/(.+?) b/(.+)$", re.MULTILINE
)


def parse_rename_pair(diff_text: str) -> tuple[str, str] | None:
    """Return ``(old_path, new_path)`` when the patch is a git rename."""
    if not (diff_text or "").strip():
        return None
    mf = _RENAME_FROM.search(diff_text)
    mt = _RENAME_TO.search(diff_text)
    if mf and mt:
        return mf.group(1).strip(), mt.group(1).strip()
    return None


def path_aliases_from_models(models: list[ModelData]) -> dict[str, str]:
    """Map alternate path names to a canonical path (prefer rename target).

    If any model renames ``old → new``, both names resolve to ``new`` so a
    second model that still edits ``old`` is compared under the same key when
    the first model only has the rename tip. Identity is best-effort: pure
    renames without content are still linked.
    """
    alias: dict[str, str] = {}
    for m in models:
        for fd in m.file_diffs:
            pair = parse_rename_pair(fd.diff or "")
            if not pair:
                continue
            old, new = pair
            alias[old] = new
            alias.setdefault(new, new)
    # Flatten one hop
    flat: dict[str, str] = {}
    for k, v in alias.items():
        flat[k] = alias.get(v, v)
    return flat


def canonical_path(path: str, aliases: dict[str, str]) -> str:
    return aliases.get(path, path)




@dataclass(slots=True)
class PatchHunk:
    """One unified-diff hunk from a single model's patch."""

    model_idx: int
    old_start: int
    old_count: int
    header: str
    label: str
    lines: list[DiffLine]  # includes hunk header as first element

    @property
    def add_texts(self) -> list[str]:
        return [dl.text for dl in self.lines if dl.kind == "add"]

    @property
    def del_texts(self) -> list[str]:
        return [dl.text for dl in self.lines if dl.kind == "del"]

    @property
    def body_key(self) -> tuple[str, ...]:
        """Identity of the change (add+del texts) for cross-model equality.

        Normalization (so raters see *design* equality, not patch encoding):
        - strip CR (``\\r``) from line text
        - drop blank add/del lines (whitespace-only) — pure blank inserts/deletes
          rarely change design intent and otherwise split A/B from C on noise
        """
        parts: list[str] = []
        for dl in self.lines:
            if dl.kind not in ("add", "del"):
                continue
            text = dl.text.replace("\r", "")
            if not text.strip():
                continue
            parts.append(f"{dl.kind}:{text}")
        return tuple(parts)

    def to_unified(self) -> str:
        rows: list[str] = []
        for dl in self.lines:
            if dl.kind == "hunk":
                rows.append(dl.text if dl.text.startswith("@@") else f"@@ {dl.text} @@")
            elif dl.kind == "add":
                rows.append(f"+{dl.text}")
            elif dl.kind == "del":
                rows.append(f"-{dl.text}")
            elif dl.kind == "ctx":
                rows.append(f" {dl.text}")
        return "\n".join(rows)


@dataclass(slots=True)
class RegionDecision:
    """Base-anchored decision: models that touch the same old-file location."""

    old_start: int
    label: str
    hunks: tuple[PatchHunk | None, PatchHunk | None, PatchHunk | None]
    relation: str  # "same" | "only" | "pair" | "diverge"
    # How many base sites collapsed into this card (repeated call sites).
    site_count: int = 1

    @property
    def present_indices(self) -> tuple[int, ...]:
        return tuple(i for i, h in enumerate(self.hunks) if h is not None)

    @property
    def coverage(self) -> str:
        return "".join(model_letter(i) for i in self.present_indices)

    def body_groups(self) -> list[tuple[tuple[str, ...], list[int]]]:
        """Group present models by identical hunk body (add+del)."""
        groups: dict[tuple[str, ...], list[int]] = {}
        order: list[tuple[str, ...]] = []
        for i, h in enumerate(self.hunks):
            if h is None:
                continue
            key = h.body_key
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(i)
        return [(k, groups[k]) for k in order]

    def body_fingerprint(self) -> tuple[tuple[str, ...] | None, ...]:
        """Per-model body keys (None if model absent) for dedupe."""
        return tuple(None if h is None else h.body_key for h in self.hunks)


@dataclass(slots=True)
class SharedFileCompare:
    """Comparison summary for one filename touched by one or more models."""

    filename: str
    patches: tuple[str, str, str] = ("", "", "")
    identical_patches: bool = False
    empty_models: tuple[bool, bool, bool] = (False, False, False)
    adds: tuple[list[str], list[str], list[str]] = field(
        default_factory=lambda: ([], [], [])
    )
    dels: tuple[list[str], list[str], list[str]] = field(
        default_factory=lambda: ([], [], [])
    )
    # Normative core sites (exact old_start + BodyKey). Path badge uses these.
    core_decisions: list[RegionDecision] = field(default_factory=list)
    # Presentation sites (optional coalesce/signal filter). Detail UI uses these.
    decisions: list[RegionDecision] = field(default_factory=list)

    @property
    def present_indices(self) -> tuple[int, ...]:
        """Model indices with a non-empty patch for this file."""
        return tuple(i for i, empty in enumerate(self.empty_models) if not empty)

    @property
    def coverage(self) -> str:
        """Canonical coverage key: model letters in ascending index order.

        Always ``A``/``AB``/``ABC`` style (sorted by model index), never
        discovery order --- list grouping and CoverageRank depend on this.
        """
        return "".join(model_letter(i) for i in self.present_indices)

    @property
    def n_models(self) -> int:
        return len(self.present_indices)

    @property
    def is_solo(self) -> bool:
        return self.n_models == 1

    @property
    def is_pair(self) -> bool:
        return self.n_models == 2

    @property
    def is_all_new(self) -> bool:
        """True when every present model introduces this path as a new file."""
        idxs = self.present_indices
        return bool(idxs) and all(is_new_file_patch(self.patches[i]) for i in idxs)

    @property
    def is_all_deleted(self) -> bool:
        """True when every present model fully deletes this path."""
        idxs = self.present_indices
        return bool(idxs) and all(is_deleted_file_patch(self.patches[i]) for i in idxs)

    @property
    def prefer_per_model_new_files(self) -> bool:
        """Multi-model new paths: one design card per model (no base sites)."""
        return (
            self.is_all_new
            and self.n_models >= 2
            and not self.identical_patches
        )

    @property
    def path_kind(self) -> str:
        """Triage label: deleted | new | solo | identical | shared_edit | diverge.

        Decision order (normative):
        1. all present patches are full-file deletes → del (incl. solo delete)
        2. all present are pure-add new files → new (incl. identical multi-new)
        3. single model edit → solo
        4. multi-model identical edit patches → same
        5. multi-model sites: share only if every multi-present site is same;
           any diverge/pair among multi-present sites → diff
        """
        if self.is_all_deleted:
            return "deleted"
        if self.is_all_new:
            return "new"
        if self.is_solo:
            return "solo"
        if self.identical_patches:
            return "identical"
        # Policy A: path badge from CORE sites only (never post-coalesce filter).
        sites = self.core_decisions
        if not sites:
            return "diverge"
        multi = [d for d in sites if len(d.present_indices) >= 2]
        if not multi:
            return "diverge"
        if any(d.relation in ("diverge", "pair") for d in multi):
            return "diverge"
        if any(d.relation == "same" for d in multi):
            return "shared_edit"
        return "diverge"

    @property
    def use_region_decisions(self) -> bool:
        """True when detail UI uses base-anchored sites (multi-model edits).

        New / deleted full-file paths skip site UI. Solo / identical edits use
        a single full-patch card.
        """
        if (
            self.is_solo
            or self.identical_patches
            or self.is_all_new
            or self.is_all_deleted
        ):
            return False
        return self.n_models >= 2 and bool(self.decisions)

    @property
    def consensus_adds(self) -> list[str]:
        """Signal adds present in all three models (strict ABC).

        Navigation / jump helper only — **not** a path agreement metric.
        Site-local BodyKey equality is the agreement unit; do not surface
        this as a Shared KPI (Policy A / anti-metrics).
        """
        a_lines = _signal_lines(self.adds[0])
        sb, sc = _signal_set(self.adds[1]), _signal_set(self.adds[2])
        return [ln for ln in a_lines if ln in sb and ln in sc]

    @property
    def agreement_adds(self) -> list[str]:
        """Signal adds shared by every model that touched this file (needs ≥2).

        Jump-target helper only; never used for path badges or list stats.
        """
        if self.n_models >= 3:
            return self.consensus_adds
        if self.is_pair:
            return self.pair_adds.get(self.coverage, [])
        return []

    @property
    def only_adds(self) -> tuple[list[str], list[str], list[str]]:
        """Signal adds unique to each model (weak lines ignored).

        Jump / extract helper only; not a path-triage signal.
        """
        sa, sb, sc = map(_signal_set, self.adds)
        return (
            [ln for ln in _signal_lines(self.adds[0]) if ln not in sb and ln not in sc],
            [ln for ln in _signal_lines(self.adds[1]) if ln not in sa and ln not in sc],
            [ln for ln in _signal_lines(self.adds[2]) if ln not in sa and ln not in sb],
        )

    @property
    def pair_adds(self) -> dict[str, list[str]]:
        """Signal adds shared by exactly two models (ordered from first member).

        Jump helper only; pair as a *site relation* is BodyKey-based.
        """
        sa, sb, sc = map(_signal_set, self.adds)
        ab = [ln for ln in _signal_lines(self.adds[0]) if ln in sb and ln not in sc]
        ac = [ln for ln in _signal_lines(self.adds[0]) if ln in sc and ln not in sb]
        bc = [ln for ln in _signal_lines(self.adds[1]) if ln in sc and ln not in sa]
        return {"AB": ab, "AC": ac, "BC": bc}

    def kind_badge(self) -> str:
        """Short triage chip label (same / new / del / share / diff / solo)."""
        return {
            "identical": "same",
            "new": "new",
            "deleted": "del",
            "solo": "solo",
            "shared_edit": "share",
            "diverge": "diff",
        }.get(self.path_kind, self.path_kind)

    def _stats_bits(self) -> list[str]:
        """Compact stats tokens for list rows and headers.

        Uses git-style ``+N`` / ``-M`` (zero sides omitted, never ``-0``).
        Pure new-file / full-delete paths use the ``new`` / ``del`` badge and
        size stats only. Edit multi-model paths with region decisions report
        region counts (not line-set noise).
        """
        missing = [model_letter(i) for i, e in enumerate(self.empty_models) if e]

        if self.is_all_deleted:
            # Badge is ``del``; sizes in coverage order (mirror of ``new``).
            # Identical deletes collapse to one ``-N`` (not ``-N · -N · -N``).
            sizes = [
                f"-{n}" if n else "0"
                for i in self.present_indices
                for n in (_nonempty_line_count(self.dels[i]),)
            ]
            if len(set(sizes)) == 1 and not missing:
                return sizes[:1] or ["empty"]
            bits = list(sizes)
            if missing:
                bits.append("no " + ",".join(missing))
            return bits or ["empty"]

        if self.identical_patches:
            idx = self.present_indices[0] if self.present_indices else 0
            n_add = _nonempty_line_count(self.adds[idx])
            n_del = _nonempty_line_count(self.dels[idx])
            return _plus_minus(n_add, n_del) or ["empty"]

        if self.is_solo:
            idx = self.present_indices[0]
            n_add = _nonempty_line_count(self.adds[idx])
            n_del = _nonempty_line_count(self.dels[idx])
            # Badge already says new/solo/del — stats are sizes only.
            if self.is_all_new:
                return _plus_minus(n_add, 0) or ["empty"]
            return _plus_minus(n_add, n_del) or ["empty"]

        if self.is_all_new:
            # Sizes in coverage order (kind badge already says new).
            bits = []
            for i in self.present_indices:
                n = _nonempty_line_count(self.adds[i])
                bits.append(f"+{n}" if n else "0")
            if missing:
                bits.append("no " + ",".join(missing))
            return bits

        # Policy A metrics: multi-model *edit* stats come from core sites only.
        # Never report bag-of-token "N same" (agreement_adds) — that invents
        # path-level Shared the geometry refuses (false locus conflation).
        if self.core_decisions:
            bits = self._site_stats_bits(self.core_decisions)
            if missing:
                bits.append("no " + ",".join(missing))
            return bits or ["differs"]

        # No core sites (fallback path): sizes only, no token-overlap KPI.
        bits = []
        for i in self.present_indices:
            letter = model_letter(i)
            n_add = _nonempty_line_count(self.adds[i])
            n_del = _nonempty_line_count(self.dels[i])
            size = " ".join(_plus_minus(n_add, n_del))
            if size:
                bits.append(f"{letter} {size}")
        if missing:
            bits.append("no " + ",".join(missing))
        return bits or ["differs"]

    def _site_stats_bits(self, sites: list[RegionDecision]) -> list[str]:
        """Core-site counts without repeating the kind badge (share/diff)."""
        n_same = sum(1 for d in sites if d.relation == "same")
        n_pair = sum(1 for d in sites if d.relation == "pair")
        n_div = sum(1 for d in sites if d.relation == "diverge")
        n_only = sum(1 for d in sites if d.relation == "only")
        n_reg = len(sites)
        bits: list[str] = [f"{n_reg} site" + ("s" if n_reg != 1 else "")]
        # Breakdown only when mixed (badge already names the dominant kind).
        kinds_hit = sum(1 for n in (n_same, n_pair, n_div, n_only) if n)
        if kinds_hit <= 1:
            return bits
        if n_same:
            bits.append(f"{n_same} same")
        if n_pair:
            bits.append(f"{n_pair} pair")
        if n_div:
            bits.append(f"{n_div} split")
        if n_only:
            # "unique" = change present in only one model at that site
            bits.append(f"{n_only} unique")
        return bits

    def summary_label(self) -> str:
        """Plain one-line label (overview tab / plain fallbacks)."""
        badge = self.kind_badge()
        meta = self._list_meta_text()
        cov = self.coverage or "?"
        tail = f"  {meta}" if meta else ""
        return f"{badge}  {cov}  {self.filename}{tail}"

    def _list_meta_text(self) -> str:
        """Trailing size only for list rows (coverage lives on group headers).

        Examples: ``+7``, ``-44``, ``+154-205``. Empty for plain edits.
        """
        if self.is_all_deleted:
            sizes = [
                _nonempty_line_count(self.dels[i]) for i in self.present_indices
            ]
            if not sizes:
                return ""
            return (
                f"-{sizes[0]}"
                if len(set(sizes)) == 1
                else f"-{min(sizes)}-{max(sizes)}"
            )
        if self.is_all_new:
            sizes = [
                _nonempty_line_count(self.adds[i]) for i in self.present_indices
            ]
            if not sizes:
                return ""
            return (
                f"+{sizes[0]}"
                if len(set(sizes)) == 1
                else f"+{min(sizes)}-{max(sizes)}"
            )
        return ""

    def list_prompt(self, model_colors: dict[str, str] | None = None):
        """Two-line fixed-column prompt for a *file* row (not group headers).

        Layout (character columns, ~40-wide content)::

            kind_ basename................ SIZE
            directory/path/...............

        Model coverage is **not** repeated here — it is shown once on the
        group header above consecutive files that share the same models.
        """
        from rich.text import Text

        from sfctl.badges import KIND_COL_WIDTH, append_kind_column

        kind = self.kind_badge()
        colors = model_colors or {}
        if "/" in self.filename:
            parent, base = self.filename.rsplit("/", 1)
        else:
            parent, base = "", self.filename

        meta = self._list_meta_text()
        if len(meta) > _LIST_META_WIDTH:
            meta = meta[:_LIST_META_WIDTH]

        reserved = KIND_COL_WIDTH + 1
        if meta:
            reserved += 1 + _LIST_META_WIDTH
        base_max = max(8, _LIST_ROW_WIDTH - reserved)
        if len(base) > base_max:
            base = base[: base_max - 2] + ".."

        t = Text()
        append_kind_column(t, kind)
        t.append(" ")
        t.append(base, style="bold")

        if meta:
            used = KIND_COL_WIDTH + 1 + len(base)
            meta_start = _LIST_ROW_WIDTH - _LIST_META_WIDTH
            pad = meta_start - used
            if pad < 1:
                pad = 1
            t.append(" " * pad)
            col = f"{meta:>{_LIST_META_WIDTH}}"
            _append_list_meta_text(t, col, colors)

        t.append("\n")
        if parent:
            path = _list_dir_line(parent, max_len=_LIST_ROW_WIDTH)
            t.append(path, style="dim")
        return t

    def short_stats(self) -> str:
        return "  ".join(self._stats_bits())


# File-list geometry (must fit #shared-file-list content width).
_LIST_ROW_WIDTH = 40
_LIST_META_WIDTH = 9  # "+154-205" / "-44"


def _list_dir_line(parent: str, *, max_len: int = _LIST_ROW_WIDTH) -> str:
    """Directory line for list row 2: deepest segments, trailing slash, ASCII.

    Uses ASCII ``.../`` (not unicode ellipsis) so column width is stable in
    every terminal. Elides only from the left.
    """
    if not parent:
        return ""
    parts = [p for p in parent.split("/") if p]
    if not parts:
        return ""
    # Prefer full path with trailing slash when it fits.
    full = "/".join(parts) + "/"
    if len(full) <= max_len:
        return full
    # Grow deepest segments until ``.../`` + joined + ``/`` would overflow.
    kept: list[str] = []
    for part in reversed(parts):
        trial_parts = [part, *kept]
        trial = ".../" + "/".join(trial_parts) + "/"
        if len(trial) > max_len and kept:
            break
        if len(trial) > max_len and not kept:
            # One segment still too long.
            body = part[-(max_len - 5) :] if max_len > 5 else part[:max_len]
            return f".../{body}/"[:max_len]
        kept.insert(0, part)
    return ".../" + "/".join(kept) + "/"


def _append_list_meta_text(
    t: object,
    col: str,
    model_colors: dict[str, str],
) -> None:
    """Append a fixed-width meta column; color coverage letters and +/- sizes."""
    from rich.text import Text

    assert isinstance(t, Text)
    # col may be left-padded spaces for right alignment inside META_WIDTH.
    i = 0
    n = len(col)
    while i < n:
        ch = col[i]
        if ch == " ":
            t.append(" ")
            i += 1
            continue
        if ch in "ABC":
            style = f"bold {model_colors[ch]}" if ch in model_colors else "bold"
            t.append(ch, style=style)
            i += 1
            continue
        if ch in "+-":
            j = i + 1
            while j < n and (col[j].isdigit() or col[j] in "+-."):
                j += 1
            tok = col[i:j]
            _append_stat_token_text(t, tok, model_colors)
            i = j
            continue
        t.append(ch, style="dim")
        i += 1


def _added_lines(diff_text: str) -> list[str]:
    return [dl.text for dl in parse_diff_lines(diff_text or "") if dl.kind == "add"]


def _deleted_lines(diff_text: str) -> list[str]:
    return [dl.text for dl in parse_diff_lines(diff_text or "") if dl.kind == "del"]


def common_filenames(models: list[ModelData]) -> list[str]:
    """Filenames present in every model's file_diffs (strict ABC intersection)."""
    if len(models) < 2:
        return []
    sets = [{fd.filename for fd in m.file_diffs if fd.filename} for m in models]
    if not sets:
        return []
    return sorted(set.intersection(*sets))


def union_filenames(models: list[ModelData]) -> list[str]:
    """All filenames touched by any model (sorted alphabetically)."""
    names: set[str] = set()
    for m in models:
        for fd in m.file_diffs:
            if fd.filename:
                names.add(fd.filename)
    return sorted(names)


def _patch_for(
    models: list[ModelData],
    filename: str,
    *,
    aliases: dict[str, str] | None = None,
) -> list[str]:
    """Patch text per model for *filename*, following rename aliases.

    A model matches if it has a FileDiff whose filename equals *filename*,
    or whose filename aliases to the same canonical path, or whose rename
    pair involves *filename*.
    """
    aliases = aliases or {}
    want = canonical_path(filename, aliases)
    out: list[str] = []
    for m in models:
        found = ""
        for f in m.file_diffs:
            if not f.filename:
                continue
            if f.filename == filename or canonical_path(f.filename, aliases) == want:
                found = (f.diff or "")
                break
            pair = parse_rename_pair(f.diff or "")
            if pair and (pair[0] == filename or pair[1] == filename or
                         canonical_path(pair[0], aliases) == want or
                         canonical_path(pair[1], aliases) == want):
                found = (f.diff or "")
                break
        out.append(found)
    return out


def _normalize_patch_for_identity(diff_text: str) -> str:
    """Normalize unified patch text for full-patch equality checks.

    Ignores git's ``\\ No newline at end of file`` markers and CR so two
    patches that differ only in EOF newline encoding still count as the same
    design.
    """
    lines: list[str] = []
    for line in (diff_text or "").splitlines():
        if line.startswith("\\ No newline"):
            continue
        lines.append(line.replace("\r", ""))
    return "\n".join(lines)


def _identical_among_present(patches: tuple[str, str, str]) -> bool:
    present = [_normalize_patch_for_identity(p) for p in patches if p.strip()]
    if len(present) < 2:
        return False
    first = present[0]
    return all(p == first for p in present[1:])


def parse_patch_hunks(diff_text: str, model_idx: int) -> list[PatchHunk]:
    """Split a unified patch into hunks keyed by old-file start line."""
    if not (diff_text or "").strip():
        return []
    dlines = parse_diff_lines(diff_text)
    hunks: list[PatchHunk] = []
    i = 0
    while i < len(dlines):
        dl = dlines[i]
        if dl.kind != "hunk":
            i += 1
            continue
        header = dl.text
        m = _HUNK_HDR.match(header)
        old_start = int(m.group(1)) if m else 0
        old_count = int(m.group(2) or "1") if m else 0
        label = (m.group(5) or "").strip() if m else ""
        j = i + 1
        while j < len(dlines) and dlines[j].kind not in ("hunk", "meta"):
            j += 1
        hunks.append(
            PatchHunk(
                model_idx=model_idx,
                old_start=old_start,
                old_count=old_count,
                header=header,
                label=label,
                lines=dlines[i:j],
            )
        )
        i = j
    return hunks


def _classify_region_relation(
    hunks: tuple[PatchHunk | None, PatchHunk | None, PatchHunk | None],
) -> str:
    present = [i for i, h in enumerate(hunks) if h is not None]
    if len(present) <= 1:
        return "only"
    keys = {hunks[i].body_key for i in present}  # type: ignore[union-attr]
    if len(keys) == 1:
        return "same"
    # Exactly two models, different bodies.
    if len(present) == 2:
        return "diverge"
    # Three models: pair if exactly two share a body and the third differs.
    groups: dict[tuple[str, ...], int] = {}
    for i in present:
        k = hunks[i].body_key  # type: ignore[union-attr]
        groups[k] = groups.get(k, 0) + 1
    if any(n == 2 for n in groups.values()) and len(groups) == 2:
        return "pair"
    return "diverge"


def build_core_region_decisions(
    patches: tuple[str, str, str],
    empty_models: tuple[bool, bool, bool],
) -> list[RegionDecision]:
    """Normative core sites: exact ``old_start`` + BodyKey classification.

    Does **not** drop non-signal sites or coalesce across starts. Peer patches
    against the same base do not shift each other's anchors; different
    hunkification of one conceptual region stays multiple sites.
    """
    by_start: dict[int, list[PatchHunk | None]] = {}
    labels: dict[int, str] = {}
    for idx in range(3):
        if empty_models[idx]:
            continue
        for h in parse_patch_hunks(patches[idx], idx):
            slot = by_start.setdefault(h.old_start, [None, None, None])
            # First hunk at this start wins per model (malformed-input guard).
            if slot[idx] is None:
                slot[idx] = h
            if h.label and h.old_start not in labels:
                labels[h.old_start] = h.label
    decisions: list[RegionDecision] = []
    for old_start in sorted(by_start):
        slot = by_start[old_start]
        hunks_t = (slot[0], slot[1], slot[2])
        if sum(1 for h in hunks_t if h is not None) == 0:
            continue
        decisions.append(
            RegionDecision(
                old_start=old_start,
                label=labels.get(old_start, ""),
                hunks=hunks_t,
                relation=_classify_region_relation(hunks_t),
            )
        )
    return decisions


def build_region_decisions(
    patches: tuple[str, str, str],
    empty_models: tuple[bool, bool, bool],
    *,
    coalesce: bool = True,
) -> list[RegionDecision]:
    """Sites for presentation: core grouping, then optional coalesce/filter.

    Path badges must use :func:`build_core_region_decisions` (or
    ``SharedFileCompare.core_decisions``), never this list after coalesce.
    """
    core = build_core_region_decisions(patches, empty_models)
    if not coalesce:
        return core
    return _coalesce_decisions(list(core))


def _adds_signature(hunk: PatchHunk | None) -> tuple[str, ...]:
    """Signal adds only — for matching the same field inserted at different lines."""
    if hunk is None:
        return ()
    return tuple(_signal_lines(hunk.add_texts))


# TypedDict / config field only — not ``pending = set(...)`` or ``else:``.
_FIELD_ANNOT = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*[A-Za-z_\"'\[]"
)
_FIELD_DEFAULT = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(True|False|None|\d+)\s*,?\s*$"
)


_CODEISH = re.compile(
    r"^(if|elif|else|for|while|return|def\s|class\s|with\s|try|raise|assert|"
    r"self\.|cls\.|async\s|await\s)"
)


def _field_anchors(hunk: PatchHunk | None) -> set[str]:
    """Config field names (``lazy_parse: bool``, ``lazy_parse=False``).

    Returns empty when the hunk looks like a real method body (if/def/self.)
    so large implementations never coalesce on a shared field name.
    """
    if hunk is None:
        return set()
    out: set[str] = set()
    codeish = 0
    for t in hunk.add_texts:
        s = t.strip()
        m = _FIELD_ANNOT.match(s) or _FIELD_DEFAULT.match(s)
        if m:
            out.add(m.group(1))
            continue
        if _CODEISH.match(s):
            codeish += 1
    if codeish > 2:
        return set()
    return out


def _hunk_has_signal(hunk: PatchHunk | None) -> bool:
    """True if hunk has any signal add *or* del line (normative HasSignal).

    Signal line: non-blank, not comment-only, not pure punctuation, not a
    generic return/call token (see ``_is_signal_line``). Pure deletes of
    real code count as signal.
    """
    if hunk is None:
        return False
    if any(_is_signal_line(t) for t in hunk.add_texts):
        return True
    return any(_is_signal_line(t) for t in hunk.del_texts)


def _decision_has_signal(d: RegionDecision) -> bool:
    """Site has signal if any present model contributes a signal hunk."""
    return any(_hunk_has_signal(h) for h in d.hunks)



def _hunk_base_span(hunk: PatchHunk) -> tuple[int, int]:
    """Half-open base interval ``[old_start, old_start+old_count)``.

    Pure inserts (``old_count == 0``) are a point interval ``(start, start)``
    that only overlaps other points at the same start (or interior of a
    non-empty span).
    """
    start = max(0, int(hunk.old_start))
    n = int(hunk.old_count)
    if n <= 0:
        return (start, start)
    return (start, start + n)


def _spans_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    a0, a1 = a
    b0, b1 = b
    if a0 == a1 and b0 == b1:
        return a0 == b0
    if a0 == a1:
        return b0 <= a0 < b1 if b0 < b1 else a0 == b0
    if b0 == b1:
        return a0 <= b0 < a1
    return a0 < b1 and b0 < a1


def _decisions_span_related(d1: RegionDecision, d2: RegionDecision) -> bool:
    spans1 = [_hunk_base_span(h) for h in d1.hunks if h is not None]
    spans2 = [_hunk_base_span(h) for h in d2.hunks if h is not None]
    return any(_spans_overlap(s1, s2) for s1 in spans1 for s2 in spans2)


def _merge_decision_slots(d1: RegionDecision, d2: RegionDecision) -> RegionDecision | None:
    """Merge two sites if no model has conflicting body keys. Else None."""
    hunks: list[PatchHunk | None] = list(d1.hunks)
    for m in range(3):
        h1, h2 = hunks[m], d2.hunks[m]
        if h1 is None and h2 is not None:
            hunks[m] = h2
        elif h1 is not None and h2 is not None and h1.body_key != h2.body_key:
            return None
    ht = (hunks[0], hunks[1], hunks[2])
    return RegionDecision(
        old_start=min(d1.old_start, d2.old_start),
        label=d1.label or d2.label,
        hunks=ht,
        relation=_classify_region_relation(ht),
        site_count=d1.site_count + d2.site_count,
    )


def _span_coalesce_decisions(decisions: list[RegionDecision]) -> list[RegionDecision]:
    """Merge sites whose base spans overlap and slots do not conflict.

    Fully specified co-location improvement over exact ``old_start`` equality:
    connected components under span-overlap (with body-key conflict check).
    """
    if len(decisions) <= 1:
        return list(decisions)
    n = len(decisions)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(n):
        for j in range(i + 1, n):
            if not _decisions_span_related(decisions[i], decisions[j]):
                continue
            # Tentative merge check
            if _merge_decision_slots(decisions[i], decisions[j]) is None:
                continue
            union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    out: list[RegionDecision] = []
    for idxs in groups.values():
        idxs_sorted = sorted(idxs, key=lambda k: decisions[k].old_start)
        merged = decisions[idxs_sorted[0]]
        for k in idxs_sorted[1:]:
            nxt = _merge_decision_slots(merged, decisions[k])
            if nxt is None:
                # Conflict after multi-way glue: keep separate (shouldn't if UF sound)
                out.append(decisions[k])
                continue
            merged = nxt
        out.append(merged)
    out.sort(key=lambda d: d.old_start)
    return out


def _coalesce_decisions(decisions: list[RegionDecision]) -> list[RegionDecision]:
    """Optional presentation normalizer (non-normative product layer).

    Not part of the core site model. Does not feed path badges (policy A).

    1. Drop sites with no signal adds (blank / punctuation noise).
    2. Same design body at multiple line numbers collapses with ``site_count``.
    3. Complementary coverage under the same location label may merge slots.
    """
    decisions = [d for d in decisions if _decision_has_signal(d)]
    if len(decisions) <= 1:
        return decisions

    # --- Pass 1: merge identical fingerprints (ignore line number) ---
    by_fp: dict[tuple, list[RegionDecision]] = {}
    fp_order: list[tuple] = []
    for d in decisions:
        fp = (d.relation, d.body_fingerprint())
        # Never merge empty fingerprints (should be filtered already).
        if fp not in by_fp:
            by_fp[fp] = []
            fp_order.append(fp)
        by_fp[fp].append(d)
    pass1: list[RegionDecision] = []
    for fp in fp_order:
        group = by_fp[fp]
        primary = group[0]
        if len(group) == 1:
            pass1.append(primary)
            continue
        pass1.append(
            RegionDecision(
                old_start=primary.old_start,
                label=primary.label,
                hunks=primary.hunks,
                relation=primary.relation,
                site_count=sum(g.site_count for g in group),
            )
        )

    # --- Pass 2: complementary coverage, same signal content ---
    used: set[int] = set()
    pass2: list[RegionDecision] = []
    for i, d in enumerate(pass1):
        if i in used:
            continue
        loc = _decision_loc_key(d)
        merged_hunks: list[PatchHunk | None] = list(d.hunks)
        site_count = d.site_count
        d_sigs = {_adds_signature(h) for h in d.hunks if h is not None}
        d_sigs.discard(())
        for j in range(i + 1, len(pass1)):
            if j in used:
                continue
            other = pass1[j]
            if _decision_loc_key(other) != loc:
                continue
            o_sigs = {_adds_signature(h) for h in other.hunks if h is not None}
            o_sigs.discard(())
            d_anchors = set()
            o_anchors = set()
            for h in merged_hunks:
                d_anchors |= _field_anchors(h)
            for h in other.hunks:
                o_anchors |= _field_anchors(h)
            same_content = bool(d_sigs and o_sigs and not d_sigs.isdisjoint(o_sigs))
            same_field = bool(d_anchors and o_anchors and not d_anchors.isdisjoint(o_anchors))
            if not same_content and not same_field:
                continue
            # Field-anchor-only merges (docs differ, same ``lazy_parse:``):
            # allow only/same plumbing, or filling an empty slot on a diverge
            # (C's ConfigDict docs at another line → third design card).
            if (
                not same_content
                and (d.relation == "diverge" or other.relation == "diverge")
                and d.relation != "only"
                and other.relation != "only"
            ):
                continue
            conflict = False
            for m in range(3):
                mh, oh = merged_hunks[m], other.hunks[m]
                if mh is None or oh is None:
                    continue
                if _adds_signature(mh) == _adds_signature(oh):
                    continue
                ma, oa = _field_anchors(mh), _field_anchors(oh)
                if ma and oa and ma.isdisjoint(oa):
                    conflict = True
                    break
                # Same field name, different docs — OK to keep first body.
            if conflict:
                continue
            filled = False
            for m in range(3):
                if merged_hunks[m] is None and other.hunks[m] is not None:
                    merged_hunks[m] = other.hunks[m]
                    filled = True
            if not filled:
                # Other only repeats a slot we already have — leave it for pass1
                # site-count merge, don't glue unrelated bodies under one region.
                continue
            site_count += other.site_count
            used.add(j)
            d_sigs |= o_sigs
        used.add(i)
        hunks_t = (merged_hunks[0], merged_hunks[1], merged_hunks[2])
        pass2.append(
            RegionDecision(
                old_start=d.old_start,
                label=d.label,
                hunks=hunks_t,
                relation=_classify_region_relation(hunks_t),
                site_count=site_count,
            )
        )
    return pass2


def compare_shared_file(
    models: list[ModelData],
    filename: str,
    *,
    aliases: dict[str, str] | None = None,
) -> SharedFileCompare:
    """Build comparison data for one filename (missing models get empty patches)."""
    patches = _patch_for(models[:3], filename, aliases=aliases)
    while len(patches) < 3:
        patches.append("")
    patches_t = (patches[0], patches[1], patches[2])
    empty = tuple(not p.strip() for p in patches_t)
    identical = _identical_among_present(patches_t)
    adds = (
        _added_lines(patches_t[0]),
        _added_lines(patches_t[1]),
        _added_lines(patches_t[2]),
    )
    dels = (
        _deleted_lines(patches_t[0]),
        _deleted_lines(patches_t[1]),
        _deleted_lines(patches_t[2]),
    )
    empty_t = (bool(empty[0]), bool(empty[1]), bool(empty[2]))
    multi = sum(1 for e in empty_t if not e) >= 2
    if identical or not multi:
        core: list[RegionDecision] = []
        display: list[RegionDecision] = []
    else:
        core = build_core_region_decisions(patches_t, empty_t)
        # Presentation pipeline (fully specified normalizers):
        #   1. HasSignal filter  2. span-overlap merge  3. fingerprint+label coalesce
        display = _coalesce_decisions(
            _span_coalesce_decisions([d for d in core if _decision_has_signal(d)])
        )
    return SharedFileCompare(
        filename=filename,
        patches=patches_t,
        identical_patches=identical,
        empty_models=empty_t,
        adds=adds,
        dels=dels,
        core_decisions=core,
        decisions=display,
    )


# Review-first kind order for the file list (stable within each kind by path).
_LIST_KIND_ORDER: dict[str, int] = {
    "diff": 0,   # designs disagree — review first
    "share": 1,  # partial agreement
    "new": 2,    # full-file adds
    "del": 3,    # full-file deletes
    "same": 4,   # identical edits
    "solo": 5,   # one model only
}

# Within a kind, group by who touched the path (most models first).
_COVERAGE_ORDER: dict[str, int] = {
    "ABC": 0,
    "AB": 1,
    "AC": 2,
    "BC": 3,
    "A": 4,
    "B": 5,
    "C": 6,
}


def _compare_sort_key(cmp: SharedFileCompare) -> tuple:
    """Stable list order: kind → model coverage → full path.

    Coverage grouping lets the UI show one header per model set instead of
    repeating A/B/C on every row.
    """
    kind = cmp.kind_badge()
    cov = cmp.coverage or "?"
    return (
        _LIST_KIND_ORDER.get(kind, 9),
        _COVERAGE_ORDER.get(cov, 9),
        cmp.filename.lower(),
    )


def compare_all_shared(models: list[ModelData]) -> list[SharedFileCompare]:
    """Compare every path any model touched (union), stable kind+coverage+path.

    Rename aliases: if any model renames old→new, both names map to *new* so
    models that still edit *old* participate in the same comparison key.
    Paths that only appear as rename sources are still listed under the
    canonical (target) name when a tip exists there.
    """
    aliases = path_aliases_from_models(models)
    raw_names = union_filenames(models)
    # Canonicalize; keep stable unique set
    names = sorted({canonical_path(n, aliases) for n in raw_names})
    compares = [
        c
        for c in (compare_shared_file(models, fn, aliases=aliases) for fn in names)
        if c.present_indices
    ]
    compares.sort(key=_compare_sort_key)
    return compares


def path_matches_filter(cmp: SharedFileCompare, filter_mode: str) -> bool:
    """Whether a path should appear under a detail/list filter mode.

    Filters are **path-level** selection over core site relations and badges:

    * ``all`` — every path
    * ``consensus`` — badge share/same, or any core site relation ``same``
    * ``unique`` — solo/new/del badge, or any core site relation ``only``
    * ``pairs`` — any core site relation ``pair``
    * ``diverge`` — badge ``diff``

    Detail sections still apply the same mode *within* a selected path.
    """
    mode = (filter_mode or "all").lower()
    if mode == "all":
        return True
    badge = cmp.kind_badge()
    core = cmp.core_decisions
    if mode in ("consensus", "shared"):
        if badge in ("share", "same"):
            return True
        return any(d.relation == "same" for d in core)
    if mode == "unique":
        if badge in ("solo", "new", "del"):
            return True
        return any(d.relation == "only" for d in core)
    if mode in ("pairs", "pair"):
        return any(d.relation == "pair" for d in core)
    if mode == "diverge":
        return badge == "diff"
    return True


@dataclass(slots=True)
class CompareListEntry:
    """One OptionList row: group header or file.

    *header* rows are disabled separators; *file* rows carry ``compare_index``
    into the sorted compares list.
    """

    is_header: bool
    compare_index: int = -1
    kind: str = ""
    coverage: str = ""
    count: int = 0


def build_compare_list_entries(
    compares: list[SharedFileCompare],
) -> list[CompareListEntry]:
    """Build header+file entries for consecutive kind x coverage groups."""
    if not compares:
        return []
    entries: list[CompareListEntry] = []
    i = 0
    n = len(compares)
    while i < n:
        kind = compares[i].kind_badge()
        cov = compares[i].coverage or "?"
        j = i + 1
        while (
            j < n
            and compares[j].kind_badge() == kind
            and (compares[j].coverage or "?") == cov
        ):
            j += 1
        entries.append(
            CompareListEntry(
                is_header=True,
                kind=kind,
                coverage=cov,
                count=j - i,
            )
        )
        for k in range(i, j):
            entries.append(
                CompareListEntry(
                    is_header=False,
                    compare_index=k,
                    kind=kind,
                    coverage=cov,
                )
            )
        i = j
    return entries


def list_coverage_header_prompt(
    coverage: str,
    kind: str,
    count: int,
    model_colors: dict[str, str] | None = None,
) -> object:
    """Rich prompt for a model-coverage group header (disabled list option)."""
    from rich.text import Text

    from sfctl.badges import append_kind_column

    colors = model_colors or {}
    t = Text()
    # Large, visible model letters first.
    cov = coverage or "?"
    for i, ch in enumerate(cov):
        if i:
            t.append(" ")
        if ch in colors:
            t.append(f" {ch} ", style=f"bold {colors[ch]} reverse")
        elif ch in "ABC":
            t.append(f" {ch} ", style="bold reverse")
        else:
            t.append(f" {ch} ", style="bold")
    t.append("  ")
    append_kind_column(t, kind)
    # Letters already say who is involved; only add the file count.
    t.append(f"  {count} file" + ("s" if count != 1 else ""), style="dim")
    return t


def patch_file_diff(cmp: SharedFileCompare, model_idx: int) -> FileDiff:
    """FileDiff for one model's patch of this shared file."""
    text = cmp.patches[model_idx] if 0 <= model_idx < 3 else ""
    return FileDiff(filename=cmp.filename, diff=text)


def jump_line_for_model(cmp: SharedFileCompare, model_idx: int) -> str | None:
    """Pick a distinctive added line to scroll to in that model's full patch.

    Prefers unique-to-model signal lines, then agreement lines, then any add.
    Used when leaving the shared-compare modal via 1/2/3.
    """
    if model_idx < 0 or model_idx > 2:
        return None
    if cmp.empty_models[model_idx]:
        return None
    only = cmp.only_adds[model_idx]
    if only:
        return max(only, key=len)
    if cmp.agreement_adds:
        return max(cmp.agreement_adds, key=len)
    adds = [ln for ln in _added_lines(cmp.patches[model_idx]) if ln.strip()]
    if adds:
        return max(adds, key=len)
    # Full-file deletes have no adds — jump to a distinctive removed line.
    dels = [ln for ln in _deleted_lines(cmp.patches[model_idx]) if ln.strip()]
    if dels:
        return max(dels, key=len)
    return None


@dataclass(slots=True)
class CompareSection:
    """One card, banner, or tabbed multi-design unit in the detail pane."""

    key: str
    title: str
    model_letter: str
    snip_diffs: list[str] = field(default_factory=list)
    part_labels: list[str] = field(default_factory=list)
    kind: str = ""  # same | only | pair | diverge | new | solo | identical | region
    collapsed: bool = False
    # Banner: groups the design cards that follow (no diff body).
    is_banner: bool = False
    # Multi-design chunk: one site in linear scroll, designs as tabs
    # ``[(tab_label, [unified_snip, ...]), ...]``. When set, render as
    # TabbedContent under *title* instead of stacked collapsibles.
    design_tabs: list[tuple[str, list[str]]] = field(default_factory=list)

    def all_snip_texts(self) -> list[str]:
        """Unified snips from a single card or all design tabs."""
        if self.design_tabs:
            out: list[str] = []
            for _lab, snips in self.design_tabs:
                out.extend(snips)
            return out
        return list(self.snip_diffs)


def snippet_to_unified(snip: PatchSnippet) -> str:
    """Turn a PatchSnippet into unified-diff text for DiffDisplay / yank."""
    out: list[str] = []
    for mark, text in snip.rows:
        if mark == "@":
            if text.startswith("@@"):
                out.append(text)
            else:
                out.append(f"@@ {text} @@")
        elif mark == "+":
            out.append(f"+{text}")
        elif mark == "-":
            out.append(f"-{text}")
        else:
            out.append(f" {text}")
    return "\n".join(out)


def compare_header_markup(
    cmp: SharedFileCompare,
    *,
    model_colors: dict[str, str] | None = None,
    filter_mode: str = "all",
) -> str:
    """Filename + kind badge + stats for the shared-compare detail header.

    Coverage letters live on the file list row only. No howto line — the
    footer keys hint and section banners already explain how to read cards.
    """
    from sfctl.badges import path_badge_markup

    colors = model_colors or {}
    badge = cmp.kind_badge()
    # Same chip family as list rows and CQ (see sfctl.badges).
    meta_parts = [path_badge_markup(badge)]
    missing = [model_letter(i) for i, e in enumerate(cmp.empty_models) if e]
    if missing:
        meta_parts.append(f"no {_paint_letters(''.join(missing), colors)}")
    for tok in cmp._stats_bits():
        if tok.startswith("no "):
            continue  # already shown above
        meta_parts.append(_paint_stat_token(tok, colors))
    if filter_mode != "all":
        filt = {
            "consensus": "Shared",
            "unique": "Unique",
            "pairs": "Pairs",
            "diverge": "Diff",
        }.get(filter_mode, filter_mode.title())
        meta_parts.append(f"[dim]Filter: {filt}[/]")
    return (
        f"[{_STYLE_HEAD}]{cmp.filename}[/]\n"
        f"{' · '.join(meta_parts)}"
    )

def _loc_from_label(label: str) -> str:
    """Symbol-ish name from a unified-diff hunk header label, or \"\"."""
    lab = (label or "").strip()
    lab = re.sub(r"\s*\{?\s*$", "", lab)
    if not lab or lab in "{}" or lab.isdigit():
        return ""
    # Go/Python/JS-ish: func (recv) Name( → Name; func Name( → Name
    m = re.search(r"\bfunc\s+(?:\([^)]*\)\s*)?(\w+)", lab)
    if m:
        return m.group(1)
    m = re.search(r"\btype\s+(\w+)", lab)
    if m:
        return m.group(1)
    m = re.search(r"\b(?:def|class|interface|struct|enum)\s+(\w+)", lab)
    if m:
        return m.group(1)
    # Top-level const/var (Go, JS) when the header carried the declaration.
    m = re.search(r"\b(?:const|var|let|val)\s+(\w+)", lab)
    if m:
        return m.group(1)
    if len(lab) > 36:
        return lab[:33] + "…"
    return lab


def _decision_loc_key(dec: RegionDecision) -> str:
    """Stable location identity for clustering / coalesce (not user-facing).

    Uses the hunk symbol when present; otherwise anchors on old_start so two
    unlabeled global edits at different lines never merge by accident.
    """
    sym = _loc_from_label(dec.label)
    if sym:
        return sym
    return f"@{dec.old_start}"


def _decision_loc(dec: RegionDecision) -> str:
    """Short, human location tag for section titles (func/type name preferred).

    Unlabeled / global-scope hunks use a content preview (first signal add or
    del), never a bare ``L7`` line number.
    """
    sym = _loc_from_label(dec.label)
    if sym:
        return sym
    for h in dec.hunks:
        if h is None:
            continue
        prev = _first_signal_preview(h.add_texts, max_len=36) or _first_signal_preview(
            h.del_texts, max_len=36
        )
        if prev:
            return prev
    if dec.old_start <= 1:
        return "top"
    return "edit"


def _placeholder_unified(msg: str) -> str:
    """Display stub when a patch has no textual add/del lines."""
    return f"@@ empty @@\n# {msg}"


def _drop_leading_comment_preamble(adds: list[str]) -> list[str]:
    """Drop a leading run of blank/comment-only lines from a new-file body.

    Keeps shebang. Closes ``/* … */`` and ``<!-- … -->`` blocks so a whole
    header comment is removed without keyword matching.
    """
    if not adds:
        return adds
    i = 0
    while i < len(adds) and not adds[i].strip():
        i += 1
    if i >= len(adds):
        return []

    shebang: str | None = None
    if adds[i].startswith("#!"):
        shebang = adds[i]
        i += 1
        while i < len(adds) and not adds[i].strip():
            i += 1
        if i >= len(adds):
            return [shebang]

    while i < len(adds):
        s = adds[i].strip()
        if not s:
            i += 1
            continue
        if not _is_comment_only_line(adds[i]):
            break
        # Consume a whole block comment in one step when opened here.
        if s.startswith("/*"):
            if "*/" not in s:
                i += 1
                while i < len(adds) and "*/" not in adds[i]:
                    i += 1
                if i < len(adds):
                    i += 1
            else:
                i += 1
            continue
        if s.startswith("<!--"):
            if "-->" not in s:
                i += 1
                while i < len(adds) and "-->" not in adds[i]:
                    i += 1
                if i < len(adds):
                    i += 1
            else:
                i += 1
            continue
        i += 1

    while i < len(adds) and not adds[i].strip():
        i += 1
    body = adds[i:]
    return [shebang, "", *body] if shebang else body


def _common_prefix_len(seqs: list[list[str]]) -> int:
    """Longest run of identical leading lines shared by every sequence."""
    if not seqs or any(not s for s in seqs):
        return 0
    n = 0
    for cols in zip(*seqs, strict=False):
        if len(set(cols)) != 1:
            break
        n += 1
    return n


def _preview_rank(line: str) -> int:
    """Lower is better for section-title previews."""
    s = line.strip()
    if re.match(r"^(async\s+)?def\s|class\s|@[A-Za-z_]", s):
        return 0
    if re.match(
        r"^(return\s|raise\s|await\s|if\s|for\s|while\s|with\s|"
        r"[A-Za-z_][\w.]*(?:\s*,\s*[A-Za-z_][\w.]*)*\s*=|"
        r"[A-Za-z_][\w.]*\()",
        s,
    ):
        return 1
    if s.startswith("from ") or s.startswith("import "):
        return 3
    if s.startswith("package "):
        return 4
    return 2  # docstring prose / other


def _first_signal_preview(add_texts: list[str], *, max_len: int = 44) -> str:
    """One scannable phrase for collapsible titles (skips comments/shell).

    Prefers code-shaped lines so two docstring rewrites do not both title as
    bare ``\"\"\"`` (or other identical delimiters).
    """
    best: str | None = None
    best_rank = 99
    for ln in add_texts:
        if not _is_signal_line(ln):
            continue
        s = ln.strip()
        if s.startswith("package "):
            continue
        rank = _preview_rank(s)
        if rank < best_rank:
            best_rank = rank
            best = s
            if rank == 0:
                break
    if not best:
        return ""
    if len(best) > max_len:
        return best[: max_len - 1] + "…"
    return best


def _cluster_region_decisions(
    decisions: list[RegionDecision],
) -> list[list[RegionDecision]]:
    """Group consecutive decisions that share location + model coverage.

    Large functions often span several hunks (docstring rewrite, then a
    call-site rename). Showing each as its own ``· 2 designs`` banner makes
    alternate designs look duplicated. Clustering keeps one banner per site
    with every hunk for that model under the same card.
    """
    clusters: list[list[RegionDecision]] = []
    for d in decisions:
        if not clusters:
            clusters.append([d])
            continue
        head = clusters[-1][0]
        same_loc = _decision_loc_key(head) == _decision_loc_key(d)
        same_cov = head.present_indices == d.present_indices
        # Keep single-model (unique) and multi-model clusters separate.
        same_arity = (len(head.present_indices) == 1) == (len(d.present_indices) == 1)
        if same_loc and same_cov and same_arity:
            clusters[-1].append(d)
        else:
            clusters.append([d])
    return clusters


def _cluster_body_key(
    cluster: list[RegionDecision], model_idx: int
) -> tuple[tuple[str, ...] | None, ...]:
    """Fingerprint of all hunks a model contributes in a decision cluster."""
    parts: list[tuple[str, ...] | None] = []
    for dec in cluster:
        h = dec.hunks[model_idx]
        parts.append(None if h is None else h.body_key)
    return tuple(parts)


def _cluster_hunks(
    cluster: list[RegionDecision], model_idx: int
) -> list[PatchHunk]:
    return [dec.hunks[model_idx] for dec in cluster if dec.hunks[model_idx] is not None]


def _normalize_add_lines(adds: list[str]) -> list[str]:
    """Non-blank add texts with CR stripped (design identity, not encoding)."""
    out: list[str] = []
    for t in adds:
        nt = t.replace("\r", "")
        if nt.strip():
            out.append(nt)
    return out


def _factor_near_identical_new_designs(
    labeled_adds: list[tuple[str, list[str]]],
) -> tuple[list[str], dict[str, list[str]]] | None:
    """Factor near-identical multi-model new files into Shared + uniques.

    Only applies when every design is mostly the same line set (high shared
    ratio on the shortest design) and unique lines are a minority. Avoids
    token-set Shared soup for largely different full-file designs, and never
    runs on mid-file edit hunks (caller is the new-file gallery only).
    """
    if len(labeled_adds) < 2:
        return None
    per_lab: dict[str, list[str]] = {}
    for lab, adds in labeled_adds:
        cleaned = _normalize_add_lines(adds)
        if not cleaned:
            return None
        per_lab[lab] = cleaned
    sets = [set(v) for v in per_lab.values()]
    shared_set = set.intersection(*sets)
    # Small files / single shared token: keep full per-model cards.
    if len(shared_set) < 3:
        return None
    min_len = min(len(v) for v in per_lab.values())
    min_ratio = len(shared_set) / min_len
    if min_ratio < 0.65:
        return None
    first_lab = labeled_adds[0][0]
    shared_lines = [ln for ln in per_lab[first_lab] if ln in shared_set]
    unique_by_lab = {
        lab: [ln for ln in adds if ln not in shared_set]
        for lab, adds in per_lab.items()
    }
    max_unique = max((len(u) for u in unique_by_lab.values()), default=0)
    # Uniques must be a minority of the shared body (near-identical wrappers).
    if max_unique > max(1, len(shared_lines) // 2):
        return None
    return shared_lines, unique_by_lab


def _hunk_snip_trimmed(hunk: PatchHunk, max_rows: int = _SNIPPET_MAX_ROWS) -> str:
    text = hunk.to_unified()
    rows = text.splitlines()
    if len(rows) <= max_rows:
        return text
    keep = max_rows // 2
    return "\n".join([*rows[:keep], "…", *rows[-keep:]])


def _patch_body_snips_and_title(
    patch: str,
    label: str,
    *,
    max_rows: int = _SNIPPET_MAX_ROWS,
) -> tuple[list[str], str]:
    """Full-hunk snips + a size/preview title for pure-delete or empty patches.

    Used when add-target extraction has nothing to latch onto (identical
    file deletions, solo removes) so the card is not a blank marker stub.
    """
    adds = _added_lines(patch)
    dels = _deleted_lines(patch)
    n_add = _nonempty_line_count(adds)
    n_del = _nonempty_line_count(dels)
    hunks = parse_patch_hunks(patch, 0)
    if hunks:
        snips = [_hunk_snip_trimmed(h, max_rows=max_rows) for h in hunks]
        prev = _first_signal_preview(adds) or _first_signal_preview(dels)
        size = "  ".join(_plus_minus(n_add, n_del))
        if n_del and not n_add:
            base = f"{label}  —  delete · -{n_del}"
        elif size:
            base = f"{label}  —  {size}"
        else:
            base = f"{label}  —  empty"
        title = f"{base}  ·  {prev}" if prev else base
        return snips, title
    if n_add:
        prev = _first_signal_preview(adds)
        title = f"{label}  —  +{n_add}"
        if prev:
            title = f"{title}  ·  {prev}"
        return [_unified_from_adds(adds, max_rows=max_rows)], title
    return (
        [_placeholder_unified("empty, binary, or marker-only patch")],
        f"{label}  —  empty",
    )


def _unified_from_adds(
    adds: list[str],
    *,
    max_rows: int = _SNIPPET_MAX_ROWS,
    skip: int = 0,
) -> str:
    """Build a display unified block from pure-add lines (new files).

    ``skip`` drops a leading common prefix (shared across models). Leading
    comment-only rows are then trimmed so cards open on code, not headers.
    """
    body_lines = list(adds[skip:]) if skip else list(adds)
    clean = _drop_leading_comment_preamble(body_lines)
    if not clean:
        return _placeholder_unified("empty after stripping comment preamble")
    body = [f"+{ln}" for ln in clean]
    n = len(body)
    head = f"@@ -0,0 +1,{n} @@"
    rows = [head, *body]
    if len(rows) > max_rows:
        keep = max_rows // 2
        rows = [*rows[:keep], "…", *rows[-keep:]]
    return "\n".join(rows)


def _strip_leading_comment_preamble_unified(unified: str) -> str:
    """Drop leading comment-only +add lines from a pure-add unified snip."""
    rows = unified.splitlines()
    if not rows:
        return unified
    out: list[str] = []
    i = 0
    if rows[0].startswith("@@"):
        out.append(rows[0])
        i = 1
    adds: list[str] = []
    rest: list[str] = []
    for line in rows[i:]:
        if line.startswith("+") and not rest:
            adds.append(line[1:])
        else:
            rest.append(line)
    if rest:
        # Mixed ctx/del snip — leave intact (edit hunks).
        return unified
    stripped = _drop_leading_comment_preamble(adds)
    out.extend(f"+{ln}" for ln in stripped)
    return "\n".join(out) if out else unified


def build_compare_sections(
    cmp: SharedFileCompare,
    *,
    filter_mode: str = "all",
    model_colors: dict[str, str] | None = None,
) -> list[CompareSection]:
    """Build selectable section groups for the shared-compare detail pane."""
    colors = model_colors or {}
    max_snips = _SNIPPETS_ALL if filter_mode == "all" else _SNIPPETS_FILTER
    sections: list[CompareSection] = []

    def _finish() -> list[CompareSection]:
        return _paint_section_titles(sections, colors)

    def _add(
        key: str,
        title: str,
        model_idx: int,
        targets: list[str],
        patch: str,
        *,
        window: str = "shared",
    ) -> None:
        snips = extract_snippets(
            patch,
            set(targets),
            max_snippets=max_snips,
            max_rows=_SNIPPET_MAX_ROWS,
            window=window,
        )
        letter = model_letter(model_idx) if 0 <= model_idx < 3 else "A"
        bodies = [snippet_to_unified(s) for s in snips]
        # New-file pure-add windows often open on a shared comment header;
        # drop leading comment-only rows so the card starts on code.
        if cmp.is_all_new:
            bodies = [_strip_leading_comment_preamble_unified(b) for b in bodies]
        sections.append(
            CompareSection(
                key=key,
                title=title,
                model_letter=letter,
                snip_diffs=bodies,
            )
        )

    def _add_hunks(
        key: str,
        title: str,
        model_idx: int,
        hunks: list[PatchHunk],
        *,
        kind: str = "",
        collapsed: bool = False,
        part_labels: list[str] | None = None,
        with_preview: bool = True,
    ) -> None:
        if not hunks:
            return
        letter = model_letter(model_idx)
        disp_title = title
        if with_preview:
            for h in hunks:
                prev = _first_signal_preview(h.add_texts)
                if prev and prev not in title:
                    disp_title = f"{title}  —  {prev}"
                    break
        sections.append(
            CompareSection(
                key=key,
                title=disp_title,
                model_letter=letter,
                snip_diffs=[_hunk_snip_trimmed(h) for h in hunks],
                part_labels=part_labels if part_labels is not None else [letter],
                kind=kind,
                collapsed=collapsed,
            )
        )

    def _add_hunk(
        key: str,
        title: str,
        model_idx: int,
        hunk: PatchHunk,
        *,
        kind: str = "",
        collapsed: bool = False,
        part_labels: list[str] | None = None,
        with_preview: bool = True,
    ) -> None:
        _add_hunks(
            key,
            title,
            model_idx,
            [hunk],
            kind=kind,
            collapsed=collapsed,
            part_labels=part_labels,
            with_preview=with_preview,
        )

    def _title_with_cluster_sites(base: str, cluster: list[RegionDecision]) -> str:
        sites = sum(d.site_count for d in cluster)
        if sites > 1:
            return f"{base}  (x{sites} sites)"
        return base

    if cmp.is_solo:
        idx = cmp.present_indices[0]
        letter = model_letter(idx)
        if cmp.is_all_new or is_new_file_patch(cmp.patches[idx]):
            adds = _added_lines(cmp.patches[idx])
            prev = _first_signal_preview(adds)
            title = f"{letter}  —  {prev}" if prev else (
                f"{letter}  —  empty file" if not adds else letter
            )
            snip = (
                _unified_from_adds(adds)
                if adds
                else _placeholder_unified("empty new file (no text lines)")
            )
            sections.append(
                CompareSection(
                    key=f"solo-{letter}",
                    title=title,
                    model_letter=letter,
                    snip_diffs=[snip],
                    part_labels=[],
                    kind="solo",
                    collapsed=False,
                )
            )
            return _finish()
        patch = cmp.patches[idx]
        targets = _signal_lines(cmp.adds[idx]) or _added_lines(patch)
        if not targets:
            # Pure delete, binary, or empty: still surface delete hunks when present.
            card_kind = "deleted" if is_deleted_file_patch(patch) else "solo"
            snips, title = _patch_body_snips_and_title(
                patch, "Del" if card_kind == "deleted" else letter
            )
            sections.append(
                CompareSection(
                    key=f"solo-{letter}",
                    title=title,
                    model_letter=letter,
                    snip_diffs=snips,
                    part_labels=[],
                    kind=card_kind,
                    collapsed=False,
                )
            )
            return _finish()
        _add(
            f"solo-{letter}",
            letter,
            idx,
            targets,
            patch,
            window="full",
        )
        sections[-1].kind = "solo"
        sections[-1].part_labels = []
        if not sections[-1].snip_diffs:
            snips, title = _patch_body_snips_and_title(patch, letter)
            sections[-1].title = title
            sections[-1].snip_diffs = snips
        return _finish()

    if cmp.is_all_deleted:
        # Full-file delete — first-class like ``new`` (badge del, removed body).
        if cmp.n_models >= 2 and not cmp.identical_patches:
            sections.append(
                CompareSection(
                    key="del-head",
                    title="Alternate deletes — read A, then B, then C",
                    model_letter="A",
                    snip_diffs=[],
                    kind="region",
                    is_banner=True,
                )
            )
            for idx in cmp.present_indices:
                letter = model_letter(idx)
                snips, title = _patch_body_snips_and_title(
                    cmp.patches[idx], letter
                )
                sections.append(
                    CompareSection(
                        key=f"del-{letter}",
                        title=title,
                        model_letter=letter,
                        snip_diffs=snips,
                        part_labels=[],
                        kind="deleted",
                    )
                )
            return _finish()
        src_idx = cmp.present_indices[0] if cmp.present_indices else 0
        snips, title = _patch_body_snips_and_title(cmp.patches[src_idx], "Del")
        sections.append(
            CompareSection(
                key="deleted",
                title=title,
                model_letter=model_letter(src_idx),
                snip_diffs=snips,
                part_labels=[],
                kind="deleted",
            )
        )
        return _finish()

    if cmp.identical_patches:
        src_idx = cmp.present_indices[0] if cmp.present_indices else 0
        patch = cmp.patches[src_idx]
        adds = _added_lines(patch)
        signal_adds = _signal_lines(adds)
        # Prefer add-target extraction when there are insertions (keeps ctx).
        if signal_adds or adds:
            targets = signal_adds or adds
            prev = _first_signal_preview(adds)
            n_add = _nonempty_line_count(adds)
            n_del = _nonempty_line_count(_deleted_lines(patch))
            size = "  ".join(_plus_minus(n_add, n_del))
            title = "Same"
            if size:
                title = f"Same  —  {size}"
            if prev:
                title = f"{title}  ·  {prev}" if size else f"Same  —  {prev}"
            _add(
                "identical",
                title,
                src_idx,
                targets,
                patch,
                window="full",
            )
            sections[-1].kind = "identical"
            sections[-1].part_labels = []
            if sections[-1].snip_diffs:
                return _finish()
            # Fall through to full-hunk render if target extract failed.
            sections.pop()
        snips, title = _patch_body_snips_and_title(patch, "Same")
        sections.append(
            CompareSection(
                key="identical",
                title=title,
                model_letter=model_letter(src_idx),
                snip_diffs=snips,
                part_labels=[],
                kind="identical",
            )
        )
        return _finish()

    # Large multi-model new files: per-model gallery. Near-identical designs
    # (high line-set overlap, few uniques) factor into Shared + unique cards;
    # otherwise drop identical leading lines so cards open on diffs.
    if cmp.prefer_per_model_new_files:
        per_adds = {
            idx: _added_lines(cmp.patches[idx]) for idx in cmp.present_indices
        }
        labeled_adds = [
            (model_letter(idx), per_adds[idx]) for idx in cmp.present_indices
        ]
        factored = _factor_near_identical_new_designs(labeled_adds)
        if factored is not None:
            shared_lines, unique_by_lab = factored
            n_unique = sum(1 for u in unique_by_lab.values() if u)
            show_shared = filter_mode in ("all", "consensus")
            show_uniq = filter_mode in ("all", "unique", "diverge")
            sections.append(
                CompareSection(
                    key="new-head",
                    title=(
                        f"shared + {n_unique} unique"
                        if shared_lines and n_unique
                        else "Alternate designs — read A, then B, then C"
                    ),
                    model_letter=labeled_adds[0][0],
                    snip_diffs=[],
                    kind="region",
                    is_banner=True,
                )
            )
            if shared_lines and show_shared:
                # Presentation compression only — not site-local BodyKey agreement.
                prev = _first_signal_preview(shared_lines)
                title = (
                    f"Common lines  —  {prev}" if prev else "Common lines"
                )
                sections.append(
                    CompareSection(
                        key="new-shared",
                        title=title,
                        model_letter=labeled_adds[0][0],
                        snip_diffs=[_unified_from_adds(shared_lines)],
                        part_labels=[],
                        kind="same",
                        collapsed=False,
                    )
                )
            if show_uniq:
                for lab, _adds0 in labeled_adds:
                    uniq = unique_by_lab.get(lab) or []
                    if not uniq:
                        continue
                    prev = _first_signal_preview(uniq)
                    title = f"{lab}  —  {prev}" if prev else lab
                    sections.append(
                        CompareSection(
                            key=f"new-{lab}",
                            title=title,
                            model_letter=lab[0],
                            snip_diffs=[_unified_from_adds(uniq)],
                            part_labels=[],
                            kind="new",
                            collapsed=False,
                        )
                    )
            return _finish()

        skip = _common_prefix_len([per_adds[i] for i in cmp.present_indices])
        # Never hide the whole file when models are fully identical (handled
        # elsewhere); if only comments remain after skip, still show them.
        tabs: list[tuple[str, list[str]]] = []
        for idx in cmp.present_indices:
            letter = model_letter(idx)
            adds = per_adds[idx]
            prev = _first_signal_preview(adds[skip:] if skip else adds)
            if prev and len(prev) > 28:
                prev = prev[:27] + "…"
            tab_lab = f"{letter}  {prev}" if prev else letter
            snip = (
                _unified_from_adds(adds, skip=skip)
                if adds
                else _placeholder_unified("empty new file (no text lines)")
            )
            tabs.append((tab_lab, [snip]))
        if len(tabs) == 1:
            sections.append(
                CompareSection(
                    key=f"new-{tabs[0][0][0]}",
                    title=tabs[0][0],
                    model_letter=tabs[0][0][0],
                    snip_diffs=tabs[0][1],
                    kind="new",
                )
            )
        elif tabs:
            sections.append(
                CompareSection(
                    key="new-tabs",
                    title=f"New file · {len(tabs)} designs",
                    model_letter=tabs[0][0][0],
                    snip_diffs=[],
                    kind="new",
                    design_tabs=tabs,
                )
            )
        return _finish()

    # Base-anchored decisions: one card per base location (stacked bodies).
    # Consecutive hunks at the same site (same loc + coverage) cluster so a
    # multi-hunk function rewrite is one design comparison, not N banners.
    if cmp.use_region_decisions:
        show_same = filter_mode in ("all", "consensus")
        show_unique = filter_mode in ("all", "unique", "diverge")
        clusters = _cluster_region_decisions(cmp.decisions)
        n_clusters = len(clusters)

        def _emit_model_cards(
            labeled_parts: list[tuple[str, list[PatchHunk]]],
            *,
            verb: str,
            force_collapse: bool,
            region_i: int,
            site: str,
            site_count: int,
        ) -> None:
            """One site in linear order; multi-design as tabs (not stacked cards)."""
            if not labeled_parts:
                return
            n_designs = len(labeled_parts)
            site_note = f"  (x{site_count} sites)" if site_count > 1 else ""
            if n_designs == 1:
                lab, hunks0 = labeled_parts[0]
                src = next(
                    (i for i, ch in enumerate("ABC") if ch == lab[0]),
                    0,
                )
                if len(lab) >= 2:
                    _add_hunks(
                        f"pair-{region_i}-{lab}",
                        f"Pair · {site}{site_note}",
                        src,
                        hunks0,
                        kind="pair",
                        collapsed=force_collapse,
                        part_labels=[lab],
                    )
                else:
                    _add_hunks(
                        f"only-{region_i}-{lab}",
                        f"{lab} · {site}{site_note}",
                        src,
                        hunks0,
                        kind="only",
                        collapsed=force_collapse,
                        part_labels=[],
                    )
                return

            # Multiple designs at one base site: keep a single linear anchor
            # (site title) and put alternate bodies in tabs so the scroll
            # order stays path → site → site, not path → A → B → C walls.
            tabs: list[tuple[str, list[str]]] = []
            for lab, hunks0 in labeled_parts:
                snips = [_hunk_snip_trimmed(h) for h in hunks0]
                if not snips:
                    continue
                prev = ""
                for h in hunks0:
                    prev = _first_signal_preview(h.add_texts) or _first_signal_preview(
                        h.del_texts
                    )
                    if prev:
                        break
                if prev and len(prev) > 28:
                    prev = prev[:27] + "…"
                tab_lab = f"{lab}  {prev}" if prev else lab
                tabs.append((tab_lab, snips))
            if not tabs:
                return
            if len(tabs) == 1:
                lab0 = labeled_parts[0][0]
                src = next((i for i, ch in enumerate("ABC") if ch == lab0[0]), 0)
                kind = (
                    "pair"
                    if len(lab0) >= 2
                    else ("diverge" if verb == "diverge" else "only")
                )
                sections.append(
                    CompareSection(
                        key=f"region-{region_i}-{lab0}",
                        title=f"{lab0} · {site}{site_note}",
                        model_letter=lab0[0],
                        snip_diffs=tabs[0][1],
                        kind=kind,
                        collapsed=force_collapse,
                    )
                )
                return
            kind = "pair" if verb == "pair" else "diverge"
            sections.append(
                CompareSection(
                    key=f"region-{region_i}-tabs",
                    title=f"{site} · {len(tabs)} designs{site_note}",
                    model_letter=labeled_parts[0][0][:1],
                    snip_diffs=[],
                    kind=kind,
                    collapsed=False,
                    design_tabs=tabs,
                )
            )

        for ci, cluster in enumerate(clusters):
            dec0 = cluster[0]
            loc = _decision_loc(dec0)
            present = dec0.present_indices
            n_present = len(present)
            collapse_tail = n_clusters > 4 and ci >= 3
            site_count = sum(d.site_count for d in cluster)

            # Group models by full multi-hunk fingerprint across the cluster.
            by_key: dict[tuple, list[int]] = {}
            key_order: list[tuple] = []
            for i in present:
                fk = _cluster_body_key(cluster, i)
                if fk not in by_key:
                    by_key[fk] = []
                    key_order.append(fk)
                by_key[fk].append(i)
            groups = [(k, by_key[k]) for k in key_order]

            if len(groups) == 1 and len(groups[0][1]) == n_present and n_present >= 2:
                if not show_same:
                    continue
                idxs = groups[0][1]
                src = idxs[0]
                hunks = _cluster_hunks(cluster, src)
                if not hunks:
                    continue
                letters = "".join(model_letter(i) for i in idxs)
                _add_hunks(
                    f"same-{ci}-{letters}",
                    _title_with_cluster_sites(f"Shared · {loc}", cluster),
                    src,
                    hunks,
                    kind="same",
                    collapsed=False,
                    part_labels=[],
                )
                continue

            if len(groups) == 1 and len(groups[0][1]) == 1:
                if not show_unique:
                    continue
                src = groups[0][1][0]
                hunks = _cluster_hunks(cluster, src)
                if not hunks:
                    continue
                letter = model_letter(src)
                _add_hunks(
                    f"only-{ci}-{letter}",
                    _title_with_cluster_sites(f"{letter} · {loc}", cluster),
                    src,
                    hunks,
                    kind="only",
                    collapsed=collapse_tail,
                    part_labels=[],
                )
                continue

            if filter_mode == "consensus":
                continue

            pair_parts: list[tuple[str, list[PatchHunk]]] = []
            only_parts: list[tuple[str, list[PatchHunk]]] = []
            for _bk, idxs in groups:
                lab = "".join(model_letter(i) for i in idxs)
                hunks = _cluster_hunks(cluster, idxs[0])
                if not hunks:
                    continue
                if len(idxs) >= 2:
                    pair_parts.append((lab, hunks))
                else:
                    only_parts.append((lab, hunks))

            if filter_mode == "unique":
                if not only_parts:
                    continue
                _emit_model_cards(
                    only_parts,
                    verb="unique",
                    force_collapse=collapse_tail,
                    region_i=ci,
                    site=loc,
                    site_count=site_count,
                )
                continue

            if filter_mode == "pairs":
                if not pair_parts:
                    continue
                _emit_model_cards(
                    pair_parts,
                    verb="pair",
                    force_collapse=False,
                    region_i=ci,
                    site=loc,
                    site_count=site_count,
                )
                continue

            labeled_parts: list[tuple[str, list[PatchHunk]]] = []
            for _bk, idxs in groups:
                lab = "".join(model_letter(i) for i in idxs)
                hunks = _cluster_hunks(cluster, idxs[0])
                if not hunks:
                    continue
                labeled_parts.append((lab, hunks))
            if not labeled_parts:
                continue
            has_pair = any(len(g[1]) >= 2 for g in groups)
            verb = "pair" if has_pair and len(groups) <= 2 else "diverge"
            _emit_model_cards(
                labeled_parts,
                verb=verb,
                force_collapse=collapse_tail,
                region_i=ci,
                site=loc,
                site_count=site_count,
            )
        if sections:
            return _finish()
        # Multi-model edit with no surviving signal sites: still show designs
        # as full per-model patches (never invent Shared from token overlap).

    # Multi-model path without region UI (or empty after filter): design gallery.
    if cmp.n_models >= 2 and filter_mode in ("all", "unique", "diverge"):
        sections.append(
            CompareSection(
                key="design-head",
                title="Alternate designs — read A, then B, then C",
                model_letter="A",
                snip_diffs=[],
                kind="region",
                is_banner=True,
            )
        )
        for idx in cmp.present_indices:
            letter = model_letter(idx)
            targets = (
                _signal_lines(cmp.adds[idx])
                or _added_lines(cmp.patches[idx])
                or [""]
            )
            _add(
                f"design-{letter}",
                letter,
                idx,
                targets,
                cmp.patches[idx],
                window="full",
            )
            sections[-1].kind = "diverge"
            sections[-1].part_labels = []
            if not sections[-1].snip_diffs:
                sections[-1].snip_diffs = [
                    _placeholder_unified("no extractable text lines")
                ]
        return _finish()

    return _finish()


def _paint_section_titles(
    sections: list[CompareSection],
    model_colors: dict[str, str],
) -> list[CompareSection]:
    """Color model letters and +/- size tokens in section and tab titles."""
    for sec in sections:
        title = sec.title
        if model_colors:
            title = paint_model_prefix(title, model_colors)
        sec.title = _paint_size_tokens(title)
        if sec.design_tabs and model_colors:
            sec.design_tabs = [
                (paint_model_prefix(lab, model_colors), snips)
                for lab, snips in sec.design_tabs
            ]
    return sections


_STYLE_ADD = DIFF_ADD_STYLE
_STYLE_DEL = DIFF_DEL_STYLE
_STYLE_CTX = "dim"
_STYLE_HEAD = "bold"


@dataclass(slots=True)
class PatchSnippet:
    """Mini unified-diff window around target add lines."""

    rows: list[tuple[str, str]]  # (mark, text) mark in " @+-"


def extract_snippets(
    diff_text: str,
    targets: set[str],
    *,
    context: int = _SNIPPET_CONTEXT,
    max_snippets: int = _SNIPPETS_ALL,
    max_rows: int = _SNIPPET_MAX_ROWS,
    window: str = "shared",
) -> list[PatchSnippet]:
    """Windows of *diff_text* covering *targets* (verbatim kinds/text).

    *window*:
      - ``"full"``: entire unified hunks (solo / identical).
      - ``"shared"``: target lines + file ctx only.
      - ``"unique"``: Only-X tight or pure-unique full hunk.
    """
    if not targets or not (diff_text or "").strip():
        return []
    dlines = parse_diff_lines(diff_text)
    hit_idxs = [
        i for i, dl in enumerate(dlines)
        if dl.kind == "add" and dl.text in targets
    ]
    if not hit_idxs:
        return []

    if window == "unique":
        return _snippets_from_unique_targets(
            dlines, hit_idxs, targets, context, max_snippets, max_rows,
        )
    if window == "full":
        if any(dl.kind == "hunk" for dl in dlines):
            return _snippets_from_hunks(
                dlines, hit_idxs, targets, max_snippets, max_rows,
            )
        return _snippets_from_islands(
            dlines, hit_idxs, targets, context, max_snippets, max_rows,
        )
    return _snippets_target_windows(
        dlines, hit_idxs, targets, context, max_snippets, max_rows,
        merge_gap=max(context * 4, 12),
    )


def _hunk_bounds(dlines: list, idx: int) -> tuple[int, int]:
    """Return ``[start, end)`` for the unified hunk containing *idx*."""
    start = 0
    for i in range(idx, -1, -1):
        if dlines[i].kind == "hunk":
            start = i
            break
        if dlines[i].kind == "meta":
            start = i + 1
            break
    end = len(dlines)
    for i in range(idx + 1, len(dlines)):
        if dlines[i].kind in ("hunk", "meta"):
            end = i
            break
    return start, end


def _rows_from_span(
    dlines: list,
    start: int,
    end: int,
    *,
    include_hunk_headers: bool = True,
) -> list[tuple[str, str]]:
    """Build snippet rows from ``dlines[start:end]``."""
    rows: list[tuple[str, str]] = []
    for i in range(start, end):
        dl = dlines[i]
        if dl.kind == "meta":
            continue
        if dl.kind == "hunk":
            if include_hunk_headers:
                rows.append(("@", dl.text))
            continue
        rows.append(_row_for(dl))
    return rows


def _target_window_rows(
    dlines: list,
    a: int,
    b: int,
    targets: set[str],
    context: int,
    *,
    hunk_header_idx: int | None = None,
) -> list[tuple[str, str]]:
    """Target adds + punct glue + surrounding ctx/del for one hit span."""
    rows: list[tuple[str, str]] = []
    if (
        hunk_header_idx is not None
        and 0 <= hunk_header_idx < len(dlines)
        and dlines[hunk_header_idx].kind == "hunk"
    ):
        rows.append(("@", dlines[hunk_header_idx].text))
    ctx_a, ctx_b = _widen_ctx_only(dlines, a, b, context)
    for i in range(ctx_a, a):
        dl = dlines[i]
        if dl.kind in ("ctx", "del"):
            rows.append(_row_for(dl))
    for i in range(a, b + 1):
        dl = dlines[i]
        if dl.kind in ("meta", "hunk"):
            continue
        if dl.kind == "add":
            if dl.text in targets or _is_punct_only(dl.text):
                rows.append(_row_for(dl))
            continue
        if dl.kind in ("ctx", "del"):
            rows.append(_row_for(dl))
    for i in range(b + 1, ctx_b + 1):
        dl = dlines[i]
        if dl.kind in ("ctx", "del"):
            rows.append(_row_for(dl))
    return rows


def _snippets_from_hunks(
    dlines: list,
    hit_idxs: list[int],
    targets: set[str],
    max_snippets: int,
    max_rows: int,
) -> list[PatchSnippet]:
    """One full unified hunk per target hit (solo / identical paths)."""
    ordered_h0: list[int] = []
    seen: set[int] = set()
    for idx in hit_idxs:
        h0, _ = _hunk_bounds(dlines, idx)
        if h0 in seen:
            continue
        seen.add(h0)
        ordered_h0.append(h0)

    snippets: list[PatchSnippet] = []
    for h0 in ordered_h0[:max_snippets]:
        hit = next(i for i in hit_idxs if _hunk_bounds(dlines, i)[0] == h0)
        h0, h1 = _hunk_bounds(dlines, hit)
        rows = _rows_from_span(dlines, h0, h1, include_hunk_headers=True)
        if len(rows) > max_rows:
            rows = _trim_snippet_rows(rows, max_rows, targets)
        if rows:
            snippets.append(PatchSnippet(rows=rows))
    return snippets


def _snippets_target_windows(
    dlines: list,
    hit_idxs: list[int],
    targets: set[str],
    context: int,
    max_snippets: int,
    max_rows: int,
    *,
    merge_gap: int,
) -> list[PatchSnippet]:
    """Shared/pair windows: target lines + file ctx around hit clusters."""
    clusters = _cluster_hits(hit_idxs, merge_gap)
    snippets: list[PatchSnippet] = []
    for a, b in clusters[:max_snippets]:
        h0, _ = _hunk_bounds(dlines, a)
        rows = _target_window_rows(
            dlines, a, b, targets, context, hunk_header_idx=h0,
        )
        if len(rows) > max_rows:
            rows = _trim_snippet_rows(rows, max_rows, targets)
        if rows:
            snippets.append(PatchSnippet(rows=rows))
    return snippets


def _snippets_from_islands(
    dlines: list,
    hit_idxs: list[int],
    targets: set[str],
    context: int,
    max_snippets: int,
    max_rows: int,
) -> list[PatchSnippet]:
    """Fallback when the patch has no hunk headers: contiguous add/del islands."""
    clusters = _cluster_hits(hit_idxs, max(context * 2, 4))
    snippets: list[PatchSnippet] = []
    for a, b in clusters[:max_snippets]:
        while a > 0 and dlines[a - 1].kind in ("add", "del"):
            a -= 1
        while b + 1 < len(dlines) and dlines[b + 1].kind in ("add", "del"):
            b += 1
        ctx_a, ctx_b = _widen_ctx_only(dlines, a, b, context)
        rows = _rows_from_span(dlines, ctx_a, ctx_b + 1, include_hunk_headers=False)
        if len(rows) > max_rows:
            rows = _trim_snippet_rows(rows, max_rows, targets)
        if rows:
            snippets.append(PatchSnippet(rows=rows))
    return snippets


def _snippets_from_unique_targets(
    dlines: list,
    hit_idxs: list[int],
    targets: set[str],
    context: int,
    max_snippets: int,
    max_rows: int,
) -> list[PatchSnippet]:
    """Only-X windows: full hunk when pure-unique, else tight unique span."""
    hunk_hits: dict[int, list[int]] = {}
    for idx in hit_idxs:
        h0, _ = _hunk_bounds(dlines, idx)
        hunk_hits.setdefault(h0, []).append(idx)

    snippets: list[PatchSnippet] = []
    for h0 in list(hunk_hits.keys())[:max_snippets]:
        hits = hunk_hits[h0]
        _, h1 = _hunk_bounds(dlines, hits[0])
        if _hunk_signal_is_pure_targets(dlines, h0, h1, targets):
            rows = _rows_from_span(dlines, h0, h1, include_hunk_headers=True)
            if len(rows) > max_rows:
                rows = _trim_snippet_rows(rows, max_rows, targets)
            if rows:
                snippets.append(PatchSnippet(rows=rows))
            continue

        clusters = _cluster_hits(hits, max(context * 2, 4))
        for a, b in clusters:
            a, b = _expand_unique_span(dlines, a, b, targets)
            rows = _target_window_rows(
                dlines, a, b, targets, context, hunk_header_idx=h0,
            )
            if len(rows) > max_rows:
                rows = _trim_snippet_rows(rows, max_rows, targets)
            if rows:
                snippets.append(PatchSnippet(rows=rows))
    return snippets


def _hunk_signal_is_pure_targets(
    dlines: list, h0: int, h1: int, targets: set[str],
) -> bool:
    """True if every signal add in [h0, h1) is a unique target."""
    saw = False
    for i in range(h0, h1):
        dl = dlines[i]
        if dl.kind != "add" or not _is_signal_line(dl.text):
            continue
        saw = True
        if dl.text not in targets:
            return False
    return saw


def _cluster_hits(hit_idxs: list[int], merge_gap: int) -> list[tuple[int, int]]:
    clusters: list[tuple[int, int]] = []
    start = prev = hit_idxs[0]
    for idx in hit_idxs[1:]:
        if idx - prev <= merge_gap:
            prev = idx
            continue
        clusters.append((start, prev))
        start = prev = idx
    clusters.append((start, prev))
    return clusters


def _expand_unique_span(
    dlines: list, a: int, b: int, targets: set[str],
) -> tuple[int, int]:
    """Widen [a, b] across adjacent target or non-signal glue adds only."""
    while a > 0 and dlines[a - 1].kind == "add":
        t = dlines[a - 1].text
        if t in targets or not _is_signal_line(t):
            a -= 1
            continue
        break
    while b + 1 < len(dlines) and dlines[b + 1].kind == "add":
        t = dlines[b + 1].text
        if t in targets or not _is_signal_line(t):
            b += 1
            continue
        break
    return a, b


def _widen_ctx_only(
    dlines: list, a: int, b: int, context: int,
) -> tuple[int, int]:
    """Return indices including up to *context* ctx/del lines outside [a, b].

    Add lines (blank or not) are stepped over without being included, so
    file context is still found above/below a block of ``+`` lines.
    """
    ctx_a = a
    got = 0
    i = a - 1
    while i >= 0 and got < context:
        dl = dlines[i]
        if dl.kind in ("hunk", "meta"):
            break
        if dl.kind in ("ctx", "del"):
            ctx_a = i
            got += 1
            i -= 1
            continue
        if dl.kind == "add":
            i -= 1
            continue
        break
    ctx_b = b
    got = 0
    i = b + 1
    while i < len(dlines) and got < context:
        dl = dlines[i]
        if dl.kind in ("hunk", "meta"):
            break
        if dl.kind in ("ctx", "del"):
            ctx_b = i
            got += 1
            i += 1
            continue
        if dl.kind == "add":
            i += 1
            continue
        break
    return ctx_a, ctx_b


def _trim_snippet_rows(
    rows: list[tuple[str, str]],
    max_rows: int,
    targets: set[str],
) -> list[tuple[str, str]]:
    """Fit *rows* into *max_rows*, keeping target adds when possible."""
    if len(rows) <= max_rows:
        return rows
    head: list[tuple[str, str]] = []
    body = rows
    if rows and rows[0][0] == "@":
        head = [rows[0]]
        body = rows[1:]
    budget = max_rows - len(head) - 1
    if budget <= 0:
        return [*head, (" ", "…")][:max_rows]

    target_idxs = [
        i for i, (mark, text) in enumerate(body)
        if mark == "+" and text in targets
    ]
    if not target_idxs:
        return [*head, *body[:budget], (" ", "…")]

    lo, hi = target_idxs[0], target_idxs[-1]
    while hi - lo + 1 < budget and (lo > 0 or hi + 1 < len(body)):
        if lo > 0 and (
            hi == len(body) - 1
            or (target_idxs[0] - lo) <= (hi - target_idxs[-1])
        ):
            lo -= 1
        elif hi + 1 < len(body):
            hi += 1
        else:
            lo -= 1
    window = body[lo : hi + 1]
    if len(window) > budget:
        keep_head = budget // 2
        keep_tail = budget - keep_head
        window = [*window[:keep_head], (" ", "…"), *window[-keep_tail:]]
        if len(window) > budget + 1:
            window = window[: budget + 1]
        out = [*head, *window]
        return out[:max_rows]

    out = [*head]
    if lo > 0:
        out.append((" ", "…"))
    out.extend(window)
    if hi + 1 < len(body):
        out.append((" ", "…"))
    return out[:max_rows]


def _row_for(dl) -> tuple[str, str]:
    if dl.kind == "ctx":
        return (" ", dl.text)
    if dl.kind == "del":
        return ("-", dl.text)
    if dl.kind == "add":
        return ("+", dl.text)
    return (" ", dl.text)


def _paint_letters(text: str, model_colors: dict[str, str]) -> str:
    """Color A/B/C characters using rank colors when present."""
    if not model_colors:
        return text
    parts: list[str] = []
    for ch in text:
        color = model_colors.get(ch)
        if color:
            parts.append(f"[{color} bold]{ch}[/]")
        else:
            parts.append(ch)
    return "".join(parts)


def paint_model_prefix(title: str, model_colors: dict[str, str] | None) -> str:
    """Color a leading model letter run (``A``, ``BC``, …) with rank colors.

    Only the prefix is painted so previews like ``A · if B else`` do not
    recolor accidental letters in the rest of the title.
    """
    if not model_colors or not title:
        return title
    m = re.match(r"^([ABC]{1,3})(?=\s|·|$)", title)
    if not m:
        return title
    letters = m.group(1)
    return _paint_letters(letters, model_colors) + title[len(letters) :]


_STAT_LETTER_PREFIX = re.compile(r"^(no )?([ABC]{1,3})(\b.*)$")
_STAT_SIZE = re.compile(r"^([+-])(\d+)$")
_STAT_LETTER_SIZE = re.compile(r"^([ABC]{1,3})(\s+)([+-]\d+)$")
# Size tokens in titles/headers: ``+12``, ``-44`` (not ``gradle-8`` / identifiers).
_SIZE_TOKEN_IN_TEXT = re.compile(r"(?<![A-Za-z0-9_./])([+-])(\d+)\b")


def _paint_size_tokens(text: str) -> str:
    """Color bare ``+N`` / ``-M`` size counts with diff add/del styles."""

    def repl(m: re.Match[str]) -> str:
        sign, num = m.group(1), m.group(2)
        style = DIFF_ADD_STYLE if sign == "+" else DIFF_DEL_STYLE
        return f"[{style}]{sign}{num}[/]"

    return _SIZE_TOKEN_IN_TEXT.sub(repl, text)


def _paint_stat_token(token: str, model_colors: dict[str, str]) -> str:
    """Paint size counts and model letters in a stats token.

    Examples: ``+9`` (green), ``-44`` (red), ``A +9``, ``AB +2``, ``no C``.
    """
    m = _STAT_SIZE.match(token)
    if m:
        style = DIFF_ADD_STYLE if m.group(1) == "+" else DIFF_DEL_STYLE
        return f"[{style}]{token}[/]"
    m = _STAT_LETTER_SIZE.match(token)
    if m:
        letters, sp, size = m.group(1), m.group(2), m.group(3)
        sign = size[0]
        style = DIFF_ADD_STYLE if sign == "+" else DIFF_DEL_STYLE
        return f"{_paint_letters(letters, model_colors)}{sp}[{style}]{size}[/]"
    m = _STAT_LETTER_PREFIX.match(token)
    if not m:
        return token
    prefix, letters, rest = m.group(1) or "", m.group(2), m.group(3)
    return f"{prefix}{_paint_letters(letters, model_colors)}{_paint_size_tokens(rest)}"


def _append_stat_token_text(
    t: object,
    token: str,
    model_colors: dict[str, str],
) -> None:
    """Append one stats token to a Rich ``Text`` with add/del colors."""
    from rich.text import Text

    assert isinstance(t, Text)
    m = _STAT_SIZE.match(token)
    if m:
        style = f"bold {DIFF_ADD}" if m.group(1) == "+" else f"bold {DIFF_DEL}"
        t.append(token, style=style)
        return
    m = _STAT_LETTER_SIZE.match(token)
    if m:
        letters, sp, size = m.group(1), m.group(2), m.group(3)
        for ch in letters:
            style = f"bold {model_colors[ch]}" if ch in model_colors else "bold"
            t.append(ch, style=style)
        t.append(sp)
        sign = size[0]
        style = f"bold {DIFF_ADD}" if sign == "+" else f"bold {DIFF_DEL}"
        t.append(size, style=style)
        return
    m = _STAT_LETTER_PREFIX.match(token)
    if m and m.group(2):
        prefix, letters, rest = m.group(1) or "", m.group(2), m.group(3)
        if prefix:
            t.append(prefix, style="dim")
        for ch in letters:
            style = f"bold {model_colors[ch]}" if ch in model_colors else "bold"
            t.append(ch, style=style)
        if rest:
            # Remaining may include a size (unusual); keep dim for prose bits.
            t.append(rest, style="dim")
        return
    t.append(token, style="dim")


def format_compare_markup(
    cmp: SharedFileCompare,
    *,
    filter_mode: str = "all",
    model_colors: dict[str, str] | None = None,
) -> str:
    """Rich markup from the same sections the shared-compare modal shows.

    Kept for tests and any non-widget export path; the TUI mounts DiffDisplay
    widgets from ``build_compare_sections`` directly.
    """
    colors = model_colors or {}
    lines: list[str] = [
        compare_header_markup(cmp, model_colors=colors, filter_mode=filter_mode),
        "",
    ]
    max_snips = _SNIPPETS_ALL if filter_mode == "all" else _SNIPPETS_FILTER
    for sec in build_compare_sections(
        cmp, filter_mode=filter_mode, model_colors=colors,
    ):
        # Titles already rank-colored by build_compare_sections.
        lines.append(f"[{_STYLE_HEAD}]{sec.title}[/]")
        if sec.design_tabs:
            for tab_lab, snips in sec.design_tabs:
                lines.append(f"[bold]{tab_lab}[/]")
                for snip in snips[:max_snips]:
                    for raw in snip.splitlines()[:_SNIPPET_MAX_ROWS]:
                        lines.append(_markup_unified_line(raw))
                    lines.append("")
            continue
        for snip in sec.snip_diffs[:max_snips]:
            for raw in snip.splitlines()[:_SNIPPET_MAX_ROWS]:
                lines.append(_markup_unified_line(raw))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _markup_unified_line(raw: str) -> str:
    """One unified-diff line as Rich markup (uses Rich's escape)."""
    if raw.startswith("@@") or raw.startswith("…"):
        return f"[{_STYLE_CTX}]{_rich_escape(_clip(raw))}[/]"
    if raw.startswith("+"):
        return f"[{_STYLE_ADD}]+{_rich_escape(_clip(raw[1:]))}[/]"
    if raw.startswith("-"):
        return f"[{_STYLE_DEL}]-{_rich_escape(_clip(raw[1:]))}[/]"
    text = raw[1:] if raw.startswith(" ") else raw
    return f"[{_STYLE_CTX}] {_rich_escape(_clip(text))}[/]"


def _clip(text: str, max_len: int = _LINE_MAX) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"
