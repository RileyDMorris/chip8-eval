"""Generate hidden and public test suites plus the agent task directories.

    python -m romgen.generate                 # all variants
    python -m romgen.generate --variant canonical

Writes:
    tests_data/hidden/<variant>/   full grading set + golden outputs (never shown to agents)
    tests_data/public/<variant>/   6 unit + 2 program tests with expected outputs
    tasks/<variant>/               prompt.md (hand-written), machine_spec.md, public_tests/, run_public_tests.py

Hidden and public suites come from different seeds, and the script asserts no
public test duplicates a hidden test.
"""

import argparse
import random
import shutil
from pathlib import Path

from reference.chip8 import CONFIGS
from romgen.programs import generate_program_tests
from romgen.testcase import write_suite
from romgen.unit_tests import generate_unit_tests

ROOT = Path(__file__).resolve().parent.parent

SEEDS = {
    # variant: (hidden seed, public seed)
    "canonical": (0xC8_0001, 0xC8_9001),
    "variant_v": (0xC8_0002, 0xC8_9002),
}
PUBLIC_UNIT_FAMILIES = ("flow", "alu", "memory", "display", "timers", "input")
PUBLIC_PROGRAMS = ("score_counter", "typing")
MACHINE_SPECS = {"canonical": "canonical.md", "variant_v": "variant_v.md"}


def build_hidden(variant):
    hidden_seed, _ = SEEDS[variant]
    config = CONFIGS[variant]
    return generate_unit_tests(hidden_seed, config) + generate_program_tests(hidden_seed + 1, config)


def build_public(variant):
    _, public_seed = SEEDS[variant]
    config = CONFIGS[variant]
    rng = random.Random(public_seed)
    units = generate_unit_tests(public_seed, config, prefix="public_")
    chosen = []
    for family in PUBLIC_UNIT_FAMILIES:
        chosen.append(rng.choice([t for t in units if t.family == family]))
    programs = generate_program_tests(public_seed + 1, config, prefix="public_", only=PUBLIC_PROGRAMS)
    return chosen + programs


def check_no_duplicates(hidden, public):
    hidden_args = {t.run_args() for t in hidden}
    hidden_roms = {t.rom for t in hidden}
    for t in public:
        if t.run_args() in hidden_args or t.rom in hidden_roms:
            raise AssertionError(f"public test {t.id} duplicates a hidden test")


def build_variant(variant, out_root=ROOT):
    hidden = build_hidden(variant)
    public = build_public(variant)
    check_no_duplicates(hidden, public)

    hidden_dir = out_root / "tests_data" / "hidden" / variant
    public_dir = out_root / "tests_data" / "public" / variant
    task_dir = out_root / "tasks" / variant
    for d in (hidden_dir, public_dir, task_dir / "public_tests"):
        if d.exists():
            shutil.rmtree(d)
    write_suite(hidden_dir, hidden, variant, "hidden")
    write_suite(public_dir, public, variant, "public", golden_dir="expected", with_reverts=False)

    shutil.copytree(public_dir, task_dir / "public_tests")
    shutil.copyfile(ROOT / "machine" / MACHINE_SPECS[variant], task_dir / "machine_spec.md")
    shutil.copyfile(ROOT / "tasks" / "_shared" / "run_public_tests.py", task_dir / "run_public_tests.py")
    return hidden, public


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--variant", choices=sorted(SEEDS) + ["all"], default="all")
    args = parser.parse_args(argv)
    variants = sorted(SEEDS) if args.variant == "all" else [args.variant]
    for variant in variants:
        hidden, public = build_variant(variant)
        units = sum(t.kind == "unit" for t in hidden)
        programs = sum(t.kind == "program" for t in hidden)
        checkpoints = sum(len(t.frames_at) for t in hidden if t.kind == "program")
        print(f"{variant}: hidden {units} unit + {programs} program tests ({checkpoints} checkpoints); "
              f"public {len(public)} tests")


if __name__ == "__main__":
    main()
