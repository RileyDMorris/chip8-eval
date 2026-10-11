"""Grade one candidate emulator end to end: run it on a suite, score it, write results.

    python -m grader.grade --emulator path/emulator.py --variant canonical --out-dir results/runs/<run_id> [--local]

Writes ``outputs.json`` (raw candidate outputs) and ``scores.json`` into the out dir.
"""

import argparse
import json
from pathlib import Path

from grader.run_candidate import run_candidate
from grader.score import score_suite

ROOT = Path(__file__).resolve().parent.parent


def suite_dir(variant, split="hidden"):
    return ROOT / "tests_data" / split / variant


def grade(emulator, variant, out_dir=None, mode="docker", split="hidden"):
    suite = suite_dir(variant, split)
    emulator = Path(emulator)
    if not emulator.is_file():
        outputs = {}
    else:
        outputs = run_candidate(emulator, suite, mode=mode)
    scores = score_suite(suite, outputs)
    scores["emulator_present"] = emulator.is_file()
    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "outputs.json").write_text(json.dumps(outputs))
        (out_dir / "scores.json").write_text(json.dumps(scores, indent=1))
    return scores


def summary_line(scores):
    return (f"overall {scores['overall']:.3f} | unit {scores['unit']['passed']}/{scores['unit']['total']} "
            f"| program {scores['program']['checkpoints_matched']}/{scores['program']['checkpoints_total']} "
            f"| robust {scores['robust_tests']}/{scores['total_tests']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--emulator", required=True)
    parser.add_argument("--variant", default="canonical")
    parser.add_argument("--split", default="hidden", choices=["hidden", "public"])
    parser.add_argument("--out-dir")
    parser.add_argument("--local", action="store_true", help="run on the host (trusted code only)")
    args = parser.parse_args(argv)
    scores = grade(args.emulator, args.variant, args.out_dir, mode="local" if args.local else "docker",
                   split=args.split)
    print(summary_line(scores))
    for family, stats in scores["unit"]["by_family"].items():
        print(f"  {family:8s} {stats['passed']:3d}/{stats['total']:3d}")


if __name__ == "__main__":
    main()
