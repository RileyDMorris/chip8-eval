"""Score raw candidate outputs against golden outputs (SPEC Section 7).

    python -m grader.score --suite tests_data/hidden/canonical --outputs outputs.json --out scores.json

Unit test: passes if halted, halt_reason, every state field and final_frame match exactly.
Program test: each checkpoint frame matches exactly or not; pixel accuracy is recorded.
Malformed JSON, a crash, or a timeout fails the test, with the reason recorded.

Scores:
    unit       = unit tests passed / unit tests
    program    = checkpoints matched / checkpoints
    robustness = tests that produced a valid JSON object within the timeout / tests
    overall    = 0.5 * unit + 0.4 * program + 0.1 * robustness
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

STATE_FIELDS = ("v", "i", "pc", "sp", "stack", "dt", "st")
WEIGHTS = {"unit": 0.5, "program": 0.4, "robustness": 0.1}


def parse_output(raw):
    """Return (data, error). ``raw`` is one driver record."""
    if raw is None:
        return None, "missing"
    if raw["status"] == "timeout":
        return None, "timeout"
    if raw["status"] != "exited":
        return None, raw["status"]
    try:
        data = json.loads(raw["stdout"])
    except (json.JSONDecodeError, ValueError):
        if raw.get("returncode"):
            return None, "crash"
        return None, "invalid_json"
    if not isinstance(data, dict):
        return None, "invalid_json"
    return data, None


def is_frame(value):
    return (isinstance(value, list) and len(value) == 32
            and all(isinstance(row, str) and len(row) == 64 for row in value))


def pixel_accuracy(got, want):
    if not is_frame(got):
        return 0.0
    return sum(a == b for g, w in zip(got, want) for a, b in zip(g, w)) / 2048


def unit_mismatches(out, golden):
    problems = []
    for key in ("halted", "halt_reason"):
        if out.get(key) != golden[key]:
            problems.append(key)
    state = out.get("state")
    state = state if isinstance(state, dict) else {}
    for key in STATE_FIELDS:
        if state.get(key) != golden["state"][key]:
            problems.append(f"state.{key}")
    if out.get("final_frame") != golden["final_frame"]:
        problems.append("final_frame")
    return problems


def program_checkpoints(out, golden):
    frames = out.get("frames") if isinstance(out, dict) else None
    frames = frames if isinstance(frames, dict) else {}
    results = []
    for cycle, want in golden["frames"].items():
        got = frames.get(cycle)
        results.append({"cycle": int(cycle), "match": got == want, "accuracy": round(pixel_accuracy(got, want), 4)})
    return results


def output_matches(out, golden, kind):
    if out is None:
        return False
    if kind == "unit":
        return not unit_mismatches(out, golden)
    return all(cp["match"] for cp in program_checkpoints(out, golden))


def _rate(passed, total):
    return round(passed / total, 4) if total else None


def score_suite(suite_dir, outputs):
    """Score ``outputs`` ({test id: driver record}) against the suite's goldens."""
    suite_dir = Path(suite_dir)
    manifest = json.loads((suite_dir / "manifest.json").read_text())
    golden_dir = suite_dir / manifest.get("expected_dir", "golden")
    per_test = []
    family = defaultdict(lambda: [0, 0])
    opcode = defaultdict(lambda: [0, 0])
    unit_passed = unit_total = cp_matched = cp_total = robust = 0
    accuracies = []
    by_program = {}
    change_stats = defaultdict(lambda: {"tests": 0, "passed": 0, "matches_reverted": 0})

    for test in manifest["tests"]:
        golden = json.loads((golden_dir / f"{test['id']}.json").read_text())
        raw = outputs.get(test["id"])
        out, error = parse_output(raw)
        robust += out is not None
        record = {"id": test["id"], "kind": test["kind"], "family": test["family"], "opcode": test["opcode"],
                  "elapsed": raw.get("elapsed") if raw else None}
        if error:
            record["error"] = error
            if raw and raw.get("stderr_tail"):
                record["stderr_tail"] = raw["stderr_tail"][-500:]
        if test["kind"] == "unit":
            unit_total += 1
            mismatches = ["no_output"] if out is None else unit_mismatches(out, golden)
            passed = not mismatches
            unit_passed += passed
            family[test["family"]][1] += 1
            family[test["family"]][0] += passed
            opcode[test["opcode"]][1] += 1
            opcode[test["opcode"]][0] += passed
            record["passed"] = passed
            if mismatches:
                record["mismatches"] = mismatches
        else:
            checkpoints = program_checkpoints(out, golden)
            matched = sum(cp["match"] for cp in checkpoints)
            cp_matched += matched
            cp_total += len(checkpoints)
            accuracies += [cp["accuracy"] for cp in checkpoints]
            record["checkpoints"] = checkpoints
            record["passed"] = matched == len(checkpoints)
            by_program[test["id"]] = {"matched": matched, "total": len(checkpoints)}
        # variant memorization analysis: did a failure match standard CHIP-8 behavior?
        for change in test.get("variant_changes", []):
            stats = change_stats[change]
            stats["tests"] += 1
            stats["passed"] += record["passed"]
            if not record["passed"] and out is not None:
                reverted = json.loads((suite_dir / "golden_revert" / change / f"{test['id']}.json").read_text())
                if output_matches(out, reverted, test["kind"]):
                    stats["matches_reverted"] += 1
                    record.setdefault("matches_reverted", []).append(change)
        per_test.append(record)

    total_tests = len(manifest["tests"])
    unit_score = unit_passed / unit_total if unit_total else 0.0
    program_score = cp_matched / cp_total if cp_total else 0.0
    robustness = robust / total_tests if total_tests else 0.0
    overall = WEIGHTS["unit"] * unit_score + WEIGHTS["program"] * program_score + WEIGHTS["robustness"] * robustness
    scores = {
        "variant": manifest["variant"],
        "split": manifest["split"],
        "overall": round(overall, 4),
        "unit_score": round(unit_score, 4),
        "program_score": round(program_score, 4),
        "robustness": round(robustness, 4),
        "unit": {
            "passed": unit_passed,
            "total": unit_total,
            "by_family": {f: {"passed": p, "total": t, "rate": _rate(p, t)} for f, (p, t) in sorted(family.items())},
            "by_opcode": {o: {"passed": p, "total": t, "rate": _rate(p, t)} for o, (p, t) in sorted(opcode.items())},
        },
        "program": {
            "checkpoints_matched": cp_matched,
            "checkpoints_total": cp_total,
            "mean_pixel_accuracy": round(sum(accuracies) / len(accuracies), 4) if accuracies else None,
            "by_program": by_program,
        },
        "robust_tests": robust,
        "total_tests": total_tests,
        "tests": per_test,
    }
    if change_stats:
        scores["variant_analysis"] = {c: dict(s) for c, s in sorted(change_stats.items())}
    return scores


def main(argv=None):
    parser = argparse.ArgumentParser(description="Score candidate outputs")
    parser.add_argument("--suite", required=True)
    parser.add_argument("--outputs", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    outputs = json.loads(Path(args.outputs).read_text())
    scores = score_suite(args.suite, outputs)
    Path(args.out).write_text(json.dumps(scores, indent=1))
    print(f"overall {scores['overall']:.3f}  unit {scores['unit_score']:.3f}  "
          f"program {scores['program_score']:.3f}  robustness {scores['robustness']:.3f}")


if __name__ == "__main__":
    main()
