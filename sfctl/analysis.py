"""Task analysis pipeline: rule-based fact checks and recommendation."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field

from sfctl.history import history_justification
from sfctl.models import AnalysisResult, QualitySignal
from sfctl.proposal import _proposal_run_elapsed_ms, sf_value
from sfctl.task_types import TaskType, detect_task_type


def _extract_text(content: list | str) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "content":
            continue
        inner = block.get("content", "")
        txt = inner.get("text", "") if isinstance(inner, dict) else (
            inner if isinstance(inner, str) else ""
        )
        if txt:
            parts.append(txt)
    return "".join(parts)


def _code_diff(model: dict) -> str:
    return (model.get("diff") or {}).get("codeDiff", "") or ""


def _parse_model_tool_events(model: dict) -> dict:
    """Extract bash commands and web search queries from a model trace's toolEvents."""
    trace = model.get("trace") or {}
    te_raw = trace.get("toolEvents", "")
    if not te_raw:
        return {"bash_commands": [], "web_search_queries": []}
    if isinstance(te_raw, str):
        try:
            events = json.loads(te_raw)
        except (json.JSONDecodeError, ValueError):
            return {"bash_commands": [], "web_search_queries": []}
    else:
        events = te_raw if isinstance(te_raw, list) else []

    bash_commands: list[tuple[str, str]] = []
    web_search_queries: list[str] = []
    for ev in events:
        name = ev.get("name", "")
        args = ev.get("arguments", {}) if isinstance(ev.get("arguments"), dict) else {}
        result = ev.get("result", "") or ""
        if name in ("run_terminal_cmd", "run_terminal_command"):
            cmd = args.get("command", "")
            if cmd:
                bash_commands.append((cmd, result if isinstance(result, str) else ""))
        elif name == "WebSearch":
            q = args.get("query", "") or args.get("search_term", "")
            if q:
                web_search_queries.append(q)
    return {"bash_commands": bash_commands, "web_search_queries": web_search_queries}


def _ranking_ids(entry: dict, key: str) -> list[str]:
    return [x.get("id", "") for x in (entry.get(key) or {}).get("value", [])]


def _truncate(s: str, n: int) -> str:
    return s[:n] + "..." if len(s) > n else s


@dataclass
class AnalysisContext:
    task_type: TaskType
    history: list[dict]
    entry: dict
    action_history: list[dict]
    justification: str
    model_traces: list[dict] = field(default_factory=list)
    elapsed_ms: int | None = None
    solved: str = ""
    bash_commands: list[tuple[str, str]] = field(default_factory=list)
    web_search_queries: list[str] = field(default_factory=list)
    tool_call_count: int = 0
    reasoning_chars: int = 0
    session_ms: int = 0
    total_diff_lines: int = 0

    @classmethod
    def build(cls, data: dict) -> AnalysisContext:
        history = data.get("history", [])
        entry = history[-1] if history else {}
        action_history = data.get("task", {}).get("actionHistory", [])
        task_type = detect_task_type(data)
        justification = history_justification(entry) if entry else ""

        model_traces: list[dict] = []
        for item in data.get("content", {}).get("content", {}).get("items", []):
            if item.get("type") == "collection" and item.get("title") == "Model Traces":
                model_traces = item.get("items", [])
                break

        session_ms = 0
        for s in (entry.get("finalUserTaskSessionTimes") or []):
            try:
                session_ms += int(s.get("endTime", 0)) - int(s.get("startTime", 0))
            except (ValueError, TypeError):
                pass

        total_diff_lines = 0
        for model in model_traces:
            diff = _code_diff(model)
            if diff:
                total_diff_lines += sum(
                    1 for line in diff.splitlines()
                    if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
                )
            model["_tool_data"] = _parse_model_tool_events(model)

        elapsed_ms: int | None = None
        solved = ""
        bash_commands: list[tuple[str, str]] = []
        web_search_queries: list[str] = []
        tool_call_count = 0
        reasoning_chars = 0

        if task_type == TaskType.PROJECT_PROPOSAL and entry:
            elapsed_ms = _proposal_run_elapsed_ms(entry)
            solved = sf_value(entry.get("opus_solved"))
            trace = data.get("trace", {})
            msgs = trace.get("trace", []) if isinstance(trace, dict) else []
            for msg in (msgs if isinstance(msgs, list) else []):
                role = msg.get("role", "")
                if role in ("assistant", "assistant_thinking"):
                    c = msg.get("content", "")
                    reasoning_chars += len(c) if isinstance(c, str) else 0
                    continue
                if role != "tool_call":
                    continue
                tool_call_count += 1
                ri = msg.get("rawInput") or {}
                output_text = _extract_text(msg.get("content", ""))
                variant = ri.get("variant", "") if isinstance(ri, dict) else ""
                if variant in ("Bash", "RunTerminalCommand"):
                    cmd = ri.get("command", "")
                    if cmd:
                        bash_commands.append((cmd, output_text))
                elif variant == "WebSearch":
                    q = ri.get("query", "")
                    if q:
                        web_search_queries.append(q)

        return cls(
            task_type=task_type, history=history, entry=entry,
            action_history=action_history, justification=justification,
            model_traces=model_traces, elapsed_ms=elapsed_ms, solved=solved,
            bash_commands=bash_commands, web_search_queries=web_search_queries,
            tool_call_count=tool_call_count, reasoning_chars=reasoning_chars,
            session_ms=session_ms, total_diff_lines=total_diff_lines,
        )


