"""Tests for multi-model shared-file comparison."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sfctl.diff import parse_content
from sfctl.diff_compare import (
    common_filenames,
    compare_all_shared,
    compare_shared_file,
    format_compare_markup,
    union_filenames,
)
from sfctl.models import FileDiff, ModelData

SIMILAR = Path(__file__).resolve().parents[1] / "similar.json"


def _model(name: str, files: dict[str, str]) -> ModelData:
    return ModelData(
        name=name,
        diff="\n".join(files.values()),
        trace_summary="",
        file_diffs=[FileDiff(filename=fn, diff=diff) for fn, diff in files.items()],
    )


def _patch(*adds: str) -> str:
    body = "\n".join(f"+{a}" for a in adds)
    return (
        f"diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
        f"@@ -0,0 +1,{len(adds)} @@\n{body}\n"
    )


def _strip_markup(s: str) -> str:
    """Drop simple Rich markup tags for letter matching in tests."""
    import re

    return re.sub(r"\[/?[^\]]*\]", "", s)


def _b_snips_from_sections(sections: list) -> list[str]:
    """Collect B model snips from solo cards or multi-design tabs."""
    b_snips: list[str] = []
    for s in sections:
        for lab, snips in s.design_tabs:
            # Tab labels start with letter runs (A, B, AC, …) plus optional preview.
            head = _strip_markup(lab).split()[0] if lab else ""
            if "B" in head:
                b_snips.extend(snips)
        if s.design_tabs:
            continue
        if "B" in (s.part_labels or []) or s.key.endswith("-B") or "B" in s.title:
            for lab, snip in zip(
                s.part_labels or [s.model_letter], s.snip_diffs, strict=False,
            ):
                if "B" in lab:
                    b_snips.append(snip)
            if not s.part_labels and "B" in s.title:
                b_snips.extend(s.snip_diffs)
    return b_snips


class TestCommonFilenames:
    def test_intersection(self):
        models = [
            _model("A", {"a.py": "+x\n", "b.py": "+y\n"}),
            _model("B", {"a.py": "+x\n", "c.py": "+z\n"}),
            _model("C", {"a.py": "+x\n", "b.py": "+y\n"}),
        ]
        assert common_filenames(models) == ["a.py"]

    def test_none_when_disjoint(self):
        models = [
            _model("A", {"a.py": "+x\n"}),
            _model("B", {"b.py": "+y\n"}),
        ]
        assert common_filenames(models) == []


class TestUnionFilenames:
    def test_union_includes_pairs_and_solo(self):
        models = [
            _model("A", {"a.py": "+x\n", "solo_a.py": "+a\n", "pair_ac.py": "+p\n"}),
            _model("B", {"a.py": "+x\n", "solo_b.py": "+b\n"}),
            _model("C", {"pair_ac.py": "+p\n"}),
        ]
        assert union_filenames(models) == [
            "a.py",
            "pair_ac.py",
            "solo_a.py",
            "solo_b.py",
        ]
        # a.py is only A+B; no path is in all three
        assert common_filenames(models) == []

    def test_compare_all_orders_by_kind_then_path(self):
        """Stable sort: kind priority (diff→…→solo), then full path — not coverage."""
        # Edit-style diverge for a.py; identical share for m.py; solo z.
        def edit(tag: str) -> str:
            return (
                "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
                "@@ -1,2 +1,3 @@\n keep\n"
                f"+only_{tag}_line_here\n keep2\n"
            )

        models = [
            _model(
                "A",
                {
                    "z_solo.py": _patch("a-only-long-enough"),
                    "m_same.py": _patch("shared-long-enough-xx"),
                    "a_diff.py": edit("a"),
                    "pkg/b_new.py": (
                        "diff --git a/pkg/b_new.py b/pkg/b_new.py\n"
                        "--- /dev/null\n+++ b/pkg/b_new.py\n"
                        "@@ -0,0 +1,1 @@\n+newfile\n"
                    ),
                },
            ),
            _model(
                "B",
                {
                    "m_same.py": _patch("shared-long-enough-xx"),
                    "a_diff.py": edit("b"),
                    "pkg/b_new.py": (
                        "diff --git a/pkg/b_new.py b/pkg/b_new.py\n"
                        "--- /dev/null\n+++ b/pkg/b_new.py\n"
                        "@@ -0,0 +1,1 @@\n+newfileB\n"
                    ),
                },
            ),
            _model(
                "C",
                {
                    "m_same.py": _patch("shared-long-enough-xx"),
                    "a_diff.py": edit("c"),
                },
            ),
        ]
        compares = compare_all_shared(models)
        names = [c.filename for c in compares]
        badges = [c.kind_badge() for c in compares]
        order = {"diff": 0, "share": 1, "new": 2, "del": 3, "same": 4, "solo": 5}
        # Kind blocks stay contiguous and non-decreasing by priority.
        ranks = [order.get(b, 9) for b in badges]
        assert ranks == sorted(ranks)
        # diff before new before same; paths alphabetical within kind.
        assert names.index("a_diff.py") < names.index("m_same.py")  # diff before new
        # Identical multi pure-add is badge new (not same); path order within new.
        assert names.index("m_same.py") < names.index("pkg/b_new.py")
        assert names.index("pkg/b_new.py") < names.index("z_solo.py")
        new_paths = [c.filename for c in compares if c.kind_badge() == "new"]
        assert new_paths == sorted(new_paths, key=str.lower)
        assert next(c for c in compares if c.filename == "m_same.py").kind_badge() == "new"

    def test_list_dir_line_keeps_deep_segments(self):
        from sfctl.diff_compare import _list_dir_line

        p = "src/main/java/dev/nautchkafe/studios/network/sdk/argon"
        short = _list_dir_line(p, max_len=34)
        assert short.startswith(".../")
        assert short.endswith("/")
        assert "argon" in short
        # Distinct packages must not collapse to the same display.
        p2 = "src/main/java/dev/nautchkafe/argon2"
        s2 = _list_dir_line(p2, max_len=34)
        assert "argon2" in s2
        assert short != s2


class TestCompareSharedFile:
    def test_identical_patches(self):
        patch = "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n@@ -1 +1,2 @@\n keep\n+add\n"
        models = [
            _model("A", {"f.py": patch}),
            _model("B", {"f.py": patch}),
            _model("C", {"f.py": patch}),
        ]
        cmp = compare_shared_file(models, "f.py")
        assert cmp.identical_patches
        assert len(cmp.consensus_adds) == 1
        assert cmp.consensus_adds[0] == "add"
        assert cmp.coverage == "ABC"

    def test_identical_multi_new_is_new_not_same(self):
        """Identical pure-add multi-model paths badge as new, not same."""
        patch = (
            "diff --git a/f.py b/f.py\n--- /dev/null\n+++ b/f.py\n"
            "@@ -0,0 +1,2 @@\n+hello\n+world\n"
        )
        models = [
            _model("A", {"f.py": patch}),
            _model("B", {"f.py": patch}),
            _model("C", {"f.py": patch}),
        ]
        cmp = compare_shared_file(models, "f.py")
        assert cmp.is_all_new
        assert cmp.identical_patches
        assert cmp.path_kind == "new"
        assert cmp.kind_badge() == "new"

    def test_solo_pure_delete_is_del(self):
        del_patch = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n-line-a\n-line-b\n"
        )
        cmp = compare_shared_file(
            [_model("A", {"f.py": del_patch}), _model("B", {}), _model("C", {})],
            "f.py",
        )
        assert cmp.is_all_deleted
        assert cmp.path_kind == "deleted"
        assert cmp.kind_badge() == "del"

    def test_one_same_site_plus_diverge_is_diff_not_share(self):
        """Path share requires no multi-present diverge/pair sites."""
        # Site 1: ABC same add. Site 2: A vs B diverge (different bodies).
        same_hunk = (
            "@@ -10,3 +10,4 @@ def shared_site():\n"
            "     keep\n"
            "+    shared_add_line_xx\n"
            "     after\n"
        )
        patch_a = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
            + same_hunk
            + "@@ -40,3 +41,4 @@ def diverge_site():\n"
            "     prep\n"
            "+    design_a_only_body\n"
            "     done\n"
        )
        patch_b = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
            + same_hunk
            + "@@ -40,3 +41,4 @@ def diverge_site():\n"
            "     prep\n"
            "+    design_b_only_body\n"
            "     done\n"
        )
        patch_c = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n" + same_hunk
        )
        models = [
            _model("A", {"f.py": patch_a}),
            _model("B", {"f.py": patch_b}),
            _model("C", {"f.py": patch_c}),
        ]
        cmp = compare_shared_file(models, "f.py")
        assert any(d.relation == "same" for d in cmp.decisions)
        assert any(d.relation == "diverge" for d in cmp.decisions)
        assert cmp.path_kind == "diverge"
        assert cmp.kind_badge() == "diff"

    def test_identical_pure_delete_is_first_class_del(self):
        """Full-file deletes use del badge (like new), not same/empty stubs."""
        from sfctl.diff_compare import build_compare_sections, is_deleted_file_patch

        patch = (
            "diff --git a/App.java b/App.java\n"
            "--- a/App.java\n+++ /dev/null\n"
            "@@ -1,4 +0,0 @@\n"
            "-package dev.example;\n"
            "-public final class App {\n"
            "-    void run() {}\n"
            "-}\n"
        )
        assert is_deleted_file_patch(patch)
        models = [
            _model("A", {"App.java": patch}),
            _model("B", {"App.java": patch}),
            _model("C", {"App.java": patch}),
        ]
        cmp = compare_shared_file(models, "App.java")
        assert cmp.is_all_deleted
        assert cmp.path_kind == "deleted"
        assert cmp.kind_badge() == "del"
        assert "del" in cmp.summary_label()
        bits = cmp._stats_bits()
        assert any(b.startswith("-") for b in bits)
        assert "empty" not in bits
        sections = build_compare_sections(cmp)
        assert len(sections) == 1
        s = sections[0]
        assert s.kind == "deleted"
        assert "empty" not in s.title.lower()
        assert "marker" not in s.title.lower()
        assert s.title.startswith("Del")
        assert "delete" in s.title.lower() or "-4" in s.title
        joined = "\n".join(s.snip_diffs)
        assert "package dev.example" in joined
        assert "public final class App" in joined
        assert "-package" in joined or joined.count("package") >= 1

    def test_unique_and_shared_adds(self):
        models = [
            _model("A", {"f.py": _patch("shared", "only-a")}),
            _model("B", {"f.py": _patch("shared", "only-b")}),
            _model("C", {"f.py": _patch("shared", "only-c")}),
        ]
        cmp = compare_shared_file(models, "f.py")
        assert cmp.consensus_adds == ["shared"]
        oa, ob, oc = cmp.only_adds
        assert oa == ["only-a"]
        assert ob == ["only-b"]
        assert oc == ["only-c"]
        markup = format_compare_markup(cmp, filter_mode="all")
        assert "only-a" in markup
        # Line-set Shared may be omitted for tiny pure-add stubs; unique still shows.

    def test_pair_ac_file(self):
        models = [
            _model("A", {"f.py": _patch("both", "only-a")}),
            _model("B", {}),
            _model("C", {"f.py": _patch("both", "only-c")}),
        ]
        cmp = compare_shared_file(models, "f.py")
        assert cmp.coverage == "AC"
        assert cmp.is_pair
        assert cmp.agreement_adds == ["both"]
        assert cmp.consensus_adds == []  # needs all three
        assert cmp.pair_adds["AC"] == ["both"]
        assert cmp.coverage == "AC"
        # Coverage is on group headers, not per-file list meta.
        assert "AC" not in cmp._list_meta_text()
        assert "no B" in cmp._stats_bits() or "no B" in " ".join(cmp._stats_bits())
        from sfctl.diff_compare import compare_header_markup

        assert "no B" in compare_header_markup(cmp) or "B" in compare_header_markup(cmp)
        prompt = cmp.list_prompt().plain
        assert "f.py" in prompt
        assert "AC" not in prompt
        markup = format_compare_markup(cmp)
        assert "only-a" in markup
        assert "only-c" in markup
        assert cmp.agreement_adds == ["both"]

    def test_solo_file(self):
        models = [
            _model("A", {}),
            _model("B", {"solo.py": _patch("b-line")}),
            _model("C", {}),
        ]
        cmp = compare_shared_file(models, "solo.py")
        assert cmp.is_solo
        assert cmp.coverage == "B"
        assert "solo.py" in cmp.summary_label()
        markup = format_compare_markup(cmp)
        assert "solo.py" in markup
        assert "b-line" in markup
        # Solo path still previews unique adds under filter unique
        markup_u = format_compare_markup(cmp, filter_mode="unique")
        assert "b-line" in markup_u

    def test_solo_stats_omit_zero_sides_and_mark_new_files(self):
        """Stats use git-style +N/-M; new files say ``new`` instead of ``-0``."""
        new_patch = (
            "diff --git a/n.py b/n.py\n--- /dev/null\n+++ b/n.py\n"
            "@@ -0,0 +1,3 @@\n+one\n+two\n+three\n"
        )
        edit_add_only = (
            "diff --git a/e.py b/e.py\n--- a/e.py\n+++ b/e.py\n"
            "@@ -1,2 +1,3 @@\n keep\n+added-line-here\n keep2\n"
        )
        edit_both = (
            "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n"
            "@@ -1,2 +1,2 @@\n-old-line-xx\n+new-line-yy\n"
        )
        models = [
            _model(
                "A",
                {
                    "n.py": new_patch,
                    "e.py": edit_add_only,
                    "b.py": edit_both,
                },
            ),
            _model("B", {}),
            _model("C", {}),
        ]
        new_cmp = compare_shared_file(models, "n.py")
        assert new_cmp.is_all_new
        bits = new_cmp._stats_bits()
        # Kind badge carries "new"; stats are sizes only (no +0/-0).
        assert bits == ["+3"]
        assert not any(t == "-0" or t.startswith("-0") for t in bits)
        assert "-0" not in new_cmp.summary_label()
        assert new_cmp.kind_badge() == "new"
        assert "new" in new_cmp.summary_label()

        add_only = compare_shared_file(models, "e.py")
        assert not add_only.is_all_new
        assert add_only._stats_bits() == ["+1"]
        assert "-0" not in add_only.summary_label()

        both = compare_shared_file(models, "b.py")
        assert "+1" in both._stats_bits()
        assert "-1" in both._stats_bits()

    def test_preview_truncates_pathological_mega_hunk(self):
        """Safety-net trim still applies when a single hunk exceeds max_rows."""
        # Use edit-style hunks (ctx present) so this is not "large new file" mode.
        many = "\n".join(f"+unique_line_{i}_xxxxxxxx" for i in range(250))
        patch_a = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
            f"@@ -1,3 +1,253 @@\n keep\n{many}\n keep2\n"
        )
        models = [
            _model("A", {"f.py": patch_a}),
            _model("B", {"f.py": _patch("shared-only-line")}),
            _model("C", {"f.py": _patch("shared-only-line")}),
        ]
        cmp = compare_shared_file(models, "f.py")
        markup = format_compare_markup(cmp, filter_mode="all")
        assert "…" in markup
        assert markup.count("unique_line_") < 250

    def test_snippets_include_context_and_hunk(self):
        """Summary shows surrounding ctx and location label, not bare +lines only."""
        from sfctl.diff_compare import extract_snippets

        patch = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
            "@@ -10,6 +10,9 @@ def outer():\n"
            "     keep_before\n"
            "     also_before\n"
            "+    added_unique\n"
            "+    added_shared\n"
            "     keep_after\n"
        )
        models = [
            _model("A", {"f.py": patch}),
            _model(
                "B",
                {
                    "f.py": (
                        "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
                        "@@ -10,4 +10,5 @@ def outer():\n"
                        "     keep_before\n"
                        "+    added_shared\n"
                        "     keep_after\n"
                    )
                },
            ),
            _model(
                "C",
                {
                    "f.py": (
                        "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
                        "@@ -10,4 +10,5 @@ def outer():\n"
                        "     keep_before\n"
                        "+    added_shared\n"
                        "     keep_after\n"
                    )
                },
            ),
        ]
        cmp = compare_shared_file(models, "f.py")
        assert cmp.agreement_adds == ["    added_shared"]
        assert cmp.only_adds[0] == ["    added_unique"]

        snips = extract_snippets(patch, set(cmp.only_adds[0]))
        assert snips
        marks = [m for m, _ in snips[0].rows]
        texts = [t for _, t in snips[0].rows]
        assert "@" in marks  # location label from hunk
        assert " " in marks  # context
        assert any("keep_before" in t or "also_before" in t for t in texts)
        assert any("added_unique" in t for t in texts)
        assert any("def outer" in t for t in texts)

        markup = format_compare_markup(cmp, filter_mode="all")
        assert "keep_before" in markup or "also_before" in markup
        assert "added_unique" in markup
        assert "def outer" in markup

    def test_blank_and_bracket_lines_not_signal_overlap(self):
        """Empty / pure-punctuation adds must not inflate agreement."""
        models = [
            _model("A", {"f.py": _patch("real_shared", ")", "")}),
            _model("B", {"f.py": _patch("real_shared", ")", "only_b")}),
            _model("C", {"f.py": _patch("real_shared", "}")}),
        ]
        cmp = compare_shared_file(models, "f.py")
        assert cmp.consensus_adds == ["real_shared"]
        assert ")" not in cmp.agreement_adds
        assert "" not in cmp.agreement_adds

    def test_generic_return_not_pair_overlap(self):
        """Short/generic return lines must not create a Pair AB section."""
        # A and B both add a trivial ``return (`` and a unique block around it.
        patch_a = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
            "@@ -1,3 +1,12 @@\n"
            " class X:\n"
            "     def m(self):\n"
            "         pass\n"
            "+    def only_a_helper(self) -> None:\n"
            "+        # unique to A\n"
            "+        self._a = 1\n"
            "+        return (\n"
            "+            self._a,\n"
            "+        )\n"
        )
        patch_b = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
            "@@ -1,3 +1,12 @@\n"
            " class X:\n"
            "     def m(self):\n"
            "         pass\n"
            "+    def only_b_helper(self) -> None:\n"
            "+        # unique to B\n"
            "+        self._b = 2\n"
            "+        return (\n"
            "+            self._b,\n"
            "+        )\n"
        )
        models = [
            _model("A", {"f.py": patch_a}),
            _model("B", {"f.py": patch_b}),
            _model("C", {}),
        ]
        cmp = compare_shared_file(models, "f.py")
        assert cmp.pair_adds["AB"] == []
        markup = format_compare_markup(cmp, filter_mode="all")
        assert "AB only" not in markup
        assert "only_a_helper" in markup
        assert "only_b_helper" in markup

    def test_snippets_use_full_unified_hunk(self):
        """Shared windows are full unified hunks (same as model Diff tab)."""
        from sfctl.diff_compare import extract_snippets

        patch = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
            "@@ -1,20 +1,25 @@ class Body:\n"
            " line1\n"
            " line2\n"
            "+unique_noise_1\n"
            "+unique_noise_2\n"
            " line3\n"
            " line4\n"
            "+target_shared_line_here_long\n"
            " line5\n"
            " line6\n"
            "@@ -40,3 +45,4 @@ class Other:\n"
            " keep\n"
            "+other_hunk_only\n"
            " tail\n"
        )
        snips = extract_snippets(
            patch, {"target_shared_line_here_long"}, window="shared",
        )
        assert len(snips) == 1
        texts = [t for _, t in snips[0].rows]
        assert any(t.startswith("@@ -1,20") for t in texts)
        assert "target_shared_line_here_long" in texts
        assert "unique_noise_1" not in texts
        assert "line4" in texts or "line5" in texts
        assert "other_hunk_only" not in texts

    def test_unlabeled_global_site_uses_content_not_line_number(self):
        """Module-scope hunks without @@ labels must not title as L7."""
        from sfctl.diff_compare import (
            _decision_loc,
            build_compare_sections,
            compare_shared_file,
        )

        patch_a = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
            "@@ -1,5 +1,8 @@\n"
            " import os\n"
            " import sys\n"
            "\n"
            "+VERSION = 1\n"
            "+DEBUG = True\n"
            "+\n"
            " def main():\n"
            "     pass\n"
        )
        patch_b = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
            "@@ -1,5 +1,7 @@\n"
            " import os\n"
            " import sys\n"
            "\n"
            "+APP_NAME = \"x\"\n"
            "+\n"
            " def main():\n"
            "     pass\n"
        )
        models = [
            _model("A", {"f.py": patch_a}),
            _model("B", {"f.py": patch_b}),
            _model("C", {"f.py": patch_a}),
        ]
        cmp = compare_shared_file(models, "f.py")
        assert cmp.decisions
        for d in cmp.decisions:
            loc = _decision_loc(d)
            assert not (loc.startswith("L") and loc[1:].isdigit()), loc
            assert "VERSION" in loc or "APP_NAME" in loc or loc in {"top", "edit"}
        titles = " ".join(s.title for s in build_compare_sections(cmp))
        assert "L1" not in titles and "L7" not in titles
        assert "VERSION" in titles or "APP_NAME" in titles

    def test_region_decisions_group_by_old_start_not_token_sets(self):
        """Same base line anchors A/B together; C's different body is Only C.

        Line-set Shared used to surface ``tuple(id(r)…`` as ABC agreement even
        though A/B put it in ``_key`` and C put it inside ``openapi``.
        """
        from sfctl.diff_compare import build_compare_sections

        patch_a = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
            "@@ -1,4 +1,12 @@ class X:\n"
            "     def m(self):\n"
            "         pass\n"
            "+    def _key(self):\n"
            "+        return (\n"
            "+            tuple(id(r) for r in self.routes),\n"
            "+            tuple(id(r) for r in self.hooks),\n"
            "+        )\n"
            "     def openapi(self):\n"
            "         return {}\n"
        )
        patch_c = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
            "@@ -1,4 +1,10 @@ class X:\n"
            "     def m(self):\n"
            "         pass\n"
            "     def openapi(self):\n"
            "+        key = (\n"
            "+            tuple(id(r) for r in self.routes),\n"
            "+            tuple(id(r) for r in self.hooks),\n"
            "+        )\n"
            "         return {}\n"
        )
        models = [
            _model("A", {"f.py": patch_a}),
            _model("B", {"f.py": patch_a}),
            _model("C", {"f.py": patch_c}),
        ]
        cmp = compare_shared_file(models, "f.py")
        assert cmp.use_region_decisions
        assert len(cmp.decisions) == 1
        assert cmp.decisions[0].relation == "pair"
        sections = build_compare_sections(cmp)
        # Multi-design at one site: tabbed unit (A/B tab + C tab).
        tabbed = [s for s in sections if s.design_tabs]
        assert tabbed and len(tabbed[0].design_tabs) == 2
        joined_ab = "\n".join(tabbed[0].design_tabs[0][1])
        joined_c = "\n".join(tabbed[0].design_tabs[1][1])
        assert "_key" in joined_ab
        assert "tuple(id(r)" in joined_ab
        assert "key =" in joined_c or "key = (" in joined_c
        assert "_key" not in joined_c

    def test_multi_hunk_same_func_clusters_into_one_design_banner(self):
        """Two A/C diverges at the same func are one tabbed multi-design unit."""
        from sfctl.diff_compare import build_compare_sections, compare_shared_file

        # Same func label, two hunk starts — A vs C differ at both.
        patch_a = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
            "@@ -10,3 +10,8 @@ async def solve_dependencies(\n"
            "     *,\n"
            "     request,\n"
            "+    \"\"\"\n"
            "+    Solve a dependency tree for a request.\n"
            "+    \"\"\"\n"
            "+    solver = get_or_compile_solver(dependant)\n"
            "+    return await solver(request=request)\n"
            "@@ -40,4 +45,4 @@ async def solve_dependencies(\n"
            "     for sub in dependant.dependencies:\n"
            "-        solved_result = await solve_dependencies(\n"
            "+        solved_result = await solve_dependencies_recursive(\n"
            "             request=request,\n"
            "             dependant=sub,\n"
        )
        patch_c = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
            "@@ -10,3 +10,10 @@ async def solve_dependencies(\n"
            "     *,\n"
            "     request,\n"
            "+    \"\"\"\n"
            "+    Resolve dependencies for a request.\n"
            "+    \"\"\"\n"
            "+    if not has_active_overrides(provider):\n"
            "+        compiled = get_compiled_resolver(dependant)\n"
            "+        return await compiled.resolve(request=request)\n"
            "+    return await _solve_dependencies_recursive(request=request)\n"
            "@@ -40,4 +47,4 @@ async def solve_dependencies(\n"
            "     for sub in dependant.dependencies:\n"
            "-        solved_result = await solve_dependencies(\n"
            "+        solved_result = await _solve_dependencies_recursive(\n"
            "             request=request,\n"
            "             dependant=sub,\n"
        )
        models = [
            _model("A", {"f.py": patch_a}),
            _model("B", {"f.py": ""}),
            _model("C", {"f.py": patch_c}),
        ]
        cmp = compare_shared_file(models, "f.py")
        assert cmp.use_region_decisions
        assert len(cmp.decisions) == 2
        sections = build_compare_sections(cmp)
        # One linear site with designs as tabs (not stacked banners + cards).
        tabbed = [s for s in sections if s.design_tabs]
        assert len(tabbed) == 1, [s.title for s in sections]
        assert "2 designs" in tabbed[0].title
        assert len(tabbed[0].design_tabs) == 2
        # Each design tab carries both hunks of the function rewrite.
        assert all(len(snips) == 2 for _lab, snips in tabbed[0].design_tabs)
        labels = [lab for lab, _ in tabbed[0].design_tabs]
        assert labels[0] != labels[1]
        assert not any(lab.rstrip().endswith('"""') for lab in labels)
        # Stats never say jargon "only" (use "unique" when a mix is shown).
        assert "only" not in " ".join(cmp._stats_bits())

    def test_unique_section_keeps_all_clusters(self):
        """Distinct unique clusters must not be clipped by a low snippet cap."""
        from sfctl.diff_compare import build_compare_sections

        def _b_patch() -> str:
            parts = [
                "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
                "@@ -1,20 +1,40 @@ class X:\n"
            ]
            for i in range(4):
                parts.append(f"     def kept_{i}(self):\n")
                parts.append("         pass\n")
                parts.append(f"+    def only_b_block_{i}(self):\n")
                parts.append(f"+        self.b_marker_{i} = {i}\n")
            return "".join(parts)

        # Same old-start edit hunks so region decisions apply (not pure-add).
        shared_edit = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
            "@@ -1,3 +1,4 @@ class X:\n"
            "     def root(self):\n"
            "         pass\n"
            "+    shared_only_line_xx = 1\n"
        )
        models = [
            _model("A", {"f.py": shared_edit}),
            _model("B", {"f.py": _b_patch()}),
            _model("C", {"f.py": shared_edit}),
        ]
        cmp = compare_shared_file(models, "f.py")
        sections = build_compare_sections(cmp)
        # B may be a solo card or a design tab (when co-located with AC).
        b_snips = _b_snips_from_sections(sections)
        joined = "\n".join(b_snips)
        assert joined, f"no B content in {[s.key for s in sections]}"
        for i in range(4):
            assert f"only_b_block_{i}" in joined
            assert f"b_marker_{i}" in joined

    def test_unique_island_preserves_model_patch_marks(self):
        """Only-B region renders B's full hunk (no amputated methods)."""
        from sfctl.diff import parse_diff_lines
        from sfctl.diff_compare import build_compare_sections

        patch_b = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
            "@@ -1,4 +1,14 @@ class X:\n"
            "     def m(self):\n"
            "         pass\n"
            "+    def only_b_helper(self):\n"
            "+        # unique comment for B\n"
            "+        return (\n"
            "+            tuple(id(r) for r in self.routes),\n"
            "+            tuple(id(r) for r in self.hooks),\n"
            "+        )\n"
            "     def openapi(self):\n"
            "         return {}\n"
        )
        patch_ac = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
            "@@ -1,4 +1,8 @@ class X:\n"
            "     def m(self):\n"
            "         pass\n"
            "     def openapi(self):\n"
            "+        key = (\n"
            "+            tuple(id(r) for r in self.routes),\n"
            "+            tuple(id(r) for r in self.hooks),\n"
            "+        )\n"
            "         return {}\n"
        )
        models = [
            _model("A", {"f.py": patch_ac}),
            _model("B", {"f.py": patch_b}),
            _model("C", {"f.py": patch_ac}),
        ]
        cmp = compare_shared_file(models, "f.py")
        sections = build_compare_sections(cmp)
        b_snips = _b_snips_from_sections(sections)
        joined = "\n".join(b_snips)
        assert "only_b_helper" in joined
        assert "unique comment for B" in joined
        # Full hunk keeps the method body (including lines A/C also added elsewhere).
        assert "tuple(id(r) for r in self.routes)" in joined

        b_lines = parse_diff_lines(cmp.patches[1])
        mark_of = {"ctx": " ", "add": "+", "del": "-", "hunk": "@", "meta": "M"}
        for sn in b_snips:
            for line in sn.splitlines():
                if line.startswith("@@") or line.strip() in {"", "…"}:
                    continue
                mark, text = line[0], line[1:]
                if mark not in " +-":
                    continue
                assert any(
                    mark_of[dl.kind] == mark and dl.text == text for dl in b_lines
                ), f"snip line not in B patch: {line!r}"

    def test_large_new_files_show_per_model_not_line_set(self):
        """Large multi-model new files skip Shared line-set soup."""
        from sfctl.diff_compare import build_compare_sections

        def big_new(n: int, tag: str) -> str:
            body = "\n".join(f"+line_{tag}_{i}_unique_content_here" for i in range(n))
            return (
                f"diff --git a/f.go b/f.go\n--- /dev/null\n+++ b/f.go\n"
                f"@@ -0,0 +1,{n} @@\n{body}\n"
            )

        models = [
            _model("A", {"f.go": big_new(50, "a")}),
            _model("B", {"f.go": big_new(55, "b")}),
            _model("C", {"f.go": big_new(45, "c")}),
        ]
        cmp = compare_shared_file(models, "f.go")
        assert cmp.is_all_new
        assert cmp.prefer_per_model_new_files
        label = cmp.summary_label()
        assert "new" in label
        # List/summary meta collapses sizes; full per-model sizes stay in stats.
        bits = cmp._stats_bits()
        assert "+50" in bits and "+55" in bits and "+45" in bits
        assert "A +" not in label
        assert "-0" not in label
        assert "+45" in label  # range form +45…55 or individual
        sections = build_compare_sections(cmp)
        tabbed = next(s for s in sections if s.design_tabs)
        assert tabbed.key == "new-tabs"
        assert len(tabbed.design_tabs) == 3
        assert "shared" not in [s.key for s in sections]
        joined_a = "\n".join(tabbed.design_tabs[0][1])
        assert "line_a_0_unique_content_here" in joined_a
        assert "Copyright" not in joined_a

    def test_diverge_region_shows_full_bodies_not_token_shared(self):
        """Same base location, different designs → Only A/B/C full hunks.

        Line-set pair-AC used to invent Shared from ``endTime``/``AddInvocation``
        while dropping the design-defining comments — unreadable.
        """
        from sfctl.diff_compare import build_compare_sections

        patch_a = (
            "diff --git a/f.go b/f.go\n--- a/f.go\n+++ b/f.go\n"
            "@@ -10,3 +10,10 @@ func Write() {\n"
            " prep()\n"
            "+// Record timing A\n"
            "+endTime := clock.Now(ctx)\n"
            "+startTime := report.CreatedAt\n"
            "+sw.run.AddInvocation(true)\n"
            " done()\n"
        )
        patch_c = (
            "diff --git a/f.go b/f.go\n--- a/f.go\n+++ b/f.go\n"
            "@@ -10,3 +10,10 @@ func Write() {\n"
            " prep()\n"
            "+// Record timing C\n"
            "+endTime := clock.Now(ctx)\n"
            "+startTime := report.CreatedAt\n"
            "+sw.run.AddInvocation(true)\n"
            " done()\n"
        )
        patch_b = (
            "diff --git a/f.go b/f.go\n--- a/f.go\n+++ b/f.go\n"
            "@@ -10,3 +10,4 @@ func Write() {\n"
            " prep()\n"
            "+// B different approach\n"
            " done()\n"
        )
        models = [
            _model("A", {"f.go": patch_a}),
            _model("B", {"f.go": patch_b}),
            _model("C", {"f.go": patch_c}),
        ]
        cmp = compare_shared_file(models, "f.go")
        assert cmp.use_region_decisions
        assert cmp.decisions[0].relation == "diverge"
        sections = build_compare_sections(cmp)
        tabbed = [s for s in sections if s.design_tabs]
        assert tabbed and "designs" in tabbed[0].title
        # Tabs hold A/B/C full bodies (not token-Shared soup).
        by_lab = {}
        for lab, snips in tabbed[0].design_tabs:
            by_lab[lab[0]] = "\n".join(snips)
        assert "Record timing A" in by_lab["A"]
        assert "AddInvocation" in by_lab["A"]
        assert "Record timing C" in by_lab["C"]
        assert "Record timing A" not in by_lab["C"]
        assert "B different approach" in by_lab["B"]



