"""Conformance fixtures for the normative path/site/design core.

These lock the design-paper checklist and critique adversarial cases.
Path badges use core_decisions only (policy A).
"""

from __future__ import annotations

from sfctl.diff_compare import (
    build_compare_list_entries,
    build_core_region_decisions,
    compare_all_shared,
    compare_shared_file,
    is_deleted_file_patch,
    is_new_file_patch,
)
from sfctl.models import FileDiff, ModelData


def _model(name: str, files: dict[str, str]) -> ModelData:
    return ModelData(
        name=name,
        diff="",
        trace_summary="",
        file_diffs=[FileDiff(filename=k, diff=v) for k, v in files.items()],
    )


def _edit(
    path: str,
    old_start: int,
    add_line: str,
    label: str = "def site():",
    *,
    ctx: str = "     keep",
) -> str:
    """One edit hunk. Vary *ctx* so full patches differ while BodyKey can match."""
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -{old_start},3 +{old_start},4 @@ {label}\n"
        f"{ctx}\n"
        f"+{add_line}\n"
        "     after\n"
    )


def _new_file(path: str, *lines: str) -> str:
    body = "\n".join(f"+{ln}" for ln in lines)
    n = len(lines)
    return (
        f"diff --git a/{path} b/{path}\n--- /dev/null\n+++ b/{path}\n"
        f"@@ -0,0 +1,{n} @@\n{body}\n"
    )


def _del_file(path: str, *lines: str) -> str:
    body = "\n".join(f"-{ln}" for ln in lines)
    n = len(lines)
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ /dev/null\n"
        f"@@ -1,{n} +0,0 @@\n{body}\n"
    )