class Rule:
    name: str = ""
    severity: str = "warn"
    scope: TaskType | None = None

    def check(self, ctx: AnalysisContext) -> QualitySignal | list[QualitySignal] | None:
        raise NotImplementedError

    def signal(self, desc: str = "") -> QualitySignal:
        return QualitySignal(name=self.name, severity=self.severity, description=desc)


RULES: list[Rule] = []


def rule(cls: type[Rule]) -> type[Rule]:
    RULES.append(cls())
    return cls


@rule
class BrokenTrace(Rule):
    name = "broken_trace"
    severity = "quarantine"
    scope = TaskType.CODE_REVIEW

    def check(self, ctx):
        signals = []
        for model in ctx.model_traces:
            name = model.get("title", "?")
            messages_str = (model.get("trace") or {}).get("messages", "")
            if not messages_str or not messages_str.strip():
                signals.append(self.signal(f"{name}: trace messages is empty"))
                continue
            try:
                parsed = json.loads(messages_str)
            except (json.JSONDecodeError, ValueError):
                signals.append(self.signal(f"{name}: trace messages is not valid JSON"))
                continue
            if not isinstance(parsed, list) or len(parsed) == 0:
                signals.append(self.signal(f"{name}: trace messages parsed to empty list"))
                continue
            last_msg = parsed[-1] if isinstance(parsed[-1], dict) else {}
            last_role = last_msg.get("role", "")
            if last_role in ("tool_call", "tool_result"):
                status = last_msg.get("status", "")
                if status == "completed":
                    continue
                signals.append(QualitySignal(
                    name="truncated_trace", severity="quarantine",
                    description=f"{name}: trace ends on {last_role} (model was cut off)",
                ))
        return signals


@rule
class EmptyDiff(Rule):
    name = "empty_diff"
    severity = "fail"
    scope = TaskType.CODE_REVIEW

    def check(self, ctx):
        return [
            self.signal(f"{model.get('title', '?')}: no code diff produced")
            for model in ctx.model_traces if not _code_diff(model).strip()
        ]


@rule
class IdenticalDiffs(Rule):
    name = "identical_diffs"
    severity = "warn"
    scope = TaskType.CODE_REVIEW

    def check(self, ctx):
        diffs = [
            (model.get("title", "?"), _code_diff(model).strip())
            for model in ctx.model_traces if _code_diff(model).strip()
        ]
        return [
            self.signal(f"{diffs[i][0]} and {diffs[j][0]} produced identical diffs")
            for i in range(len(diffs)) for j in range(i + 1, len(diffs))
            if diffs[i][1] == diffs[j][1]
        ]


