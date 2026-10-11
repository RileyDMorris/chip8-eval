"""Self-check: run emulator.py on the public tests and report pass/fail.

    python run_public_tests.py [--emulator emulator.py] [--tests public_tests] [-v]

Comparison rules are the same ones the hidden grader uses:
  * unit tests pass if halted, halt_reason, every state field and final_frame match exactly
    (cycles_run is reported but not graded);
  * program tests are scored per checkpoint frame (exact match), with pixel accuracy shown.
Each run has a 10 second timeout.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TIMEOUT = 10
STATE_FIELDS = ("v", "i", "pc", "sp", "stack", "dt", "st")


def run_emulator(emulator, test, tests_dir):
    cmd = [sys.executable, str(emulator), "--rom", str(tests_dir / test["rom"]), "--cycles", str(test["cycles"])]
    if test.get("keys"):
        cmd += ["--keys", str(tests_dir / test["keys"])]
    if test.get("seed", 1) != 1:
        cmd += ["--seed", str(test["seed"])]
    if test.get("frames_at"):
        cmd += ["--frames-at", ",".join(str(c) for c in test["frames_at"])]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, f"timeout after {TIMEOUT}s"
    if proc.returncode != 0:
        tail = proc.stderr.strip().splitlines()[-3:]
        return None, f"exit code {proc.returncode}: " + " | ".join(tail)
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        return None, f"stdout is not one JSON object ({e})"
    if not isinstance(data, dict):
        return None, "stdout JSON is not an object"
    return data, None


def frame_accuracy(got, want):
    if not (isinstance(got, list) and len(got) == 32 and all(isinstance(r, str) and len(r) == 64 for r in got)):
        return 0.0
    same = sum(a == b for g, w in zip(got, want) for a, b in zip(g, w))
    return same / 2048


def describe_frame_diff(got, want):
    if not (isinstance(got, list) and len(got) == 32):
        return "frame is not a list of 32 rows"
    rows = [y for y in range(32) if got[y] != want[y]]
    return f"{len(rows)} rows differ (first: row {rows[0]})" if rows else "identical"


def check_unit(out, exp):
    problems = []
    for key in ("halted", "halt_reason"):
        if out.get(key) != exp[key]:
            problems.append(f"{key}: got {out.get(key)!r}, expected {exp[key]!r}")
    state = out.get("state") if isinstance(out.get("state"), dict) else {}
    for key in STATE_FIELDS:
        if state.get(key) != exp["state"][key]:
            problems.append(f"state.{key}: got {state.get(key)!r}, expected {exp['state'][key]!r}")
    if out.get("final_frame") != exp["final_frame"]:
        problems.append("final_frame: " + describe_frame_diff(out.get("final_frame"), exp["final_frame"]))
    return problems


def check_program(out, exp):
    results = []
    frames = out.get("frames") if isinstance(out.get("frames"), dict) else {}
    for cycle, want in exp["frames"].items():
        got = frames.get(cycle)
        results.append((cycle, got == want, frame_accuracy(got, want)))
    return results


def main():
    parser = argparse.ArgumentParser(description="Run emulator.py against the public tests")
    parser.add_argument("--emulator", default=str(HERE / "emulator.py"))
    parser.add_argument("--tests", default=str(HERE / "public_tests"))
    parser.add_argument("-v", "--verbose", action="store_true", help="show every mismatch")
    args = parser.parse_args()
    tests_dir = Path(args.tests)
    manifest = json.loads((tests_dir / "manifest.json").read_text())
    expected_dir = tests_dir / manifest.get("expected_dir", "expected")

    unit_pass = unit_total = cp_pass = cp_total = 0
    for test in manifest["tests"]:
        exp = json.loads((expected_dir / f"{test['id']}.json").read_text())
        out, error = run_emulator(args.emulator, test, tests_dir)
        if test["kind"] == "unit":
            unit_total += 1
            problems = [error] if error else check_unit(out, exp)
            ok = not problems
            unit_pass += ok
            print(f"{'PASS' if ok else 'FAIL'}  {test['id']}  ({test['description']})")
            for problem in problems if args.verbose else problems[:3]:
                print(f"      {problem}")
            if out is not None and out.get("cycles_run") != exp["cycles_run"]:
                print(f"      note: cycles_run is {out.get('cycles_run')!r}, expected {exp['cycles_run']!r} (not graded)")
        else:
            n = len(exp["frames"])
            cp_total += n
            if error:
                print(f"FAIL  {test['id']}  0/{n} checkpoints  ({error})")
                continue
            results = check_program(out, exp)
            passed = sum(ok for _, ok, _ in results)
            cp_pass += passed
            print(f"{'PASS' if passed == n else 'FAIL'}  {test['id']}  {passed}/{n} checkpoints  ({test['description']})")
            for cycle, ok, acc in results:
                if not ok:
                    print(f"      cycle {cycle}: frame mismatch, pixel accuracy {acc:.1%}")
    print()
    print(f"unit tests: {unit_pass}/{unit_total} passed; program checkpoints: {cp_pass}/{cp_total} matched")
    return 0 if unit_pass == unit_total and cp_pass == cp_total else 1


if __name__ == "__main__":
    sys.exit(main())