class TestConformancePathBadges:
    def test_1_multi_identical_edit_is_same(self):
        p = _edit("f.py", 10, "shared_edit_line_xx")
        cmp = compare_shared_file(
            [_model("A", {"f.py": p}), _model("B", {"f.py": p}), _model("C", {"f.py": p})],
            "f.py",
        )
        assert cmp.kind_badge() == "same"
        assert cmp.identical_patches

    def test_2_multi_identical_pure_new_is_new(self):
        p = _new_file("f.py", "hello", "world")
        cmp = compare_shared_file(
            [_model("A", {"f.py": p}), _model("B", {"f.py": p}), _model("C", {"f.py": p})],
            "f.py",
        )
        assert cmp.is_all_new and cmp.identical_patches
        assert cmp.kind_badge() == "new"

    def test_3_solo_pure_delete_is_del(self):
        p = _del_file("f.py", "line-a", "line-b")
        cmp = compare_shared_file(
            [_model("A", {"f.py": p}), _model("B", {}), _model("C", {})],
            "f.py",
        )
        assert cmp.kind_badge() == "del"
        assert cmp.is_all_deleted

    def test_4_pair_site_path_is_diff(self):
        ab = _edit("f.py", 40, "body_ab_shared")
        c = _edit("f.py", 40, "body_c_unique")
        cmp = compare_shared_file(
            [
                _model("A", {"f.py": ab}),
                _model("B", {"f.py": ab}),
                _model("C", {"f.py": c}),
            ],
            "f.py",
        )
        assert any(d.relation == "pair" for d in cmp.core_decisions)
        assert cmp.kind_badge() == "diff"

    def test_5_same_plus_diverge_is_diff_not_share(self):
        same = _edit("f.py", 10, "shared_add_line_xx", "def shared():")
        a = same + _edit("f.py", 40, "design_a_only", "def diverge():")
        b = same + _edit("f.py", 40, "design_b_only", "def diverge():")
        c = same
        cmp = compare_shared_file(
            [
                _model("A", {"f.py": a}),
                _model("B", {"f.py": b}),
                _model("C", {"f.py": c}),
            ],
            "f.py",
        )
        assert any(d.relation == "same" for d in cmp.core_decisions)
        assert any(d.relation == "diverge" for d in cmp.core_decisions)
        assert cmp.kind_badge() == "diff"

    def test_6_same_token_two_functions_two_core_sites(self):
        # Same add text at two old_starts — core keeps two sites (no token Shared).
        # Different context so full patches are not identical (sites still built).
        a = _edit("f.py", 10, "import_os_shared", "def f1():", ctx="     keep_a") + _edit(
            "f.py", 50, "import_os_shared", "def f2():", ctx="     keep_a"
        )
        b = _edit("f.py", 10, "import_os_shared", "def f1():", ctx="     keep_b") + _edit(
            "f.py", 50, "import_os_shared", "def f2():", ctx="     keep_b"
        )
        c = _edit("f.py", 10, "import_os_shared", "def f1():", ctx="     keep_c") + _edit(
            "f.py", 50, "import_os_shared", "def f2():", ctx="     keep_c"
        )
        cmp = compare_shared_file(
            [
                _model("A", {"f.py": a}),
                _model("B", {"f.py": b}),
                _model("C", {"f.py": c}),
            ],
            "f.py",
        )
        assert not cmp.identical_patches
        starts = {d.old_start for d in cmp.core_decisions}
        assert 10 in starts and 50 in starts
        assert len(cmp.core_decisions) >= 2

    def test_7_mixed_new_and_edit_is_diff_not_all_new(self):
        # A pure-add path p; B edit existing p → not all_new; path diff.
        new_a = _new_file("p.py", "only_a")
        edit_b = _edit("p.py", 5, "only_b_edit")
        cmp = compare_shared_file(
            [
                _model("A", {"p.py": new_a}),
                _model("B", {"p.py": edit_b}),
                _model("C", {}),
            ],
            "p.py",
        )
        assert not cmp.is_all_new
        assert cmp.kind_badge() == "diff"
        # No invented path-level token Shared KPI in list stats.
        assert not any(
            tok.endswith(" same") and not tok[0].isdigit()
            for tok in cmp._stats_bits()
            if "site" not in tok
        )

    def test_8_mid_file_pure_add_no_set_shared_in_core(self):
        # Overlapping add lines at same start: BodyKey equality only if bodies match.
        a = _edit("f.py", 20, "shared_line_xx")
        b = _edit("f.py", 20, "shared_line_xx")
        c = _edit("f.py", 20, "other_line_yy")
        cmp = compare_shared_file(
            [
                _model("A", {"f.py": a}),
                _model("B", {"f.py": b}),
                _model("C", {"f.py": c}),
            ],
            "f.py",
        )
        # AB same body, C differs → pair or diverge; not inventing Shared from tokens elsewhere
        assert cmp.core_decisions
        assert any(d.relation in {"pair", "diverge", "same"} for d in cmp.core_decisions)

    def test_9_crlf_vs_lf_identical_edit_is_same(self):
        lf = _edit("f.py", 10, "body_line")
        crlf = lf.replace("\n", "\r\n")
        cmp = compare_shared_file(
            [
                _model("A", {"f.py": lf}),
                _model("B", {"f.py": crlf}),
                _model("C", {"f.py": lf}),
            ],
            "f.py",
        )
        assert cmp.identical_patches or cmp.kind_badge() in {"same", "share", "diff"}
        # NormalizePatch should make multi identical when only CR differs
        assert cmp.kind_badge() == "same"

    def test_10_all_unique_sites_abc_is_diff(self):
        a = _edit("f.py", 10, "only_a")
        b = _edit("f.py", 20, "only_b")
        c = _edit("f.py", 30, "only_c")
        cmp = compare_shared_file(
            [
                _model("A", {"f.py": a}),
                _model("B", {"f.py": b}),
                _model("C", {"f.py": c}),
            ],
            "f.py",
        )
        assert cmp.coverage == "ABC"
        assert all(d.relation == "only" for d in cmp.core_decisions)
        assert cmp.kind_badge() == "diff"

    def test_11_same_body_different_old_start_two_core_sites(self):
        """A1: core does not coalesce different old_starts."""
        body = "identical_call_site_body"
        a = _edit("f.py", 10, body, ctx="     a0") + _edit("f.py", 80, body, ctx="     a1")
        b = _edit("f.py", 10, body, ctx="     b0") + _edit("f.py", 80, body, ctx="     b1")
        c = _edit("f.py", 10, body, ctx="     c0") + _edit("f.py", 80, body, ctx="     c1")
        cmp = compare_shared_file(
            [
                _model("A", {"f.py": a}),
                _model("B", {"f.py": b}),
                _model("C", {"f.py": c}),
            ],
            "f.py",
        )
        assert not cmp.identical_patches
        assert len(cmp.core_decisions) == 2
        # Presentation may collapse; badge still from core (both same → share)
        assert cmp.kind_badge() == "share"
        assert len(cmp.decisions) <= len(cmp.core_decisions)

    def test_12_n2_two_bodies_is_diverge_not_pair(self):
        a = _edit("f.py", 10, "body_a")
        b = _edit("f.py", 10, "body_b")
        cmp = compare_shared_file(
            [_model("A", {"f.py": a}), _model("B", {"f.py": b}), _model("C", {})],
            "f.py",
        )
        assert cmp.coverage == "AB"
        assert any(d.relation == "diverge" for d in cmp.core_decisions)
        assert not any(d.relation == "pair" for d in cmp.core_decisions)
        assert cmp.kind_badge() == "diff"