@rule
class NoDiffFileRefs(Rule):
    name = "no_diff_file_refs"
    severity = "warn"
    scope = TaskType.CODE_REVIEW

    def check(self, ctx):
        if not ctx.justification or len(ctx.justification.split()) < 50:
            return None
        just_lower = ctx.justification.lower()
        filenames: set[str] = set()
        identifiers: set[str] = set()
        for model in ctx.model_traces:
            diff = _code_diff(model)
            for m in re.finditer(r"diff --git a/(\S+)", diff):
                filenames.add(m.group(1).split("/")[-1])
            for m in re.finditer(
                r"^@@[^@]+@@\s*.*?(?:def|fn|func|function|impl|class)\s+(\w+)",
                diff, re.MULTILINE,
            ):
                identifiers.add(m.group(1))
            for m in re.finditer(
                r"^[+-]\s*(?:def|fn|func|function|impl|class|struct|trait|interface|enum)\s+(\w+)",
                diff, re.MULTILINE,
            ):
                identifiers.add(m.group(1))
        identifiers.discard("self")
        identifiers.discard("test")
        if not filenames:
            return None
        if any(fn.lower() in just_lower for fn in filenames):
            return None
        if any(len(name) >= 4 and name.lower() in just_lower for name in identifiers):
            return None
        sample = ", ".join(sorted(filenames)[:5])
        if len(filenames) > 5:
            sample += f" (+{len(filenames) - 5} more)"
        return self.signal(f"Justification doesn't reference any changed files: {sample}")


@rule
class ShortSession(Rule):
    name = "short_session"
    severity = "warn"

    def check(self, ctx):
        if 0 < ctx.session_ms < 120000:
            return self.signal(f"Review session was only {ctx.session_ms / 1000:.0f}s")
        return None


@rule
class SuspiciousWps(Rule):
    name = "suspicious_wps"
    severity = "fail"
    scope = TaskType.CODE_REVIEW

    def check(self, ctx):
        words = len(ctx.justification.split()) if ctx.justification else 0
        if words < 100 or ctx.session_ms <= 0:
            return None
        wps = words / (ctx.session_ms / 1000)
        if wps >= 5:
            return self.signal(
                f"{words} words in {ctx.session_ms / 1000:.0f}s ({wps:.1f} words/sec)"
            )
        return None


@rule
class RankingFlip(Rule):
    name = "ranking_flip"
    severity = "warn"
    scope = TaskType.CODE_REVIEW

    def check(self, ctx):
        signals = []
        for i in range(1, len(ctx.history)):
            prev, curr = ctx.history[i - 1], ctx.history[i]
            prev_pref = _ranking_ids(prev, "preference_ranking")
            curr_pref = _ranking_ids(curr, "preference_ranking")
            if prev_pref and curr_pref and prev_pref != curr_pref:
                if prev.get("reviewLevel") == curr.get("reviewLevel"):
                    signals.append(self.signal(
                        f"Ranking changed at L{curr.get('reviewLevel', '?')} (same level)",
                    ))
        return signals


@rule
class EmptyJustification(Rule):
    name = "empty_justification"
    severity = "fail"
    scope = TaskType.CODE_REVIEW

    def check(self, ctx):
        if not ctx.justification:
            return self.signal("No justification text")
        return None


@rule
class ShortJustification(Rule):
    name = "short_justification"
    severity = "warn"
    scope = TaskType.CODE_REVIEW

    def check(self, ctx):
        if ctx.justification and len(ctx.justification.split()) < 50:
            return self.signal(f"Justification is only {len(ctx.justification.split())} words")
        return None


@rule
class NoCodeRefs(Rule):
    name = "no_code_refs"
    severity = "warn"
    scope = TaskType.CODE_REVIEW

    def check(self, ctx):
        j = ctx.justification
        if not j or len(j.split()) < 50:
            return None
        refs = (
            len(re.findall(r"[\w/]+\.\w+(?::\d+)?", j))
            + len(re.findall(r"`[^`]+`", j))
            + len(re.findall(r"line\s+\d+", j, re.IGNORECASE))
        )
        if refs == 0:
            return self.signal("No file paths, backtick refs, or line references in justification")
        return None


