"""Build a DEMO report from synthetic "models" made of deliberately broken reference emulators.

This exercises make_report.py end to end before any real model has run. The
numbers it produces say nothing about any real model.

    python -m report.demo        # -> results/demo_runs/, report/demo/REPORT.md
"""

import json
import random
import shutil
import tempfile
from pathlib import Path

from grader.grade import grade
from grader.mutants import MUTANTS
from reference.bundle import write_bundle
from report.make_report import build_report

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "results" / "demo_runs"
OUT = ROOT / "report" / "demo" / "REPORT.md"
BY_NAME = {m.name: m for m in MUTANTS}

# model -> variant -> list of runs; each run is (machine the emulator implements, [mutant names])
DEMO_MODELS = {
    "demo-careful": {
        "canonical": [("canonical", []), ("canonical", []), ("canonical", ["timers_tick_before_execute"])],
        "variant_v": [("variant_v", []), ("variant_v", ["revert_timer_period"]), ("variant_v", ["input_fx0a_highest_key"])],
    },
    "demo-memorizer": {
        "canonical": [("canonical", []), ("canonical", ["load_fx1e_sets_vf"]), ("canonical", [])],
        "variant_v": [("canonical", []), ("canonical", []), ("variant_v", ["revert_skip_swap", "revert_font_addr"])],
    },
    "demo-sloppy": {
        "canonical": [("canonical", ["display_wrap_not_clip", "alu_flag_before_result"]),
                      ("canonical", ["memory_fx55_increments_i", "halt_invalid_is_noop"]),
                      ("canonical", ["alu_logic_resets_vf", "display_vf_untouched"])],
        "variant_v": [("variant_v", ["display_wrap_not_clip", "revert_add_vf"]),
                      ("variant_v", ["memory_bcd_reversed", "revert_mul"]),
                      ("variant_v", ["alu_shift_uses_vy", "timers_period_off_by_one"])],
    },
}

INTRO = """> **DEMO — synthetic data.** Every "model" below is the reference emulator with bugs patched in
> (see `report/demo.py`); episode statistics are made up. This page exists to show what the real
> report looks like and to test `report/make_report.py`. Real results go in `report/REPORT.md`."""


def main():
    if RUNS_DIR.exists():
        shutil.rmtree(RUNS_DIR)
    rng = random.Random(8)
    with tempfile.TemporaryDirectory() as tmp:
        for model, variants in DEMO_MODELS.items():
            for variant, runs in variants.items():
                for attempt, (machine, mutants) in enumerate(runs, start=1):
                    patches = tuple(p for name in mutants for p in BY_NAME[name].patches)
                    emulator = write_bundle(Path(tmp) / f"{model}_{variant}_{attempt}.py", machine, patches)
                    run_id = f"{model}__{variant}__r{attempt}"
                    run_dir = RUNS_DIR / run_id
                    scores = grade(emulator, variant, out_dir=run_dir, mode="local")
                    shutil.copyfile(emulator, run_dir / "emulator.py")
                    meta = {
                        "run_id": run_id, "model": {"name": model, "provider": "demo"}, "variant": variant,
                        "attempt": attempt, "demo": {"machine": machine, "mutants": mutants},
                        "episode": {"end_reason": rng.choice(["submitted", "submitted", "max_tool_calls"]),
                                    "tool_calls": rng.randint(30, 150), "wall_clock_s": rng.randint(600, 5000),
                                    "cost_usd": round(rng.uniform(1, 12), 2)},
                        "scores": {k: scores[k] for k in ("overall", "unit_score", "program_score", "robustness")},
                    }
                    (run_dir / "meta.json").write_text(json.dumps(meta, indent=1))
                    print(f"{run_id}: {scores['overall']:.3f}  ({machine} + {', '.join(mutants) or 'no bugs'})")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    build_report(RUNS_DIR, OUT, title="CHIP-8 Eval — DEMO report (synthetic models)", intro=INTRO)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