@pytest.mark.skipif(not SIMILAR.exists(), reason="similar.json not present")
class TestSimilarFixture:
    def test_shared_files_and_stats(self):
        data = json.loads(SIMILAR.read_text())
        models = parse_content(data["content"]).models
        names = common_filenames(models)
        assert "pandas/io/_util.py" in names
        compares = compare_all_shared(models)
        util = next(c for c in compares if c.filename.endswith("_util.py"))
        assert len(util.consensus_adds) >= 20
        assert sum(len(x) for x in util.only_adds) > 0
        label = util.summary_label()
        assert "ABC" in label and "_util.py" in label
        assert "shared" in label or "+" in label
        # Union is at least as large as intersection
        assert len(compares) >= len(names)


class TestCompareSections:
    def test_build_sections_multi_new_is_design_gallery(self):
        """Multi-model new files use one card per model (not token Shared)."""
        from sfctl.diff_compare import build_compare_sections

        models = [
            _model("A", {"f.py": _patch("shared-long-enough-xx", "only-a-unique-line")}),
            _model("B", {"f.py": _patch("shared-long-enough-xx", "only-b-unique-line")}),
            _model("C", {"f.py": _patch("shared-long-enough-xx")}),
        ]
        cmp = compare_shared_file(models, "f.py")
        assert cmp.is_all_new
        assert cmp.prefer_per_model_new_files
        assert not cmp.use_region_decisions
        sections = build_compare_sections(cmp, filter_mode="all")
        keys = [s.key for s in sections]
        # Multi-design new files: one tabbed unit (not N stacked cards).
        tabbed = [s for s in sections if s.design_tabs]
        assert tabbed or any(s.key.startswith("new-") for s in sections)
        if tabbed:
            labels = [lab for lab, _ in tabbed[0].design_tabs]
            assert any(lab.startswith("A") for lab in labels)
            joined = "\n".join(tabbed[0].all_snip_texts())
        else:
            body_a = next(s for s in sections if "A" in s.key or s.model_letter == "A")
            joined = "\n".join(body_a.all_snip_texts())
        assert "only-a-unique-line" in joined
        assert "shared" not in keys

    def test_edit_region_decisions_no_false_shared_tokens(self):
        """B and C share Unlock/return true tokens but different guards → diverge."""
        from sfctl.diff_compare import build_compare_sections

        patch_b = (
            "diff --git a/index.go b/index.go\n--- a/index.go\n+++ b/index.go\n"
            "@@ -205,6 +209,13 @@ func Compact {\n"
            " ti.Lock()\n"
            "+if isHistoryLocked != nil && isHistoryLocked(keyi.key) {\n"
            "+keyi.keepAll(available)\n"
            "+ti.Unlock()\n"
            "+return true\n"
            "+}\n"
            " keyi.compact(ti.lg, rev, available)\n"
        )
        patch_c = (
            "diff --git a/index.go b/index.go\n--- a/index.go\n+++ b/index.go\n"
            "@@ -205,6 +229,12 @@ func Compact {\n"
            " ti.Lock()\n"
            "+if ti.isCompactLocked(keyi.key) {\n"
            "+ti.Unlock()\n"
            "+return true\n"
            "+}\n"
            " keyi.compact(ti.lg, rev, available)\n"
        )
        patch_a = (
            "diff --git a/index.go b/index.go\n--- a/index.go\n+++ b/index.go\n"
            "@@ -205,6 +206,9 @@ func Compact {\n"
            " ti.Lock()\n"
            "+if _, locked := lockHistory[string(keyi.key)]; locked {\n"
            "+keyi.compact(ti.lg, rev, available, true)\n"
            "+}\n"
            " keyi.compact(ti.lg, rev, available)\n"
        )
        models = [
            _model("A", {"index.go": patch_a}),
            _model("B", {"index.go": patch_b}),
            _model("C", {"index.go": patch_c}),
        ]
        cmp = compare_shared_file(models, "index.go")
        assert cmp.use_region_decisions
        assert cmp.decisions[0].relation == "diverge"
        # Line-set still sees BC token overlap — decisions must not surface it as Shared.
        assert cmp.pair_adds["BC"]
        sections = build_compare_sections(cmp)
        assert not any(s.kind == "same" for s in sections)
        joined = "\n".join(
            s.title + "\n" + "\n".join(s.all_snip_texts()) for s in sections
        )
        assert "isHistoryLocked" in joined
        assert "isCompactLocked" in joined
        assert "lockHistory" in joined
        # Compact site is one tabbed diverge unit, not token-Shared.
        compact = [
            s for s in sections
            if "Compact" in s.title or s.kind == "diverge" or s.design_tabs
        ]
        assert compact
        assert any(s.design_tabs or s.kind == "diverge" for s in compact)