@rule
class EmptyCodePatch(Rule):
    name = "empty_code_patch"
    severity = "warn"
    scope = TaskType.PROJECT_PROPOSAL

    def check(self, ctx):
        rollouts = (ctx.entry.get("coding_question") or {}).get("rollouts", {})
        turns = rollouts.get("A", {}).get("turns", [])
        if not turns:
            return None
        for turn in turns:
            patch = turn.get("codePatch")
            if patch and (not isinstance(patch, str) or patch.strip()):
                return None
        if ctx.solved in ("full", "yes"):
            return QualitySignal(
                name="solved_no_patch", severity="fail",
                description=f"Marked solved but no code patch in any of {len(turns)} turns",
            )
        return self.signal(f"No code patch in any of {len(turns)} turns")


@rule
class SolvedWithIssues(Rule):
    name = "solved_with_issues"
    severity = "warn"
    scope = TaskType.PROJECT_PROPOSAL

    def check(self, ctx):
        issues = sf_value(ctx.entry.get("opus_issues_partial") or ctx.entry.get("opus_issues_no") or {})
        if ctx.solved in ("full", "yes") and issues and len(issues) > 50:
            return self.signal(f"Marked solved but has issues: {_truncate(issues.strip(), 120)}")
        return None


@rule
class ModelLooping(Rule):
    name = "model_looping"
    severity = "warn"
    scope = TaskType.PROJECT_PROPOSAL

    def check(self, ctx):
        if not ctx.bash_commands:
            return None
        counts = Counter(cmd for cmd, _ in ctx.bash_commands)
        top_cmd, top_cnt = counts.most_common(1)[0]
        if top_cnt >= 10:
            return self.signal(f"Model ran same command {top_cnt}x: `{_truncate(top_cmd.strip(), 60)}`")
        return None


@rule
class LowReasoning(Rule):
    name = "low_reasoning"
    severity = "warn"
    scope = TaskType.PROJECT_PROPOSAL

    def check(self, ctx):
        if ctx.tool_call_count < 20:
            return None
        ratio = ctx.reasoning_chars / ctx.tool_call_count
        if ratio < 50:
            return self.signal(
                f"Only {ratio:.0f} chars of reasoning per tool call "
                f"({ctx.reasoning_chars} chars across {ctx.tool_call_count} calls)"
            )
        return None


@rule
class FastSolve(Rule):
    name = "fast_solve"
    severity = "warn"
    scope = TaskType.PROJECT_PROPOSAL

    def check(self, ctx):
        if ctx.elapsed_ms and ctx.solved in ("full", "yes") and ctx.elapsed_ms < 300000:
            return self.signal(f"Solved in {ctx.elapsed_ms / 60000:.1f}min")
        return None


_DURATION_RANGES: dict[str, tuple[int, int]] = {
    "1-3m": (1, 3), "3-5m": (3, 5), "5-10m": (5, 10),
    "10-20m": (10, 20), "20-40m": (20, 40), "40m-1h": (40, 60),
    "1h-2h": (60, 120), "2h+": (120, 999),
}


@rule
class DurationMismatch(Rule):
    name = "duration_mismatch"
    severity = "warn"
    scope = TaskType.PROJECT_PROPOSAL

    def check(self, ctx):
        reported = sf_value(ctx.entry.get("opus_duration"))
        if not ctx.elapsed_ms or not reported:
            return None
        r = _DURATION_RANGES.get(reported)
        if not r:
            return None
        low, high = r
        actual_min = ctx.elapsed_ms / 60000
        if actual_min < low * 0.25:
            return QualitySignal(
                name=self.name, severity="fail",
                description=f"Reported {reported} but actual was {actual_min:.1f}min",
            )
        if actual_min < low * 0.5 or actual_min > high * 2:
            return self.signal(f"Reported {reported} but actual was {actual_min:.1f}min")
        return None