class TestCoreVsPresentation:
    def test_badge_uses_core_not_coalesced_only(self):
        """Policy A: core sites drive badge even when display coalesces."""
        body = "repeat_me_xx"
        a = _edit("f.py", 10, body, ctx="     a") + _edit("f.py", 80, body, ctx="     a")
        b = _edit("f.py", 10, body, ctx="     b") + _edit("f.py", 80, body, ctx="     b")
        c = _edit("f.py", 10, body, ctx="     c") + _edit("f.py", 80, body, ctx="     c")
        cmp = compare_shared_file(
            [
                _model("A", {"f.py": a}),
                _model("B", {"f.py": b}),
                _model("C", {"f.py": c}),
            ],
            "f.py",
        )
        assert len(cmp.core_decisions) == 2
        assert len(cmp.decisions) <= len(cmp.core_decisions)
        assert cmp.kind_badge() == "share"

    def test_stats_from_core_when_presentation_filtered(self):
        """Policy A metrics: list stats use core sites even if HasSignal empties display."""
        # Comment-only multi-present same + a unique comment site.
        def _comment(path: str, start: int, text: str) -> str:
            return (
                f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
                f"@@ -{start},2 +{start},3 @@\n keep\n+{text}\n keep2\n"
            )

        a = _comment("f.py", 10, "# shared comment body") + _comment(
            "f.py", 40, "# only a"
        )
        b = _comment("f.py", 10, "# shared comment body") + _comment(
            "f.py", 40, "# only b"
        )
        c = _comment("f.py", 10, "# shared comment body")
        cmp = compare_shared_file(
            [
                _model("A", {"f.py": a}),
                _model("B", {"f.py": b}),
                _model("C", {"f.py": c}),
            ],
            "f.py",
        )
        assert cmp.core_decisions
        # Display may drop all non-signal sites.
        assert cmp.decisions == [] or len(cmp.decisions) <= len(cmp.core_decisions)
        assert cmp.kind_badge() in {"share", "diff"}
        bits = cmp._stats_bits()
        # Must report core site geometry, not bag-of-token "N same" / "differs".
        assert any("site" in tok for tok in bits)
        assert "differs" not in bits

    def test_cross_locus_token_not_shared_kpi(self):
        """Same token at two base lines → no path share from token set; stats are sites."""
        a = _edit("f.py", 10, "return True") + _edit("f.py", 80, "only_a")
        b = _edit("f.py", 10, "only_b") + _edit("f.py", 80, "return True")
        c = _edit("f.py", 10, "return True") + _edit("f.py", 80, "only_c")
        cmp = compare_shared_file(
            [
                _model("A", {"f.py": a}),
                _model("B", {"f.py": b}),
                _model("C", {"f.py": c}),
            ],
            "f.py",
        )
        assert cmp.kind_badge() == "diff"
        # Token helper still sees the line (jump targets) — must not pollute stats.
        assert "return True" in cmp.agreement_adds or "return True" in (
            cmp.consensus_adds or []
        )
        bits = " ".join(cmp._stats_bits())
        assert "site" in bits
        # No path-level "1 same" style token KPI (site breakdown uses "N same").
        assert cmp.agreement_adds  # helper lives
        # Badge is not share despite token overlap.
        assert cmp.kind_badge() != "share"

    def test_build_core_region_decisions_export(self):
        a = _edit("f.py", 10, "x")
        b = _edit("f.py", 10, "x")
        c = _edit("f.py", 10, "y")
        patches = (a, b, c)
        empty = (False, False, False)
        core = build_core_region_decisions(patches, empty)
        assert len(core) == 1
        assert core[0].relation == "pair"

    def test_a2_five_same_one_diverge_is_diff(self):
        """Partial agreement does not promote path to share."""
        chunks_same = [
            _edit("f.py", 10 + i * 10, f"shared_body_{i}", ctx=f"     c{i}")
            for i in range(5)
        ]
        same_block = "".join(chunks_same)
        a = same_block + _edit("f.py", 100, "design_a", ctx="     da")
        b = same_block + _edit("f.py", 100, "design_b", ctx="     db")
        c = same_block + _edit("f.py", 100, "design_c", ctx="     dc")
        # Make full patches non-identical via diverge site only (same sites match).
        cmp = compare_shared_file(
            [
                _model("A", {"f.py": a}),
                _model("B", {"f.py": b}),
                _model("C", {"f.py": c}),
            ],
            "f.py",
        )
        assert sum(1 for d in cmp.core_decisions if d.relation == "same") >= 5
        assert any(d.relation == "diverge" for d in cmp.core_decisions)
        assert cmp.kind_badge() == "diff"