class TestJumpLineForModel:
    def test_prefers_unique_line(self):
        from sfctl.diff_compare import jump_line_for_model

        models = [
            _model("A", {"f.py": _patch("shared-long-enough-xx", "only-a-unique-line")}),
            _model("B", {"f.py": _patch("shared-long-enough-xx", "only-b-unique-line")}),
            _model("C", {"f.py": _patch("shared-long-enough-xx")}),
        ]
        cmp = compare_shared_file(models, "f.py")
        assert jump_line_for_model(cmp, 0) == "only-a-unique-line"
        assert jump_line_for_model(cmp, 1) == "only-b-unique-line"
        # C has no unique lines — fall back to agreement
        assert jump_line_for_model(cmp, 2) == "shared-long-enough-xx"


class TestEmptyPatchesExcluded:
    def test_all_empty_paths_dropped_from_compare_all(self):
        """Paths with no non-empty patch for any model do not appear."""
        models = [
            _model("A", {"a.py": _patch("x")}),
            _model("B", {"ghost.py": ""}),  # filename only, empty body
            _model("C", {"a.py": _patch("x")}),
        ]
        # Manually inject empty FileDiff like a stripped empty new file
        from sfctl.models import FileDiff

        models[1] = ModelData(
            name="B",
            diff="",
            trace_summary="",
            file_diffs=[FileDiff(filename="ghost.py", diff="")],
        )
        names = [c.filename for c in compare_all_shared(models)]
        assert "a.py" in names
        assert "ghost.py" not in names

    def test_empty_new_file_shows_as_solo(self):
        from sfctl.diff import extract_file_diffs
        from sfctl.models import ModelData

        block = (
            "diff --git a/pkg/__init__.py b/pkg/__init__.py\n"
            "new file mode 100644\n"
            "index 0000000..e69de29\n"
        )
        fds = extract_file_diffs(block)
        models = [
            ModelData(name="A", diff="", trace_summary="", file_diffs=[]),
            ModelData(name="B", diff=block, trace_summary="", file_diffs=fds),
            ModelData(name="C", diff="", trace_summary="", file_diffs=[]),
        ]
        compares = compare_all_shared(models)
        assert len(compares) == 1
        c = compares[0]
        assert c.filename == "pkg/__init__.py"
        assert c.coverage == "B"
        assert c.is_solo

    def test_comment_only_lines_not_signal_or_agreement(self):
        """Comment-only lines never form Shared agreement (no keyword hacks)."""
        from sfctl.diff_compare import (
            _drop_leading_comment_preamble,
            _is_comment_only_line,
            _is_signal_line,
            _unified_from_adds,
            build_compare_sections,
        )

        assert _is_comment_only_line("/*")
        assert _is_comment_only_line(" * header text")
        assert _is_comment_only_line("# a comment")
        assert _is_comment_only_line("// note")
        assert not _is_comment_only_line("#!/bin/bash")
        assert not _is_comment_only_line("package com.example;")
        assert not _is_signal_line(" * header text")
        assert not _is_signal_line("# a comment")

        clean = _drop_leading_comment_preamble([
            "/*",
            " * header text",
            " */",
            "package com.example;",
            "class X {}",
        ])
        assert clean[0].startswith("package")
        u = _unified_from_adds([
            "/*",
            " * header text",
            " */",
            "package com.example;",
        ])
        assert "header text" not in u
        assert "package com.example" in u

        def java_new(tag: str) -> str:
            body = "\n".join(
                f"+{ln}"
                for ln in [
                    "/*",
                    " * shared header comment across models",
                    " */",
                    f"package com.example.{tag};",
                    f"public class {tag.title()} {{}}",
                ]
            )
            n = body.count("\n") + 1
            return (
                f"diff --git a/F.java b/F.java\n--- /dev/null\n+++ b/F.java\n"
                f"@@ -0,0 +1,{n} @@\n{body}\n"
            )

        models = [
            _model("A", {"F.java": java_new("alpha")}),
            _model("B", {"F.java": java_new("beta")}),
            _model("C", {"F.java": java_new("gamma")}),
        ]
        cmp = compare_shared_file(models, "F.java")
        assert not any("shared header" in ln for ln in cmp.agreement_adds)
        sections = build_compare_sections(cmp)
        for s in sections:
            if s.is_banner or not s.snip_diffs:
                continue
            assert "shared header" not in s.title

    def test_leading_hash_comments_after_shebang_dropped(self):
        from sfctl.diff_compare import build_compare_sections

        script = "\n".join(
            f"+{ln}"
            for ln in [
                "#!/bin/bash",
                "# preamble comment one",
                "# preamble comment two",
                "#",
                'echo "hello"',
            ]
        )
        patch = (
            f"diff --git a/run.sh b/run.sh\n--- /dev/null\n+++ b/run.sh\n"
            f"@@ -0,0 +1,5 @@\n{script}\n"
        )
        models = [
            _model("A", {"run.sh": patch}),
            _model("B", {}),
            _model("C", {}),
        ]
        cmp = compare_shared_file(models, "run.sh")
        sections = build_compare_sections(cmp)
        joined = "\n".join(s.snip_diffs[0] for s in sections if s.snip_diffs)
        assert "#!/bin/bash" in joined
        assert "preamble comment" not in joined
        assert 'echo "hello"' in joined

    def test_multi_model_common_prefix_dropped(self):
        """Identical leading lines across A/B/C are skipped in per-model cards."""
        from sfctl.diff_compare import build_compare_sections

        def big(tag: str) -> str:
            shared = [f"+shared_line_{i}" for i in range(20)]
            unique = [f"+{tag}_only_{i}_unique_content_here" for i in range(30)]
            body = "\n".join([*shared, *unique])
            n = 50
            return (
                f"diff --git a/f.go b/f.go\n--- /dev/null\n+++ b/f.go\n"
                f"@@ -0,0 +1,{n} @@\n{body}\n"
            )

        models = [
            _model("A", {"f.go": big("a")}),
            _model("B", {"f.go": big("b")}),
            _model("C", {"f.go": big("c")}),
        ]
        cmp = compare_shared_file(models, "f.go")
        assert cmp.prefer_per_model_new_files
        sections = build_compare_sections(cmp)
        bodies = [
            snip
            for s in sections
            for snip in s.all_snip_texts()
        ]
        assert bodies
        # Shared prefix gone; unique design lines remain.
        assert all("shared_line_0" not in b for b in bodies)
        assert any("a_only_0_unique_content_here" in b for b in bodies)

    def test_empty_multi_model_new_files_get_placeholders(self):
        from sfctl.diff_compare import build_compare_sections

        empty = (
            "diff --git a/x.py b/x.py\n--- /dev/null\n+++ b/x.py\n"
            "@@ -0,0 +0,0 @@\n# empty new file\n"
        )
        blank = (
            "diff --git a/x.py b/x.py\n--- /dev/null\n+++ b/x.py\n"
            "@@ -0,0 +1,1 @@\n+\n"
        )
        models = [
            _model("A", {"x.py": blank}),
            _model("B", {}),
            _model("C", {"x.py": empty}),
        ]
        cmp = compare_shared_file(models, "x.py")
        assert cmp.prefer_per_model_new_files
        sections = build_compare_sections(cmp)
        assert sections
        # Tabbed multi-design or single cards: every design body has a placeholder.
        assert all(
            s.all_snip_texts() for s in sections if not s.is_banner
        )

    def test_no_newline_marker_still_counts_as_new_file(self):
        """Git EOF markers must not disqualify pure-add new-file patches."""
        from sfctl.diff_compare import is_new_file_patch

        patch = (
            "@@ -0,0 +1,3 @@\n"
            "+alpha=1\n"
            "+beta=2\n"
            "+gamma=3\n"
            "\\ No newline at end of file\n"
        )
        assert is_new_file_patch(patch)

    def test_near_identical_new_files_factor_shared_plus_unique(self):
        """Near-identical wrappers: Shared body + one unique line per model."""
        from sfctl.diff_compare import build_compare_sections

        def props(url: str, *, eof: bool = False) -> str:
            body = "\n".join(
                [
                    "+distributionBase=GRADLE_USER_HOME",
                    "+distributionPath=wrapper/dists",
                    f"+distributionUrl={url}",
                    "+networkTimeout=10000",
                    "+validateDistributionUrl=true",
                    "+zipStoreBase=GRADLE_USER_HOME",
                    "+zipStorePath=wrapper/dists",
                ]
            )
            tail = "\n\\ No newline at end of file" if eof else ""
            return (
                "diff --git a/w.properties b/w.properties\n"
                "--- /dev/null\n+++ b/w.properties\n"
                f"@@ -0,0 +1,7 @@\n{body}{tail}\n"
            )

        models = [
            _model("A", {"w.properties": props("gradle-8.12-bin.zip", eof=True)}),
            _model("B", {"w.properties": props("gradle-8.10.2-bin.zip")}),
            _model("C", {"w.properties": props("gradle-8.7-bin.zip")}),
        ]
        cmp = compare_shared_file(models, "w.properties")
        assert cmp.is_all_new
        assert cmp.prefer_per_model_new_files
        sections = build_compare_sections(cmp)
        keys = [s.key for s in sections]
        assert "new-shared" in keys
        assert "new-A" in keys and "new-B" in keys and "new-C" in keys
        shared = next(s for s in sections if s.key == "new-shared")
        assert "distributionBase=GRADLE_USER_HOME" in shared.snip_diffs[0]
        assert "gradle-8.12" not in shared.snip_diffs[0]
        a_body = next(s for s in sections if s.key == "new-A").snip_diffs[0]
        assert "gradle-8.12" in a_body
        assert "distributionBase" not in a_body