_LOCAL_INSTALL_RE = re.compile(
    r"\binstall\s+(-\S+\s+)*(-e\s+|--editable\s+)"
    r"|\binstall\s+(-\S+\s+)*[./]"
    r"|\binstall\s+(-\S+\s+)*-r\s"
    r"|\binstall\s*$"
    r"|\b(?:npm|yarn|pnpm|bun)\s+(?:install|i|ci)\s*(?:--\S+\s*)*(?:$|[|>&2])"
    r"|\bbundle\s+install\b|\bpoetry\s+install\b"
    r"|\bdotnet\s+restore\b"
    r"|\bgo\s+(?:install|mod\s+download)\s+\./",
    re.IGNORECASE | re.MULTILINE,
)

_EXT_INSTALL_SPECS: list[tuple[str, str | None]] = [
    (
        r"\bpip3?\s+install\b|\bpython3?\s+-m\s+pip\s+install\b"
        r"|\buv\s+(?:pip\s+install|add)\b"
        r"|\bpipx?\s+install\b|\bconda\s+install\b"
        r"|\bpoetry\s+add\b",
        r"already satisfied",
    ),
    (
        r"\b(?:npm|yarn|pnpm|bun)\s+(?:install|add|i)\b",
        r"up to date|already satisfied",
    ),
    (
        r"\bapt(?:-get)?\s+install\b|\bapk\s+add\b"
        r"|\bpacman\s+-S\b|\b(?:yum|dnf)\s+install\b",
        r"already the newest|is already installed|0 newly installed",
    ),
    (
        r"\bbrew\s+install\b",
        r"already installed",
    ),
    (
        r"\bcargo\s+(?:install|add)\b|\bgem\s+install\b"
        r"|\bcomposer\s+require\b|\bnuget\s+install\b"
        r"|\bdotnet\s+add\b.*\bpackage\b|\bcpanm?\b.*\binstall\b",
        None,
    ),
]
_INSTALL_ECOSYSTEMS = [
    (re.compile(cmd, re.IGNORECASE), re.compile(noop, re.IGNORECASE) if noop else None)
    for cmd, noop in _EXT_INSTALL_SPECS
]


def _check_model_installs(cmds: list[tuple[str, str]]) -> str | None:
    for cmd, output in cmds:
        if _LOCAL_INSTALL_RE.search(cmd):
            continue
        for cmd_re, noop_re in _INSTALL_ECOSYSTEMS:
            if not cmd_re.search(cmd):
                continue
            if noop_re and output and noop_re.search(output):
                break
            return cmd.strip()
    return None


@rule
class ModelInstallsDeps(Rule):
    name = "model_installs_deps"
    severity = "fail"
    scope = TaskType.PROJECT_PROPOSAL

    def check(self, ctx):
        cmd = _check_model_installs(ctx.bash_commands)
        if cmd:
            return self.signal(f"Model installed packages during its run: `{_truncate(cmd, 80)}`")
        return None


@rule
class ModelInstallsDepsRanking(Rule):
    name = "model_installs_deps"
    severity = "warn"
    scope = TaskType.CODE_REVIEW

    def check(self, ctx):
        hits = []
        for model in ctx.model_traces:
            td = model.get("_tool_data") or {}
            cmd = _check_model_installs(td.get("bash_commands", []))
            if cmd:
                hits.append(f"{model.get('title', '?')}: `{_truncate(cmd, 60)}`")
        if hits:
            return self.signal("Model installed packages: " + "; ".join(hits))
        return None


@rule
class SparseTrace(Rule):
    name = "sparse_trace"
    severity = "fail"
    scope = TaskType.PROJECT_PROPOSAL

    def check(self, ctx):
        if ctx.tool_call_count == 0:
            return QualitySignal(
                name="empty_trace", severity="quarantine",
                description="Model made zero tool calls (no trace activity)",
            )
        if ctx.tool_call_count < 20:
            return self.signal(
                f"Model made only {ctx.tool_call_count} tool calls"
            )
        return None


@rule
class WebSearchUsed(Rule):
    name = "web_search_used"
    severity = "fail"
    scope = TaskType.PROJECT_PROPOSAL

    def check(self, ctx):
        if ctx.web_search_queries:
            q = _truncate(ctx.web_search_queries[0], 80)
            extra = f" (+{len(ctx.web_search_queries) - 1} more)" if len(ctx.web_search_queries) > 1 else ""
            return self.signal(f"Model used web search: `{q}`{extra}")
        return None