class TestPredicatesAndCoverage:
    def test_new_file_ignores_no_newline_marker(self):
        p = (
            "@@ -0,0 +1,1 @@\n"
            "+hello\n"
            "\\ No newline at end of file\n"
        )
        assert is_new_file_patch(p)

    def test_deleted_file_ignores_no_newline_marker(self):
        p = (
            "@@ -1,1 +0,0 @@\n"
            "-goodbye\n"
            "\\ No newline at end of file\n"
        )
        assert is_deleted_file_patch(p)

    def test_coverage_key_is_sorted_letters(self):
        # B and A present → "AB" not "BA"
        a = _edit("f.py", 10, "x")
        b = _edit("f.py", 10, "x")
        cmp = compare_shared_file(
            [_model("A", {"f.py": a}), _model("B", {"f.py": b}), _model("C", {})],
            "f.py",
        )
        assert cmp.coverage == "AB"

    def test_list_groups_by_canonical_coverage(self):
        models = [
            _model("A", {"z.py": _new_file("z.py", "a"), "m.py": _edit("m.py", 1, "x")}),
            _model("B", {"m.py": _edit("m.py", 1, "x")}),
            _model("C", {"m.py": _edit("m.py", 1, "x")}),
        ]
        compares = compare_all_shared(models)
        entries = build_compare_list_entries(compares)
        headers = [e for e in entries if e.is_header]
        # Coverage keys on headers are canonical
        for h in headers:
            assert h.coverage == "".join(sorted(h.coverage))


class TestSizeMetaDefinition:
    def test_new_size_is_nonblank_add_count(self):
        p = _new_file("n.py", "a", "", "b", "c")
        cmp = compare_shared_file(
            [_model("A", {"n.py": p}), _model("B", {}), _model("C", {})],
            "n.py",
        )
        # three non-blank adds
        assert cmp._list_meta_text() == "+3"

    def test_del_size_is_nonblank_del_count(self):
        p = _del_file("d.py", "a", "b")
        cmp = compare_shared_file(
            [_model("A", {"d.py": p}), _model("B", {"d.py": p}), _model("C", {"d.py": p})],
            "d.py",
        )
        assert cmp._list_meta_text() == "-2"


