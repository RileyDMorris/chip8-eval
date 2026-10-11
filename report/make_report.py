"""Build report/REPORT.md from graded runs: tables, per-family heatmap, memorization gap, frame diffs.

    python -m report.make_report                                   # results/runs -> report/REPORT.md
    python -m report.make_report --runs-dir results/demo_runs --out report/demo/REPORT.md --title "..."
"""

import argparse
import json
import shutil
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from report.png import render_diff, valid_frame

ROOT = Path(__file__).resolve().parent.parent
FAMILIES = ("flow", "alu", "load", "memory", "display", "timers", "input", "random", "halt")
VARIANT_CHANGE_LABELS = {
    "font_addr": "font at 0x000 (FX29 = 5*VX)",
    "skip_swap": "3XNN/4XNN swapped",
    "add_vf": "7XNN sets VF",
    "timer_period": "timers every 8 cycles",
    "mul": "5XY1 multiply",
}


@dataclass
class Run:
    path: Path
    run_id: str
    model: str
    variant: str
    attempt: int
    meta: dict
    scores: dict

    _outputs = None

    def outputs(self):
        if self._outputs is None:
            path = self.path / "outputs.json"
            self._outputs = json.loads(path.read_text()) if path.exists() else {}
        return self._outputs

    def candidate_result(self, test_id):
        record = self.outputs().get(test_id)
        if not record or record.get("status") != "exited":
            return None
        try:
            data = json.loads(record["stdout"])
        except (ValueError, TypeError):
            return None
        return data if isinstance(data, dict) else None


def load_runs(runs_dir, include_smoke=False):
    runs = []
    for d in sorted(Path(runs_dir).iterdir()) if Path(runs_dir).exists() else []:
        if not (d / "scores.json").exists() or not (d / "meta.json").exists():
            continue
        meta = json.loads((d / "meta.json").read_text())
        if meta["model"].get("smoke_only") and not include_smoke:
            continue
        scores = json.loads((d / "scores.json").read_text())
        runs.append(Run(d, meta["run_id"], meta["model"]["name"], meta["variant"], meta.get("attempt", 1), meta, scores))
    return runs


# ----------------------------------------------------------------------------- formatting

def mean(values):
    values = [v for v in values if v is not None]
    return statistics.fmean(values) if values else None


def pct(value, digits=0):
    return "–" if value is None else f"{100 * value:.{digits}f}%"


def spread(values, digits=1):
    values = [v for v in values if v is not None]
    if not values:
        return "–"
    m = statistics.fmean(values)
    if len(values) == 1:
        return f"{100 * m:.{digits}f}"
    return f"{100 * m:.{digits}f} ({100 * min(values):.0f}–{100 * max(values):.0f})"


def heat(value):
    if value is None:
        return "–"
    mark = "🟩" if value >= 0.95 else "🟨" if value >= 0.75 else "🟧" if value >= 0.4 else "🟥"
    return f"{mark} {100 * value:.0f}"


def table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


# ----------------------------------------------------------------------------- sections

def overall_section(variant, by_model):
    rows = []
    for model, runs in by_model.items():
        episodes = [r.meta.get("episode") or {} for r in runs]
        rows.append([
            f"**{model}**", len(runs),
            spread([r.scores["overall"] for r in runs]),
            spread([r.scores["unit_score"] for r in runs]),
            spread([r.scores["program_score"] for r in runs]),
            spread([r.scores["robustness"] for r in runs]),
            f"{sum(e.get('end_reason') == 'submitted' for e in episodes)}/{len(runs)}",
            f"{mean([e.get('tool_calls') for e in episodes]) or 0:.0f}",
            f"{(mean([e.get('wall_clock_s') for e in episodes]) or 0) / 60:.0f} min",
            f"${mean([e.get('cost_usd') for e in episodes]) or 0:.2f}",
        ])
    rows.sort(key=lambda r: -float(r[2].split()[0]) if r[2] != "–" else 0)
    header = ["Model", "Runs", "Overall", "Unit", "Program", "Robustness", "Submitted", "Tool calls", "Time", "Cost/run"]
    return (f"### Overall — `{variant}`\n\nScores are percentages: mean over runs, with (min–max). "
            "Overall = 0.5 × unit + 0.4 × program + 0.1 × robustness.\n\n" + table(header, rows))