@rule
class WebSearchUsedRanking(Rule):
    name = "web_search_used"
    severity = "warn"
    scope = TaskType.CODE_REVIEW

    def check(self, ctx):
        hits = []
        for model in ctx.model_traces:
            td = model.get("_tool_data") or {}
            queries = td.get("web_search_queries", [])
            if queries:
                q = _truncate(queries[0], 60)
                extra = f" (+{len(queries) - 1} more)" if len(queries) > 1 else ""
                hits.append(f"{model.get('title', '?')}: `{q}`{extra}")
        if hits:
            return self.signal("; ".join(hits))
        return None


@rule
class RushedReview(Rule):
    name = "rushed_review"
    severity = "warn"
    scope = TaskType.CODE_REVIEW

    def check(self, ctx):
        if ctx.session_ms <= 0 or ctx.total_diff_lines < 100:
            return None
        sec_per_100 = (ctx.session_ms / 1000) / (ctx.total_diff_lines / 100)
        if sec_per_100 < 20:
            return self.signal(
                f"{ctx.total_diff_lines} diff lines reviewed in "
                f"{ctx.session_ms / 1000:.0f}s "
                f"({sec_per_100:.1f}s per 100 lines)"
            )
        return None


_LLM_EVIDENCE_RE = re.compile(
    r"As (?:shown|seen|demonstrated|evidenced) (?:by|in)\s*:", re.IGNORECASE,
)
_LLM_RIGID_CMP_RE = re.compile(
    r"(?:is better than|beats|falls below|falls short of|outperforms|surpasses"
    r"|edges out|tops|exceeds|is worse than|is weaker than)"
    r"\s+(?:Model\s+)?[A-C]\s+because",
    re.IGNORECASE,
)
_LLM_MATTERS_RE = re.compile(r"this matters because\b", re.IGNORECASE)
_LLM_MODEL_BECAUSE_RE = re.compile(
    r"Model\s+[A-C]\s+(?:because|since)\b", re.IGNORECASE,
)
_LLM_FORMULA_OPENER_RE = re.compile(
    r"(?:the most important (?:aspects?|things?|points?) (?:to (?:take into account|consider|evaluate|check|look at)|are))"
    r"|(?:For this (?:prompt|task),?\s+(?:the most important|important|key))",
    re.IGNORECASE,
)
_LLM_BEST_WORST_RE = re.compile(
    r"Model [A-C] is (?:the )?(?:best|worst|second[ -]?best)\s+because\b",
    re.IGNORECASE,
)
_LLM_CONTRACTION_RE = re.compile(r"\b\w+'[a-z]+\b", re.IGNORECASE)
_LLM_HEDGE_RE = re.compile(
    r"\b(?:maybe|perhaps|I think|I believe|it seems|seems like|probably"
    r"|might be|not sure|could be|I guess|kinda|sort of|honestly|tbh|imo|imho)\b",
    re.IGNORECASE,
)
_HUMAN_INFORMAL_RE = re.compile(
    r"\b(?:w/o|gonna|wanna|gotta|kinda|sorta|dunno|lemme|gimme"
    r"|btw|fyi|iirc|afaik|fwiw|ymmv|tho|nah|yeah|yep|nope"
    r"|lol|lmao|smh|ngl|lowkey|tbf)\b|(?<!\w)(?:w/|b/c)(?!\w)",
    re.IGNORECASE,
)
_HUMAN_APO_LESS_RE = re.compile(
    r"\b(?:doesnt|dont|cant|wont|shouldnt|wouldnt|couldnt|isnt|wasnt"
    r"|havent|hasnt|didnt|thats|heres|whats|ive|youve|theyve|weve"
    r"|youre|theyre|hes|shes|itll|youll|theyll|youd|theyd)\b",
    re.IGNORECASE,
)
_HUMAN_ELLIPSIS_RE = re.compile(r"\.{2,}")
_HUMAN_STARTER_RE = re.compile(r"(?:^|[.!?]\s+)(?:But|And|So|Also|Plus)\s", re.MULTILINE)
_HUMAN_GRAMMAR_RE = re.compile(
    r"\brather then\b|\bbetter then\b|\bworse then\b"
    r"|\bit don't\b|\bit dont\b|\bhe don't\b|\bshe don't\b"
    r"|\bdo not prevents?\b|\bdo not handles?\b|\bdo not provides?\b"
    r"|\bthe model [A-C] do\b|\bmodel [A-C] do not\b"
    r"|\bin same\b|\bat same\b|\bwith same\b"
    r"|\bthe implementation of [A-C] is\b",
    re.IGNORECASE,
)