class TestModelLetterColorsInCompare:
    def test_paint_model_prefix_only_leading_letters(self):
        from sfctl.diff_compare import paint_model_prefix

        colors = {"A": "green", "B": "yellow", "C": "red"}
        assert paint_model_prefix("A · Write", colors).startswith("[green bold]A[/]")
        assert "Write" in paint_model_prefix("A · Write", colors)
        # Does not recolor letters inside the preview.
        painted = paint_model_prefix("A · if B else C", colors)
        assert painted.startswith("[green bold]A[/]")
        assert "[yellow bold]B[/]" not in painted
        assert paint_model_prefix("Shared", colors) == "Shared"

    def test_plus_minus_stats_use_diff_colors(self):
        """List/header size tokens use DIFF_ADD / DIFF_DEL green and red."""
        from sfctl.constants import DIFF_ADD, DIFF_DEL
        from sfctl.diff_compare import (
            _paint_size_tokens,
            _paint_stat_token,
            build_compare_sections,
            compare_header_markup,
        )

        assert DIFF_ADD in _paint_stat_token("+12", {})
        assert DIFF_DEL in _paint_stat_token("-44", {})
        assert DIFF_ADD in _paint_stat_token("A +9", {"A": "cyan"})
        painted = _paint_size_tokens("Same  —  delete · -44  ·  foo")
        assert DIFF_DEL in painted
        assert "-44" in painted
        # Do not recolor versions embedded in identifiers.
        assert "gradle-8" in _paint_size_tokens("url=gradle-8.12-bin")
        assert DIFF_DEL not in _paint_size_tokens("url=gradle-8.12-bin")

        patch = (
            "diff --git a/App.java b/App.java\n"
            "--- a/App.java\n+++ /dev/null\n"
            "@@ -1,3 +0,0 @@\n"
            "-package x;\n"
            "-class App {}\n"
            "-\n"
        )
        models = [
            _model("A", {"App.java": patch}),
            _model("B", {"App.java": patch}),
            _model("C", {"App.java": patch}),
        ]
        cmp = compare_shared_file(models, "App.java")
        hdr = compare_header_markup(cmp)
        assert DIFF_DEL in hdr
        sections = build_compare_sections(cmp)
        assert any(DIFF_DEL in s.title for s in sections)
        prompt = cmp.list_prompt()
        # Rich Text: del size should not be plain dim-only.
        plain = prompt.plain
        assert "-" in plain and any(ch.isdigit() for ch in plain)

    def test_list_prompt_fixed_columns(self):
        """Two lines: kind+base[+right meta]; directory alone on line 2."""
        from sfctl.diff_compare import _LIST_META_WIDTH, _LIST_ROW_WIDTH

        def edit(tag: str) -> str:
            return (
                "diff --git a/dir/deep/f.py b/dir/deep/f.py\n"
                "--- a/dir/deep/f.py\n+++ b/dir/deep/f.py\n"
                "@@ -1,2 +1,3 @@\n keep\n"
                f"+only_{tag}_unique_line_here\n keep2\n"
            )

        del_patch = (
            "diff --git a/pkg/App.java b/pkg/App.java\n"
            "--- a/pkg/App.java\n+++ /dev/null\n"
            "@@ -1,3 +0,0 @@\n-package x;\n-class App {}\n-\n"
        )
        new_a = (
            "diff --git a/pkg/n.py b/pkg/n.py\n--- /dev/null\n+++ b/pkg/n.py\n"
            "@@ -0,0 +1,3 @@\n+a\n+b\n+c\n"
        )
        new_b = (
            "diff --git a/pkg/n.py b/pkg/n.py\n--- /dev/null\n+++ b/pkg/n.py\n"
            "@@ -0,0 +1,5 @@\n+a\n+b\n+c\n+d\n+e\n"
        )

        models = [
            _model(
                "A",
                {
                    "dir/deep/f.py": edit("a"),
                    "pkg/App.java": del_patch,
                    "pkg/n.py": new_a,
                },
            ),
            _model(
                "B",
                {
                    "dir/deep/f.py": edit("b"),
                    "pkg/App.java": del_patch,
                    "pkg/n.py": new_b,
                },
            ),
            _model(
                "C",
                {
                    "dir/deep/f.py": edit("c"),
                    "pkg/App.java": del_patch,
                },
            ),
        ]

        # Diverge: no meta on line 1; path on line 2; no wrap past row width.
        diff_cmp = compare_shared_file(models, "dir/deep/f.py")
        d0, d1 = diff_cmp.list_prompt().plain.splitlines()
        assert d0.startswith("diff")
        assert "f.py" in d0
        assert "1 site" not in d0
        assert d1.startswith("dir/") or d1.startswith(".../")
        assert len(d0) <= _LIST_ROW_WIDTH + 2
        assert len(d1) <= _LIST_ROW_WIDTH + 2

        # Delete: size right-aligned in meta column; path not on same line as size.
        del_cmp = compare_shared_file(models, "pkg/App.java")
        e0, e1 = del_cmp.list_prompt().plain.splitlines()
        assert e0.startswith("del")
        assert "App.java" in e0
        # Non-blank delete lines only (blank "-" rows are ignored in counts).
        assert e0.rstrip().endswith("-2") or "-2" in e0[-_LIST_META_WIDTH:]
        assert not e1.strip().startswith("-")
        assert e1.endswith("pkg/") or "pkg" in e1

        # New: size on line 1; coverage is not repeated (lives on group header).
        new_cmp = compare_shared_file(models, "pkg/n.py")
        n0, n1 = new_cmp.list_prompt().plain.splitlines()
        assert n0.startswith("new")
        assert "+" in n0
        assert "AB" not in n0
        assert "AB" not in n1

    def test_coverage_group_headers_not_repeated_on_files(self):
        from sfctl.diff_compare import (
            build_compare_list_entries,
            compare_all_shared,
            list_coverage_header_prompt,
        )

        models = [
            _model("A", {"a.py": _patch("shared-long-enough-xx", "only-a")}),
            _model("B", {"a.py": _patch("shared-long-enough-xx", "only-b")}),
            _model("C", {"a.py": _patch("shared-long-enough-xx"), "solo.py": _patch("c-only")}),
        ]
        compares = compare_all_shared(models)
        entries = build_compare_list_entries(compares)
        headers = [e for e in entries if e.is_header]
        files = [e for e in entries if not e.is_header]
        assert headers
        assert all(h.count >= 1 for h in headers)
        # Every file belongs to a group; headers carry coverage.
        assert {e.coverage for e in files} <= {h.coverage for h in headers}
        colors = {"A": "green", "B": "yellow", "C": "red"}
        for h in headers:
            prompt = list_coverage_header_prompt(
                h.coverage, h.kind, h.count, colors
            )
            plain = prompt.plain
            for ch in h.coverage:
                assert ch in plain
            assert str(h.count) in plain
            styles = {span.style for span in prompt.spans if span.style}
            if "A" in h.coverage:
                assert any("green" in str(s) for s in styles)

    def test_list_prompt_uses_rank_colors_for_size_meta(self):
        """File rows color +/- sizes; coverage is not on the file line."""
        from sfctl.constants import DIFF_ADD

        models = [
            _model("A", {"n.py": (
                "diff --git a/n.py b/n.py\n--- /dev/null\n+++ b/n.py\n"
                "@@ -0,0 +1,2 @@\n+a\n+b\n"
            )}),
            _model("B", {}),
            _model("C", {}),
        ]
        cmp = compare_shared_file(models, "n.py")
        prompt = cmp.list_prompt({"A": "green"})
        assert "+2" in prompt.plain
        assert "A" not in prompt.plain.splitlines()[0].replace("new", "")
        # Size token uses add color (not plain dim only).
        styles = {str(span.style) for span in prompt.spans if span.style}
        assert any(DIFF_ADD in s or "bold" in s for s in styles)

    def test_only_section_uses_rank_colors(self):
        from sfctl.diff_compare import format_compare_markup
        from sfctl.models import ModelScores
        from sfctl.ranking import model_letter_colors

        models = [
            _model("A", {"f.py": _patch("only-a-long-enough")}),
            _model("B", {"f.py": _patch("only-b-long-enough")}),
            _model("C", {"f.py": _patch("shared-line-long", "only-c-long-enough")}),
        ]
        # Force C first, A middle, B last via scores
        scores = [
            ModelScores(overall=0, response=0, code=1),   # A middle-ish
            ModelScores(overall=0, response=0, code=-2),  # B worst
            ModelScores(overall=0, response=0, code=5),   # C best
        ]
        # Ensure totals: C=5, A=1, B=-2
        colors = model_letter_colors(scores, [], 3)
        assert colors["C"] == "green"
        assert colors["B"] == "red"
        assert colors["A"] == "yellow"

        cmp = compare_shared_file(models, "f.py")
        markup = format_compare_markup(cmp, model_colors=colors)
        # Solo cards or design-tab labels get rank-colored letters.
        assert "[green bold]C[/]" in markup or "[green bold]C" in markup
        assert (
            "[red bold]B[/]" in markup
            or "[red bold]B" in markup
            or "[yellow bold]A[/]" in markup
            or "[yellow bold]A" in markup
        )
        assert "only-a-long-enough" in markup

    def test_unranked_letters_stay_plain(self):
        from sfctl.diff_compare import format_compare_markup
        from sfctl.models import ModelScores
        from sfctl.ranking import model_letter_colors

        models = [
            _model("A", {"f.py": _patch("only-a-long-enough")}),
            _model("B", {"f.py": _patch("only-b-long-enough")}),
            _model("C", {"f.py": _patch("only-c-long-enough")}),
        ]
        scores = [ModelScores() for _ in range(3)]
        colors = model_letter_colors(scores, [], 3)
        assert colors == {}
        markup = format_compare_markup(
            compare_shared_file(models, "f.py"), model_colors=colors,
        )
        assert "[green bold]" not in markup
        assert "only-a-long-enough" in markup


