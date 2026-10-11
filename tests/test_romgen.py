"""Checks for the ROM builder and the generated test suites (milestone M1)."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from reference.chip8 import CANONICAL, VARIANT_CHANGES, VARIANT_V
from romgen import generate
from romgen.builder import B, DT, F, I, K, MEM_I, ST, V0, V1, V2, VF, AsmError, Program
from romgen.testcase import load_suite

ROOT = Path(__file__).resolve().parent.parent
VARIANTS = ("canonical", "variant_v")


def words(program):
    rom = program.assemble()
    return [int.from_bytes(rom[i:i + 2], "big") for i in range(0, len(rom), 2)]


# ---------------------------------------------------------------- builder

def test_builder_encodings():
    p = Program()
    p.CLS(); p.RET(); p.SYS(0x123)
    p.JP(0x345); p.JP(V0, 0x300); p.CALL(0x456)
    p.SE(V1, 0x12); p.SNE(V1, 0x12); p.SE(V1, V2); p.SNE(V1, V2)
    p.LD(V1, 0x12); p.ADD(V1, 0x12); p.LD(V1, V2)
    p.OR(V1, V2); p.AND(V1, V2); p.XOR(V1, V2); p.ADD(V1, V2); p.SUB(V1, V2)
    p.SHR(V1); p.SUBN(V1, V2); p.SHL(V1)
    p.LD(I, 0xABC); p.RND(V1, 0x0F); p.DRW(V1, V2, 5); p.SKP(V1); p.SKNP(V1)
    p.LD(V1, DT); p.LD(V1, K); p.LD(DT, V1); p.LD(ST, V1); p.ADD(I, V1)
    p.LD(F, V1); p.LD(B, V1); p.LD(MEM_I, V1); p.LD(V1, MEM_I)
    assert words(p) == [
        0x00E0, 0x00EE, 0x0123,
        0x1345, 0xB300, 0x2456,
        0x3112, 0x4112, 0x5120, 0x9120,
        0x6112, 0x7112, 0x8120,
        0x8121, 0x8122, 0x8123, 0x8124, 0x8125,
        0x8106, 0x8127, 0x810E,
        0xAABC, 0xC10F, 0xD125, 0xE19E, 0xE1A1,
        0xF107, 0xF10A, 0xF115, 0xF118, 0xF11E,
        0xF129, 0xF133, 0xF155, 0xF165,
    ]


def test_builder_labels_and_data():
    p = Program()
    p.JP("later")
    p.LD(I, "data")
    p.label("later")
    p.SPIN()
    p.label("data")
    p.DB(1, 2, 3)
    assert p.assemble() == bytes([0x12, 0x04, 0xA2, 0x06, 0x12, 0x04, 1, 2, 3])


def test_builder_variant_semantic_skips():
    p = Program(VARIANT_V)
    p.SE(V1, 7)
    p.SNE(V1, 7)
    p.MUL(V1, V2)
    assert words(p) == [0x4107, 0x3107, 0x5121]


def test_builder_rejects_bad_operands():
    with pytest.raises(AsmError):
        Program().MUL(V1, V2)
    with pytest.raises(AsmError):
        Program().LD(V1, 0x100)
    with pytest.raises(AsmError):
        Program().SYS(0x0E0)
    p = Program()
    p.JP("nowhere")
    with pytest.raises(AsmError):
        p.assemble()


# ---------------------------------------------------------------- suites

def test_generation_is_deterministic_and_committed(tmp_path):
    """Regenerating from the fixed seeds reproduces the committed files byte for byte."""
    (tmp_path / "machine").mkdir()
    (tmp_path / "tasks").mkdir()
    for variant in VARIANTS:
        generate.build_variant(variant, out_root=tmp_path)
    for sub in ("tests_data", "tasks/canonical/public_tests", "tasks/variant_v/public_tests"):
        fresh = sorted(p.relative_to(tmp_path) for p in (tmp_path / sub).rglob("*") if p.is_file())
        committed = sorted(p.relative_to(ROOT) for p in (ROOT / sub).rglob("*") if p.is_file())
        assert fresh == committed, f"file list differs under {sub}"
        for rel in fresh:
            assert (tmp_path / rel).read_bytes() == (ROOT / rel).read_bytes(), f"{rel} differs"


@pytest.mark.parametrize("variant", VARIANTS)
def test_suite_sizes_and_coverage(variant):
    _, tests = load_suite(ROOT / "tests_data" / "hidden" / variant)
    units = [t for t in tests if t["kind"] == "unit"]
    programs = [t for t in tests if t["kind"] == "program"]
    assert 95 <= len(units) <= 130
    assert 15 <= len(programs) <= 20
    families = {t["family"] for t in units}
    assert families == {"flow", "alu", "load", "memory", "display", "timers", "input", "random", "halt"}
    opcodes = {t["opcode"] for t in units}
    for op in ("0NNN", "00E0", "00EE", "1NNN", "2NNN", "3XNN", "4XNN", "5XY0", "6XNN", "7XNN",
               "8XY0", "8XY1", "8XY2", "8XY3", "8XY4", "8XY5", "8XY6", "8XY7", "8XYE", "9XY0",
               "ANNN", "BNNN", "CXNN", "DXYN", "EX9E", "EXA1", "FX07", "FX0A", "FX15", "FX18",
               "FX1E", "FX29", "FX33", "FX55", "FX65"):
        assert op in opcodes, f"no unit test for {op}"
    for t in programs:
        assert 3 <= len(t["frames_at"]) <= 6
    halts = {json.loads((ROOT / "tests_data" / "hidden" / variant / "golden" / f"{t['id']}.json").read_text())["halt_reason"]
             for t in units}
    assert {"invalid_opcode", "stack_overflow", "stack_underflow", "pc_out_of_bounds"} <= halts


def test_variant_changes_are_all_exercised():
    _, tests = load_suite(ROOT / "tests_data" / "hidden" / "variant_v")
    for change in VARIANT_CHANGES:
        assert sum(change in t["variant_changes"] for t in tests) >= 3, change


@pytest.mark.parametrize("variant", VARIANTS)
def test_public_tests_do_not_duplicate_hidden(variant):
    _, hidden = load_suite(ROOT / "tests_data" / "hidden" / variant)
    _, public = load_suite(ROOT / "tests_data" / "public" / variant)
    assert len(public) == 8
    hidden_roms = {t["rom_bytes"] for t in hidden}
    for t in public:
        assert t["rom_bytes"] not in hidden_roms, t["id"]


@pytest.mark.parametrize("variant", VARIANTS)
def test_every_hidden_test_passes_against_reference_cli(variant):
    """Run the reference CLI as a black box on each hidden test and compare to golden."""
    suite = ROOT / "tests_data" / "hidden" / variant
    _, tests = load_suite(suite)

    def check(test):
        cmd = [sys.executable, "-m", "reference.cli", "--variant", variant,
               "--rom", str(suite / test["rom"]), "--cycles", str(test["cycles"]), "--seed", str(test["seed"])]
        if test["keys"]:
            cmd += ["--keys", str(suite / test["keys"])]
        if test["frames_at"]:
            cmd += ["--frames-at", ",".join(map(str, test["frames_at"]))]
        out = subprocess.run(cmd, cwd=ROOT, capture_output=True, check=True).stdout
        golden = json.loads((suite / "golden" / f"{test['id']}.json").read_text())
        return test["id"], json.loads(out) == golden

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(check, tests))
    failed = [tid for tid, ok in results if not ok]
    assert not failed


def test_public_runner_passes_with_reference(tmp_path):
    from reference.bundle import write_bundle

    for variant in VARIANTS:
        emu = write_bundle(tmp_path / f"emu_{variant}.py", variant)
        proc = subprocess.run([sys.executable, str(ROOT / "tasks" / variant / "run_public_tests.py"),
                               "--emulator", str(emu)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stdout
        assert "unit tests: 6/6 passed" in proc.stdout