def _human_noise_dims(text: str) -> tuple[int, list[str]]:
    dims: list[str] = []
    labels: list[str] = []
    if _HUMAN_INFORMAL_RE.search(text):
        dims.append("informal")
        labels.append("informal language (kinda, tho, btw, ...)")
    apo = len(_HUMAN_APO_LESS_RE.findall(text))
    if apo >= 3:
        dims.append("apo_less")
        labels.append(f"missing apostrophes x{apo} (doesnt, didnt, ...)")
    if text.count("!") >= 2:
        dims.append("exclamations")
        labels.append(f"exclamation marks x{text.count('!')}")
    ellipsis = len(_HUMAN_ELLIPSIS_RE.findall(text))
    if ellipsis >= 2:
        dims.append("ellipsis")
        labels.append(f"trailing dots x{ellipsis}")
    starters = len(_HUMAN_STARTER_RE.findall(text))
    if starters >= 2:
        dims.append("starters")
        labels.append(f"sentences starting with But/And/So x{starters}")
    grammar = len(_HUMAN_GRAMMAR_RE.findall(text))
    if grammar >= 4:
        dims.extend(["grammar", "grammar"])
        labels.append(f"grammar errors x{grammar} (then/than, it don't, ...)")
    elif grammar >= 2:
        dims.append("grammar")
        labels.append(f"grammar errors x{grammar}")
    return len(dims), labels


def _llm_justification_score(text: str) -> tuple[float, list[str], list[str]]:
    words = text.split()
    wc = len(words)
    if wc < 100:
        return 0, []

    score = 0.0
    llm_hits: list[str] = []
    human_hits: list[str] = []

    rigid = len(_LLM_RIGID_CMP_RE.findall(text))
    if rigid >= 4:
        score += 3
        llm_hits.append(f"rigid comparisons x{rigid}")
    elif rigid >= 2:
        score += 2
        llm_hits.append(f"rigid comparisons x{rigid}")

    contractions = len(_LLM_CONTRACTION_RE.findall(text))
    if contractions == 0 and wc >= 200:
        score += 2
        llm_hits.append("zero contractions")
    elif contractions <= 1 and wc >= 300:
        score += 1
        llm_hits.append(f"near-zero contractions ({contractions})")

    evidence = len(_LLM_EVIDENCE_RE.findall(text))
    if evidence >= 3:
        score += 2
        llm_hits.append(f"evidence blocks x{evidence}")
    elif evidence >= 2:
        score += 1
        llm_hits.append(f"evidence blocks x{evidence}")

    has_best = bool(re.search(r"is (?:the )?best\b", text))
    has_second = bool(re.search(
        r"(?:second.?best|second.?place|takes second|comes second|is (?:better|second))",
        text, re.IGNORECASE,
    ))
    has_worst = bool(re.search(
        r"(?:worst|weakest|last place|comes last|takes last)", text, re.IGNORECASE,
    ))
    if has_best and has_second and has_worst:
        score += 1
        llm_hits.append("ranking trifecta")

    hedges = len(_LLM_HEDGE_RE.findall(text))
    if hedges == 0 and wc >= 300:
        score += 1
        llm_hits.append("zero hedges")

    matters = len(_LLM_MATTERS_RE.findall(text))
    if matters >= 2:
        score += 1.5
        llm_hits.append(f'"this matters because" x{matters}')
    elif matters == 1:
        score += 0.5
        llm_hits.append('"this matters because"')

    model_because = len(_LLM_MODEL_BECAUSE_RE.findall(text))
    if model_because >= 4:
        score += 1
        llm_hits.append(f'"Model X because" x{model_because}')

    if _LLM_FORMULA_OPENER_RE.search(text):
        score += 0.5
        llm_hits.append("formulaic opener")

    best_worst = len(_LLM_BEST_WORST_RE.findall(text))
    if best_worst >= 3:
        score += 1
        llm_hits.append(f'"Model X is the best/worst because" x{best_worst}')
    elif best_worst >= 2:
        score += 0.5
        llm_hits.append(f'"Model X is the best/worst because" x{best_worst}')

    backticks = len(re.findall(r"`[^`]+`", text))
    marker_parts = []
    if contractions >= 3:
        marker_parts.append(f"{contractions} contractions")
    if backticks >= 3:
        marker_parts.append(f"{backticks} code refs")
    if contractions >= 5 and backticks >= 5:
        score -= 3
    elif contractions >= 3 and backticks >= 3:
        score -= 2
    elif backticks >= 10:
        score -= 3
    elif contractions >= 3 or backticks >= 5:
        score -= 1.5
    else:
        marker_parts.clear()
    if marker_parts:
        human_hits.append(", ".join(marker_parts))

    ndims, dim_labels = _human_noise_dims(text)
    if ndims >= 4:
        score -= 6
        human_hits.append(", ".join(dim_labels))
    elif ndims >= 3:
        score -= 4
        human_hits.append(", ".join(dim_labels))
    elif ndims == 2:
        score -= 2
        human_hits.append(", ".join(dim_labels))

    return round(max(score, 0), 1), llm_hits, human_hits