def family_section(variant, by_model):
    rows = []
    for model, runs in by_model.items():
        cells = []
        for family in FAMILIES:
            rates = [r.scores["unit"]["by_family"].get(family, {}).get("rate") for r in runs]
            cells.append(heat(mean(rates)))
        cells.append(heat(mean([r.scores["program_score"] for r in runs])))
        rows.append([f"**{model}**", *cells])
    legend = "🟩 ≥ 95% · 🟨 ≥ 75% · 🟧 ≥ 40% · 🟥 < 40% (mean pass rate over runs)"
    return f"### Per-family pass rates — `{variant}`\n\n{legend}\n\n" + table(["Model", *FAMILIES, "programs"], rows)


def opcode_section(variant, by_model):
    opcodes = sorted({op for runs in by_model.values() for r in runs for op in r.scores["unit"]["by_opcode"]})
    rows = []
    for op in opcodes:
        rates = {m: mean([r.scores["unit"]["by_opcode"].get(op, {}).get("rate") for r in runs])
                 for m, runs in by_model.items()}
        if all(v is None or v >= 1.0 for v in rates.values()):
            continue
        rows.append([f"`{op}`", *[heat(rates[m]) for m in by_model]])
    if not rows:
        return f"### Opcode-level failures — `{variant}`\n\nEvery model passed every unit test for every opcode."
    return (f"### Opcode-level failures — `{variant}`\n\nOnly opcodes where some model lost points.\n\n"
            + table(["Opcode", *by_model], rows))


def program_section(variant, by_model):
    programs = sorted({p for runs in by_model.values() for r in runs for p in r.scores["program"]["by_program"]})
    rows = []
    for program in programs:
        cells = []
        for runs in by_model.values():
            rates = []
            for r in runs:
                stats = r.scores["program"]["by_program"].get(program)
                if stats:
                    rates.append(stats["matched"] / stats["total"])
            cells.append(heat(mean(rates)))
        rows.append([f"`{program.removeprefix('program_')}`", *cells])
    return (f"### Program checkpoints — `{variant}`\n\nShare of checkpoint frames matched exactly (mean over runs).\n\n"
            + table(["Program", *by_model], rows))


def errors_section(variant, by_model):
    rows = []
    for model, runs in by_model.items():
        counts = Counter(t.get("error") for r in runs for t in r.scores["tests"] if t.get("error"))
        ends = Counter((r.meta.get("episode") or {}).get("end_reason", "error") for r in runs)
        rows.append([f"**{model}**", ", ".join(f"{k} × {v}" for k, v in sorted(ends.items())),
                     ", ".join(f"{k} × {v}" for k, v in counts.most_common()) or "none"])
    return (f"### How runs ended and test errors — `{variant}`\n\n"
            + table(["Model", "Episode end reasons", "Test errors (all runs)"], rows))


def common_failures_section(variant, by_model, limit=8):
    parts = [f"### Most common unit-test failures — `{variant}`\n"]
    for model, runs in by_model.items():
        failures = Counter()
        fields = defaultdict(Counter)
        for r in runs:
            for t in r.scores["tests"]:
                if t["kind"] == "unit" and not t["passed"]:
                    failures[t["id"]] += 1
                    for field in t.get("mismatches", []):
                        fields[t["id"]][field] += 1
        if not failures:
            parts.append(f"**{model}**: no unit-test failures.\n")
            continue
        rows = [[f"`{tid.removeprefix('unit_')}`", f"{n}/{len(runs)}", ", ".join(f for f, _ in fields[tid].most_common(4))]
                for tid, n in failures.most_common(limit)]
        parts.append(f"**{model}** ({sum(failures.values())} failures over {len(runs)} runs)\n\n"
                     + table(["Test", "Runs failing", "Mismatched fields"], rows) + "\n")
    return "\n".join(parts)