class TestSharedCompareHelp:
    @pytest.mark.asyncio
    async def test_help_screen_opens_and_closes(self, make_app, fixture_data):
        from sfctl.screens import SharedCompareHelpScreen, SharedCompareScreen

        app = make_app(data=fixture_data)
        comps = app.handler.shared_file_compares()
        if not comps:
            pytest.skip("fixture has no shared files")

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.push_screen(SharedCompareScreen(comps, 0))
            await pilot.pause()
            assert isinstance(app.screen, SharedCompareScreen)
            app.screen.action_show_help()
            await pilot.pause()
            assert isinstance(app.screen, SharedCompareHelpScreen)
            body = app.screen.query_one("#shared-help-body")
            assert body is not None
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, SharedCompareScreen)
            await pilot.press("escape")
            await pilot.pause()


class TestDesignTabSync:
    @pytest.mark.asyncio
    async def test_selecting_c_syncs_all_design_tabs(self, make_app):
        """Activating design C on one site activates C on every other site."""
        from textual.widgets import TabbedContent

        from sfctl.diff_compare import compare_shared_file
        from sfctl.screens import SharedCompareScreen

        def _site_patch(tag: str, old_start: int, body: str) -> str:
            return (
                f"@@ -{old_start},3 +{old_start},5 @@ def site_{old_start}():\n"
                f"     keep\n"
                f"+    {body}_{tag}_line_one_here\n"
                f"+    {body}_{tag}_line_two_here\n"
                f"     keep2\n"
            )

        # Two diverge sites so the detail mounts two TabbedContent widgets.
        patch_a = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
            + _site_patch("a", 10, "alpha")
            + _site_patch("a", 40, "beta")
        )
        patch_b = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
            + _site_patch("b", 10, "alpha")
            + _site_patch("b", 40, "beta")
        )
        patch_c = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
            + _site_patch("c", 10, "alpha")
            + _site_patch("c", 40, "beta")
        )
        models = [
            _model("A", {"f.py": patch_a}),
            _model("B", {"f.py": patch_b}),
            _model("C", {"f.py": patch_c}),
        ]
        comps = [compare_shared_file(models, "f.py")]
        app = make_app(
            task_id="t-tab-sync",
            data={
                "task": {"taskId": "t-tab-sync"},
                "content": {
                    "taskId": "t-tab-sync",
                    "content": {
                        "items": [
                            {"type": "text", "title": "Repository", "text": "r"},
                            {
                                "type": "message",
                                "title": "Current Prompt",
                                "content": "p",
                            },
                            {
                                "type": "collection",
                                "title": "Model Traces",
                                "items": [
                                    {
                                        "title": "Model A",
                                        "diff": {"codeDiff": patch_a},
                                        "trace": {
                                            "trace": "",
                                            "messages": "[]",
                                            "toolEvents": "[]",
                                        },
                                    },
                                    {
                                        "title": "Model B",
                                        "diff": {"codeDiff": patch_b},
                                        "trace": {
                                            "trace": "",
                                            "messages": "[]",
                                            "toolEvents": "[]",
                                        },
                                    },
                                    {
                                        "title": "Model C",
                                        "diff": {"codeDiff": patch_c},
                                        "trace": {
                                            "trace": "",
                                            "messages": "[]",
                                            "toolEvents": "[]",
                                        },
                                    },
                                ],
                            },
                        ]
                    },
                },
                "history": [],
                "feedback": {},
            },
        )

        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            screen = SharedCompareScreen(comps, 0)
            app.push_screen(screen)
            # Wait for async detail rebuild worker.
            for _ in range(30):
                await pilot.pause()
                tabs = list(screen.query(".shared-design-tabs"))
                if len(tabs) >= 2:
                    break
            tabs = list(screen.query(".shared-design-tabs"))
            assert len(tabs) >= 2, f"expected >=2 design tab bars, got {len(tabs)}"

            first = tabs[0]
            assert isinstance(first, TabbedContent)
            c_pane = None
            for pid in (p.id for p in first.query("TabPane")):
                if pid and pid.endswith("-C"):
                    c_pane = pid
                    break
            assert c_pane, f"no C pane in {list(p.id for p in first.query('TabPane'))}"
            first.active = c_pane
            await pilot.pause()
            # Handler may run sync; also exercise public path.
            screen._design_letter_pref = "C"
            screen._sync_design_tabs("C")
            await pilot.pause()

            for tc in tabs:
                assert isinstance(tc, TabbedContent)
                active = tc.active or ""
                assert active.endswith("-C"), f"{tc.id} active={active!r}"