@rule
class LlmJustification(Rule):
    name = "llm_justification"
    severity = "warn"
    scope = TaskType.CODE_REVIEW

    def check(self, ctx):
        if not ctx.justification or len(ctx.justification.split()) < 100:
            return None
        score, llm_hits, human_hits = _llm_justification_score(ctx.justification)
        lines = []
        if llm_hits:
            lines.append("  LLM evidence: " + "; ".join(llm_hits))
        if human_hits:
            lines.append("  Reduces confidence: " + "; ".join(human_hits))
        detail = "\n".join(lines)
        if score >= 6:
            return QualitySignal(
                name=self.name, severity="fail",
                description=f"Score {score} — strong LLM signals\n{detail}",
            )
        if score >= 4:
            return self.signal(
                f"Score {score} — possible LLM signals\n{detail}"
            )
        return None


def _level_trajectory(action_history: list[dict]) -> str:
    levels = []
    for a in action_history:
        from_l = a.get("fromLevel")
        to_l = a.get("toLevel")
        if from_l is not None and not levels:
            levels.append(str(from_l))
        if to_l is not None:
            levels.append(str(to_l))
    return " -> ".join(levels) if levels else ""


def _recommend(signals: list[QualitySignal]) -> str:
    if not signals:
        return "no_signal"
    counts = Counter(s.severity for s in signals)
    if counts["quarantine"]:
        return "quarantine"
    if counts["fail"]:
        return "send_back"
    if counts["warn"] >= 3:
        return "send_back"
    return "no_signal"


_TYPE_LABELS = {TaskType.CODE_REVIEW: "ranking", TaskType.PROJECT_PROPOSAL: "proposal"}


def analyze_task(data: dict) -> AnalysisResult:
    ctx = AnalysisContext.build(data)

    signals: list[QualitySignal] = []
    for r in RULES:
        if r.scope is not None and r.scope != ctx.task_type:
            continue
        result = r.check(ctx)
        if isinstance(result, list):
            signals.extend(result)
        elif result:
            signals.append(result)

    return AnalysisResult(
        task_id=data.get("task", {}).get("taskId", ""),
        task_type=_TYPE_LABELS.get(ctx.task_type, "unknown"),
        action=_recommend(signals),
        signals=signals,
        summary=_level_trajectory(ctx.action_history),
    )