class TestSpanCoalesceAndSignal:
    def test_overlapping_spans_merge_in_presentation(self):
        """Spans [10,30) and [20,25) co-locate in presentation, not core."""
        from sfctl.diff_compare import _hunk_base_span, parse_patch_hunks

        # Overlapping base spans: [10,15) vs [12,14)
        a = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
            "@@ -10,5 +10,6 @@ def region():\n"
            "     a0\n"
            "     a1\n"
            "     a2\n"
            "     a3\n"
            "     a4\n"
            "+    from_a\n"
        )
        b = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
            "@@ -12,2 +12,3 @@ def region():\n"
            "     a2\n"
            "     a3\n"
            "+    from_b\n"
        )
        # A span [10,15), B span [12,14) — overlap
        ha = parse_patch_hunks(a, 0)[0]
        hb = parse_patch_hunks(b, 1)[0]
        assert _hunk_base_span(ha)[0] <= _hunk_base_span(hb)[0] < _hunk_base_span(ha)[1]
        cmp = compare_shared_file(
            [
                _model("A", {"f.py": a}),
                _model("B", {"f.py": b}),
                _model("C", {}),
            ],
            "f.py",
        )
        # Core: two different old_starts → two sites
        assert len(cmp.core_decisions) == 2
        # Presentation: span merge → one site with both models
        assert len(cmp.decisions) == 1
        assert cmp.decisions[0].coverage in {"AB", "A", "B"} or len(
            cmp.decisions[0].present_indices
        ) == 2

    def test_pure_delete_is_signal(self):
        from sfctl.diff_compare import _decision_has_signal, build_core_region_decisions

        p = _del_file("f.py", "important_function_body")
        core = build_core_region_decisions((p, p, ""), (False, False, True))
        assert core
        assert _decision_has_signal(core[0])


class TestRenameAliases:
    def test_rename_links_old_and_new_path(self):
        rename = (
            "diff --git a/old.py b/new.py\n"
            "rename from old.py\n"
            "rename to new.py\n"
            "--- a/old.py\n+++ b/new.py\n"
            "@@ -1,2 +1,3 @@\n keep\n+added\n keep2\n"
        )
        edit_old = (
            "diff --git a/old.py b/old.py\n--- a/old.py\n+++ b/old.py\n"
            "@@ -1,2 +1,3 @@\n keep\n+added_by_b\n keep2\n"
        )
        from sfctl.diff_compare import compare_all_shared, parse_rename_pair

        assert parse_rename_pair(rename) == ("old.py", "new.py")
        models = [
            _model("A", {"new.py": rename}),
            _model("B", {"old.py": edit_old}),
            _model("C", {}),
        ]
        compares = compare_all_shared(models)
        names = {c.filename for c in compares}
        # Canonical target should appear; both models present on that comparison
        assert "new.py" in names
        c = next(x for x in compares if x.filename == "new.py")
        assert c.n_models >= 2


class TestPathFilterSemantics:
    def test_filter_modes(self):
        from sfctl.diff_compare import path_matches_filter

        # share path
        same = _edit("s.py", 10, "shared_xx")
        share_cmp = compare_shared_file(
            [
                _model("A", {"s.py": same}),
                _model("B", {"s.py": same}),
                _model("C", {"s.py": same}),
            ],
            "s.py",
        )
        # force non-identical via ctx already same body - use identical for same badge
        assert path_matches_filter(share_cmp, "consensus")
        assert not path_matches_filter(share_cmp, "pairs")

        # pair path
        ab = _edit("p.py", 10, "ab_body")
        c = _edit("p.py", 10, "c_body")
        pair_cmp = compare_shared_file(
            [
                _model("A", {"p.py": ab}),
                _model("B", {"p.py": ab}),
                _model("C", {"p.py": c}),
            ],
            "p.py",
        )
        assert path_matches_filter(pair_cmp, "pairs")
        assert path_matches_filter(pair_cmp, "diverge")
        assert not path_matches_filter(pair_cmp, "consensus")

        # solo
        solo = compare_shared_file(
            [
                _model("A", {"u.py": _edit("u.py", 1, "only_a")}),
                _model("B", {}),
                _model("C", {}),
            ],
            "u.py",
        )
        assert path_matches_filter(solo, "unique")
        assert not path_matches_filter(solo, "pairs")