def pick_interesting(runs, variant, per_model=3):
    """Near-misses first (subtle bugs), then a gross failure, distinct tests."""
    candidates = []
    for r in runs:
        for t in r.scores["tests"]:
            if t["kind"] == "program":
                for cp in t.get("checkpoints", []):
                    if not cp["match"]:
                        candidates.append((cp["accuracy"], r, t, cp["cycle"]))
            elif not t["passed"] and "final_frame" in t.get("mismatches", []):
                candidates.append((None, r, t, None))
    near = sorted((c for c in candidates if c[0] is not None and c[0] >= 0.9), key=lambda c: -c[0])
    unit = [c for c in candidates if c[0] is None]
    gross = sorted((c for c in candidates if c[0] is not None and c[0] < 0.9), key=lambda c: c[0])
    chosen, seen = [], set()
    for pool in (near, unit, gross, near[1:]):
        for c in pool:
            if len(chosen) >= per_model:
                break
            if c[2]["id"] in seen:
                continue
            seen.add(c[2]["id"])
            chosen.append(c)
    return chosen


def frames_section(variant, by_model, out_dir, image_prefix):
    parts = [f"### Frame diffs — `{variant}`\n",
             "Each image shows **candidate | reference | diff**. In the diff, red pixels are lit only in the "
             "candidate's frame, green pixels only in the reference's, and grey pixels in both.\n"]
    golden_dir = ROOT / "tests_data" / "hidden" / variant / "golden"
    manifest = {t["id"]: t for t in json.loads((golden_dir.parent / "manifest.json").read_text())["tests"]}
    any_image = False
    for model, runs in by_model.items():
        chosen = pick_interesting(runs, variant)
        if not chosen:
            continue
        parts.append(f"**{model}**\n")
        for accuracy, run, test, cycle in chosen:
            golden = json.loads((golden_dir / f"{test['id']}.json").read_text())
            result = run.candidate_result(test["id"])
            if cycle is not None:
                reference = golden["frames"][str(cycle)]
                candidate = ((result or {}).get("frames") or {}).get(str(cycle))
                where = f"checkpoint at cycle {cycle}, pixel accuracy {pct(accuracy, 1)}"
            else:
                reference = golden["final_frame"]
                candidate = (result or {}).get("final_frame")
                where = "final frame; also mismatched: " + ", ".join(m for m in test.get("mismatches", []) if m != "final_frame")
            name = f"{variant}__{model}__r{run.attempt}__{test['id']}" + (f"__{cycle}" if cycle else "") + ".png"
            render_diff(out_dir / name, candidate, reference)
            any_image = True
            note = "" if valid_frame(candidate) else " (no valid frame in the candidate's output)"
            description = manifest.get(test["id"], {}).get("description", "")
            parts.append(f"- `{test['id']}` — {description}; run {run.attempt}, {where}{note}\n\n"
                         f"  ![{test['id']}]({image_prefix}/{name})\n")
    if not any_image:
        parts.append("No frame mismatches to show.\n")
    return "\n".join(parts)


def memorization_section(groups):
    models = sorted(set(groups.get("canonical", {})) & set(groups.get("variant_v", {})))
    if not models:
        return ("## Memorization gap\n\nNo model has runs on both `canonical` and `variant_v` yet.")
    rows = []
    for model in models:
        c = [r.scores["overall"] for r in groups["canonical"][model]]
        v = [r.scores["overall"] for r in groups["variant_v"][model]]
        cu = [r.scores["unit_score"] for r in groups["canonical"][model]]
        vu = [r.scores["unit_score"] for r in groups["variant_v"][model]]
        rows.append([f"**{model}**", spread(c), spread(v), f"**{100 * (mean(c) - mean(v)):+.1f}**",
                     f"{100 * (mean(cu) - mean(vu)):+.1f}"])
    gap = table(["Model", "Canonical overall", "Variant overall", "Gap (points)", "Unit-score gap"], rows)
    change_rows = []
    for model in models:
        cells = []
        for change in VARIANT_CHANGE_LABELS:
            stats = [r.scores.get("variant_analysis", {}).get(change) for r in groups["variant_v"][model]]
            stats = [s for s in stats if s]
            if not stats:
                cells.append("–")
                continue
            tests = sum(s["tests"] for s in stats)
            passed = sum(s["passed"] for s in stats)
            failed = tests - passed
            reverted = sum(s["explained_by_standard"] for s in stats)
            cell = heat(passed / tests if tests else None)
            if failed:
                cell += f" · {reverted}/{failed} std"
            cells.append(cell)
        change_rows.append([f"**{model}**", *cells])
    changes = table(["Model", *VARIANT_CHANGE_LABELS.values()], change_rows)
    return ("## Memorization gap\n\n"
            "`variant_v` is the canonical machine with five deliberate changes, and the agent is told the spec "
            "overrides prior knowledge. The gap is canonical score minus variant score: how much a model's "
            "result depends on the machine matching standard CHIP-8.\n\n" + gap + "\n\n"
            "### Which changes each model got wrong\n\n"
            "For each change: pass rate on the variant tests whose graded output depends on that change. "
            "`k/n std` means that of `n` failures, `k` are reproduced exactly by the reference emulator with "
            "this change reverted to *standard* CHIP-8 behavior (the grader searches for the smallest set of "
            "reverted changes that reproduces each failure; the failure is attributed to the changes in that "
            "set). That is direct evidence the model followed prior knowledge instead of the spec.\n\n"
            + changes)