class TestSharedCompareAction:
    def test_binding_gated(self, make_app, fixture_data):
        app = make_app(data=fixture_data)
        # Sample fixture models may or may not share files
        has = bool(app.handler.shared_file_compares())
        assert app.check_action("shared_compare", ()) is has

    @pytest.mark.skipif(not SIMILAR.exists(), reason="similar.json not present")
    def test_similar_enables_action(self, make_app):
        data = json.loads(SIMILAR.read_text())
        app = make_app(task_id=data["task"]["taskId"], data=data)
        comps = app.handler.shared_file_compares()
        assert len(comps) >= 1
        assert app.check_action("shared_compare", ()) is True

    @pytest.mark.skipif(not SIMILAR.exists(), reason="similar.json not present")
    @pytest.mark.asyncio
    async def test_shared_compare_modal_opens(self, make_app):
        from sfctl.screens import SharedCompareScreen

        data = json.loads(SIMILAR.read_text())
        app = make_app(task_id=data["task"]["taskId"], data=data)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.action_shared_compare()
            await pilot.pause()
            assert isinstance(app.screen, SharedCompareScreen)
            await pilot.press("c")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, SharedCompareScreen)

    @pytest.mark.asyncio
    async def test_open_patch_from_shared_compare(self, make_app):
        """Shift+1 (or !) leaves shared compare and opens that model's Diffs tab."""
        from textual.widgets import TabbedContent

        from sfctl.ids import model_id, model_tabs_id, tab_diffs_id
        from sfctl.screens import SharedCompareScreen

        patch = (
            "diff --git a/shared.py b/shared.py\n"
            "--- a/shared.py\n+++ b/shared.py\n"
            "@@ -1 +1,2 @@\n keep\n+added\n"
        )

        def _item(title: str) -> dict:
            return {
                "title": title,
                "diff": {"codeDiff": patch},
                "trace": {"trace": "summary", "messages": "[]", "toolEvents": "[]"},
            }

        data = {
            "task": {"taskId": "t-shared-nav"},
            "content": {
                "taskId": "t-shared-nav",
                "content": {
                    "items": [
                        {"type": "text", "title": "Repository", "text": "repo"},
                        {"type": "message", "title": "Current Prompt", "content": "p"},
                        {
                            "type": "collection",
                            "title": "Model Traces",
                            "items": [
                                _item("Model A"),
                                _item("Model B"),
                                _item("Model C"),
                            ],
                        },
                    ]
                },
            },
            "history": [],
            "feedback": {},
        }
        app = make_app(task_id="t-shared-nav", data=data)
        assert app.handler.shared_file_compares()

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.action_shared_compare()
            await pilot.pause()
            assert isinstance(app.screen, SharedCompareScreen)

            # Shift+1 may arrive as "!" depending on the pilot backend.
            await pilot.press("shift+1")
            await pilot.pause()
            if isinstance(app.screen, SharedCompareScreen):
                await pilot.press("!")
                await pilot.pause()
            assert not isinstance(app.screen, SharedCompareScreen)

            mid = model_id(0)
            tabs = app.query_one(f"#{model_tabs_id(mid)}", TabbedContent)
            assert tabs.active == tab_diffs_id(mid)

    @pytest.mark.asyncio
    async def test_select_design_keys_sync_tabs(self, make_app):
        """1/2/3 select design letter-runs without leaving the compare modal."""
        from textual.widgets import TabbedContent

        from sfctl.screens import SharedCompareScreen

        def _edit(path: str, start: int, body: str) -> str:
            return (
                f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
                f"@@ -{start},3 +{start},4 @@ def site():\n"
                f"     keep\n+{body}\n     after\n"
            )

        def _item(title: str, files: dict[str, str]) -> dict:
            # Multi-file traces: build one combined codeDiff (first file wins for parser)
            # Ranking parser uses file_diffs from model; use ModelData-style via full blob
            patches = "".join(files.values())
            return {
                "title": title,
                "diff": {"codeDiff": patches},
                "trace": {"trace": "summary", "messages": "[]", "toolEvents": "[]"},
            }

        # Two diverge sites so multi-design tabs exist and can sync.
        a = _edit("f.py", 10, "body_a") + _edit("f.py", 40, "body_a2")
        b = _edit("f.py", 10, "body_b") + _edit("f.py", 40, "body_b2")
        c = _edit("f.py", 10, "body_c") + _edit("f.py", 40, "body_c2")
        data = {
            "task": {"taskId": "t-design-keys"},
            "content": {
                "taskId": "t-design-keys",
                "content": {
                    "items": [
                        {"type": "text", "title": "Repository", "text": "repo"},
                        {"type": "message", "title": "Current Prompt", "content": "p"},
                        {
                            "type": "collection",
                            "title": "Model Traces",
                            "items": [
                                _item("Model A", {"f.py": a}),
                                _item("Model B", {"f.py": b}),
                                _item("Model C", {"f.py": c}),
                            ],
                        },
                    ]
                },
            },
            "history": [],
            "feedback": {},
        }
        app = make_app(task_id="t-design-keys", data=data)
        comps = app.handler.shared_file_compares()
        assert comps

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.action_shared_compare()
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, SharedCompareScreen)

            await pilot.press("3")
            await pilot.pause()
            assert isinstance(app.screen, SharedCompareScreen)
            assert screen._design_letter_pref == "C"
            tabbeds = list(screen.query(".shared-design-tabs"))
            assert tabbeds
            for tc in tabbeds:
                assert isinstance(tc, TabbedContent)
                active = tc.active or ""
                assert active.endswith("-C"), f"active={active!r}"

            await pilot.press("1")
            await pilot.pause()
            assert screen._design_letter_pref == "A"
            for tc in screen.query(".shared-design-tabs"):
                assert (tc.active or "").endswith("-A")


class TestSharedBadges:
    def test_path_and_cq_tones_share_module(self):
        from sfctl.badges import (
            badge_css_classes,
            badge_markup,
            badge_tone,
            path_badge_markup,
        )

        assert badge_tone("same") == "success"
        assert badge_tone("diff") == "warning"
        assert badge_tone("del") == "error"
        assert badge_tone("error") == "error"
        assert "ui-badge" in badge_css_classes("error", "violation-chip")
        assert "ui-badge-error" in badge_css_classes("error")
        assert "ui-badge-primary" in badge_css_classes(
            "primary", "violation-chip", "violation-mark"
        )
        m = path_badge_markup("same")
        assert "same" in m
        assert "on #" in m or "on #" in badge_markup("x", "success")