def mutants_section():
    parts = ["## Grader validation (mutants)\n",
             "Before any model runs, the grader is checked against deliberately broken copies of the reference "
             "emulator (`grader/mutants.py`). It must score the reference 100%, every mutant below 100%, and "
             "each mutant must lose points in the opcode family it broke.\n"]
    for variant in ("canonical", "variant_v"):
        path = ROOT / "results" / "grader_validation" / f"{variant}.json"
        if not path.exists():
            continue
        report = json.loads(path.read_text())
        status = "passed" if not report["failures"] else "FAILED: " + "; ".join(report["failures"])
        rows = [[f"`{m['name']}`", m["description"], m["family"], pct(m["family_rate"]), pct(m["overall"], 1),
                 ", ".join(m["families_hit"])] for m in report["mutants"]]
        parts.append(f"### `{variant}`: reference {pct(report['reference_overall'])}, "
                     f"{len(report['mutants'])} mutants, validation {status}\n\n"
                     + table(["Mutant", "Bug", "Family", "Family pass rate", "Overall", "Families that lost points"], rows)
                     + "\n")
    return "\n".join(parts)


def runs_section(runs):
    rows = []
    for r in sorted(runs, key=lambda r: (r.variant, r.model, r.attempt)):
        e = r.meta.get("episode") or {}
        rows.append([f"`{r.run_id}`", e.get("end_reason", "error"), e.get("tool_calls", "–"),
                     f"{(e.get('wall_clock_s') or 0) / 60:.0f} min", f"${e.get('cost_usd', 0):.2f}",
                     pct(r.scores["overall"], 1)])
    return "## All runs\n\n" + table(["Run", "End", "Tool calls", "Time", "Cost", "Overall"], rows)


def build_report(runs_dir, out_path, title="CHIP-8 Eval — Results", intro=None, include_smoke=False):
    out_path = Path(out_path)
    frames_dir = out_path.parent / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)
    runs = load_runs(runs_dir, include_smoke=include_smoke)
    groups = defaultdict(lambda: defaultdict(list))
    for r in runs:
        groups[r.variant][r.model].append(r)
    sections = [f"# {title}\n"]
    if intro:
        sections.append(intro + "\n")
    if not runs:
        sections.append("No graded runs found yet. Run `python -m harness.run_eval` (needs API keys in `.env`).\n")
    for variant in ("canonical", "variant_v"):
        by_model = dict(sorted(groups.get(variant, {}).items()))
        if not by_model:
            continue
        sections.append(f"## Results — `{variant}`\n")
        sections.append(overall_section(variant, by_model))
        sections.append(family_section(variant, by_model))
        sections.append(opcode_section(variant, by_model))
        sections.append(program_section(variant, by_model))
        sections.append(errors_section(variant, by_model))
        sections.append(common_failures_section(variant, by_model))
        sections.append(frames_section(variant, by_model, frames_dir, "frames"))
    if runs:
        sections.append(memorization_section(groups))
    sections.append(mutants_section())
    if runs:
        sections.append(runs_section(runs))
    out_path.write_text("\n\n".join(s.rstrip() for s in sections) + "\n")
    return out_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs-dir", default=str(ROOT / "results" / "runs"))
    parser.add_argument("--out", default=str(ROOT / "report" / "REPORT.md"))
    parser.add_argument("--title", default="CHIP-8 Eval — Results")
    parser.add_argument("--intro-file", help="markdown file inserted under the title")
    parser.add_argument("--include-smoke", action="store_true")
    args = parser.parse_args(argv)
    intro = Path(args.intro_file).read_text() if args.intro_file else None
    path = build_report(args.runs_dir, args.out, args.title, intro, args.include_smoke)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
