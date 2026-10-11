"""Deliberately broken emulators used to validate the grader (SPEC Section 7.1).

Each mutant is the reference emulator bundled into a single ``emulator.py`` with
one bug patched in. Validation asserts:

1. the reference scores 100%;
2. every mutant scores below 100%;
3. each mutant loses points in the opcode family it broke.

    python -m grader.mutants                       # validate both variants (local, fast)
    python -m grader.mutants --variant canonical --docker
"""

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from grader.grade import grade
from reference.bundle import write_bundle

ROOT = Path(__file__).resolve().parent.parent
BOTH = ("canonical", "variant_v")


@dataclass(frozen=True)
class Mutant:
    name: str
    family: str          # the opcode family the bug lives in
    description: str
    patches: tuple
    variants: tuple = BOTH


def swap_lines(first, second, indent=12):
    pad = " " * indent
    return (f"{pad}{first}\n{pad}{second}\n", f"{pad}{second}\n{pad}{first}\n")


MUTANTS = [
    # ---- alu
    Mutant("alu_8xy5_flag_gt", "alu", "8XY5 sets VF with > instead of >=",
           (("v[0xF] = 1 if vx >= vy else 0", "v[0xF] = 1 if vx > vy else 0"),)),
    Mutant("alu_flag_before_result", "alu", "8XY4/5/6/7/E write VF before the result",
           (swap_lines("v[x] = total & 0xFF", "v[0xF] = 1 if total > 0xFF else 0"),
            swap_lines("v[x] = (vx - vy) & 0xFF", "v[0xF] = 1 if vx >= vy else 0"),
            swap_lines("v[x] = vx >> 1", "v[0xF] = vx & 0x1"),
            swap_lines("v[x] = (vy - vx) & 0xFF", "v[0xF] = 1 if vy >= vx else 0"),
            swap_lines("v[x] = (vx << 1) & 0xFF", "v[0xF] = (vx >> 7) & 0x1"))),
    Mutant("alu_shift_uses_vy", "alu", "8XY6/8XYE shift VY into VX (COSMAC quirk)",
           (("v[x] = vx >> 1\n            v[0xF] = vx & 0x1", "v[x] = vy >> 1\n            v[0xF] = vy & 0x1"),
            ("v[x] = (vx << 1) & 0xFF\n            v[0xF] = (vx >> 7) & 0x1",
             "v[x] = (vy << 1) & 0xFF\n            v[0xF] = (vy >> 7) & 0x1"))),
    Mutant("alu_logic_resets_vf", "alu", "8XY1/2/3 reset VF to 0 (COSMAC quirk)",
           (("v[x] = vx | vy\n", "v[x] = vx | vy\n            v[0xF] = 0\n"),
            ("v[x] = vx & vy\n", "v[x] = vx & vy\n            v[0xF] = 0\n"),
            ("v[x] = vx ^ vy\n", "v[x] = vx ^ vy\n            v[0xF] = 0\n"))),
    # ---- display
    Mutant("display_wrap_not_clip", "display", "sprites wrap around the edges instead of clipping",
           (("            if py >= DISPLAY_HEIGHT:\n                break\n", "            py %= DISPLAY_HEIGHT\n"),
            ("                if px >= DISPLAY_WIDTH:\n                    break\n", "                px %= DISPLAY_WIDTH\n"))),
    Mutant("display_no_start_wrap", "display", "DXYN start position is not wrapped (off-screen sprites vanish)",
           (("x0 = vx % DISPLAY_WIDTH\n        y0 = vy % DISPLAY_HEIGHT", "x0 = vx\n        y0 = vy"),)),
    Mutant("display_vf_untouched", "display", "DXYN never writes the collision flag",
           (("        self.v[0xF] = collision\n", "        pass\n"),)),
    Mutant("display_cls_noop", "display", "00E0 does not clear the screen",
           (("                        row[col] = 0\n", "                        pass\n"),)),
    # ---- memory
    Mutant("memory_fx55_increments_i", "memory", "FX55 leaves I = I + X + 1",
           (("                self.write(self.i + k, v[k])\n",
             "                self.write(self.i + k, v[k])\n            self.i = (self.i + x + 1) & 0xFFFF\n"),)),
    Mutant("memory_fx65_increments_i", "memory", "FX65 leaves I = I + X + 1",
           (("                v[k] = self.read(self.i + k)\n",
             "                v[k] = self.read(self.i + k)\n            self.i = (self.i + x + 1) & 0xFFFF\n"),)),
    Mutant("memory_bcd_reversed", "memory", "FX33 stores the digits ones-first",
           (("self.write(self.i, value // 100)", "self.write(self.i + 2, value // 100)"),
            ("self.write(self.i + 2, value % 10)", "self.write(self.i, value % 10)"))),
    Mutant("memory_unmasked", "memory", "memory addresses are not masked to 12 bits",
           (("return self.mem[addr & 0xFFF]", "return self.mem[addr]"),
            ("self.mem[addr & 0xFFF] = value & 0xFF", "self.mem[addr] = value & 0xFF"))),
    # ---- timers
    Mutant("timers_period_off_by_one", "timers", "timers tick every 9 cycles (7 on variant_v)",
           (("if self.cycles % self.config.timer_period == 0:",
             "if self.cycles % (self.config.timer_period - 1) == 0:"),)),
    Mutant("timers_tick_before_execute", "timers", "timers tick at the start of the cycle instead of the end",
           (("            opcode = (self.read(self.pc) << 8) | self.read(self.pc + 1)\n",
             "            if (self.cycles + 1) % self.config.timer_period == 0:\n"
             "                self.dt = max(0, self.dt - 1)\n"
             "                self.st = max(0, self.st - 1)\n"
             "            opcode = (self.read(self.pc) << 8) | self.read(self.pc + 1)\n"),
            ("        if self.cycles % self.config.timer_period == 0:\n"
             "            if self.dt > 0:\n                self.dt -= 1\n"
             "            if self.st > 0:\n                self.st -= 1\n", ""))),
    Mutant("timers_st_frozen", "timers", "the sound timer never counts down",
           (("            if self.st > 0:\n                self.st -= 1\n", ""),)),
    # ---- random
    Mutant("random_ignores_seed", "random", "CXNN ignores --seed (always seed 1)",
           (("        self.rng_state = seed\n", "        self.rng_state = 1\n"),)),
    Mutant("random_low_bits", "random", "rand() returns the low byte of the state",
           (("return (self.rng_state >> 16) & 0xFF", "return self.rng_state & 0xFF"),)),
    # ---- input
    Mutant("input_fx0a_highest_key", "input", "FX0A picks the highest-numbered key",
           (("            for key in range(16):", "            for key in reversed(range(16)):"),)),
    Mutant("input_key_unmasked", "input", "EX9E/EXA1 index keys with VX instead of VX & 0xF",
           (("self.keys[v[x] & 0xF]", "self.keys[v[x]]", 2),)),
    Mutant("input_skip_ops_swapped", "input", "EX9E and EXA1 swapped",
           (("            if nn == 0x9E:", "            if nn == 0xA1:"),
            ("            elif nn == 0xA1:", "            elif nn == 0x9E:"))),
    # ---- flow
    Mutant("flow_call_pushes_old_pc", "flow", "2NNN pushes the address of the call itself",
           (("self.stack.append(self.pc)", "self.stack.append(self.pc - 2)"),)),
    Mutant("flow_bnnn_uses_vx", "flow", "BNNN adds VX instead of V0 (SUPER-CHIP quirk)",
           (("self.pc = nnn + v[0]", "self.pc = nnn + v[x]"),)),
    Mutant("flow_3xnn_inverted", "flow", "3XNN skips on the wrong condition",
           (("            if equal != cfg.skip_swapped:", "            if equal == cfg.skip_swapped:"),)),
    # ---- load
    Mutant("load_7xnn_sets_vf", "load", "7XNN writes a carry flag", (
        ("            if cfg.add_imm_sets_vf:", "            if True:"),), variants=("canonical",)),
    Mutant("load_fx1e_sets_vf", "load", "FX1E sets VF when I passes 0xFFF (Amiga quirk)",
           (("self.i = (self.i + v[x]) & 0xFFFF\n",
             "self.i = (self.i + v[x]) & 0xFFFF\n            v[0xF] = 1 if self.i > 0xFFF else 0\n"),)),
    Mutant("load_i_12_bit", "load", "FX1E wraps I at 12 bits",
           (("self.i = (self.i + v[x]) & 0xFFFF", "self.i = (self.i + v[x]) & 0xFFF"),)),
    Mutant("load_fx29_other_base", "load", "FX29 uses the other machine's font base",
           (("self.i = self.config.font_addr + 5 * (v[x] & 0xF)",
             "self.i = (0x050 - self.config.font_addr) + 5 * (v[x] & 0xF)"),)),
    # ---- halt
    Mutant("halt_invalid_is_noop", "halt", "invalid opcodes are ignored",
           (('raise Halt("invalid_opcode")', "pass", 5),)),
    Mutant("halt_stack_limit_17", "halt", "stack overflow only at 17 entries",
           (("if len(self.stack) >= STACK_LIMIT:", "if len(self.stack) > STACK_LIMIT:"),)),
    Mutant("halt_pc_wraps", "halt", "PC wraps at 0x1000 instead of halting",
           (('            if self.pc >= MEMORY_SIZE:\n                raise Halt("pc_out_of_bounds")\n',
             "            self.pc &= 0xFFF\n"),)),
    Mutant("halt_underflow_ignored", "halt", "00EE with an empty stack is a no-op",
           (('                    raise Halt("stack_underflow")\n', "                    return\n"),)),
    # ---- variant_v only: standard CHIP-8 behavior where the variant differs
    Mutant("revert_font_addr", "load", "variant_v with the font at 0x050 (standard CHIP-8)",
           (("    font_addr=0x000,\n", "    font_addr=0x050,\n"),), variants=("variant_v",)),
    Mutant("revert_skip_swap", "flow", "variant_v with standard 3XNN/4XNN",
           (("    skip_swapped=True,\n", "    skip_swapped=False,\n"),), variants=("variant_v",)),
    Mutant("revert_add_vf", "load", "variant_v with standard 7XNN (VF untouched)",
           (("    add_imm_sets_vf=True,\n", "    add_imm_sets_vf=False,\n"),), variants=("variant_v",)),
    Mutant("revert_timer_period", "timers", "variant_v with timers every 10 cycles",
           (("    timer_period=8,\n", "    timer_period=10,\n"),), variants=("variant_v",)),
    Mutant("revert_mul", "alu", "variant_v without the 5XY1 multiply",
           (("    mul_opcode=True,\n", "    mul_opcode=False,\n"),), variants=("variant_v",)),
]


def mutants_for(variant):
    return [m for m in MUTANTS if variant in m.variants]


def validate(variant, mode="local", out_path=None, log=print):
    """Grade the reference and every mutant; return (report, failures)."""
    rows = []
    failures = []
    with tempfile.TemporaryDirectory(prefix="chip8-mutants-") as tmp:
        reference = write_bundle(Path(tmp) / "reference.py", variant)
        ref_scores = grade(reference, variant, mode=mode)
        if ref_scores["overall"] != 1.0:
            failures.append(f"reference scored {ref_scores['overall']} (expected 1.0)")
        log(f"[{variant}] reference: overall {ref_scores['overall']:.3f}")
        for mutant in mutants_for(variant):
            path = write_bundle(Path(tmp) / f"{mutant.name}.py", variant, mutant.patches)
            scores = grade(path, variant, mode=mode)
            family = scores["unit"]["by_family"].get(mutant.family, {"rate": None})
            row = {
                "name": mutant.name,
                "family": mutant.family,
                "description": mutant.description,
                "overall": scores["overall"],
                "unit_score": scores["unit_score"],
                "program_score": scores["program_score"],
                "robustness": scores["robustness"],
                "family_rate": family["rate"],
                "families_hit": sorted(f for f, s in scores["unit"]["by_family"].items() if s["rate"] < 1.0),
            }
            ok_total = scores["overall"] < 1.0
            ok_family = family["rate"] is not None and family["rate"] < 1.0
            row["detected"] = ok_total and ok_family
            if not ok_total:
                failures.append(f"{mutant.name}: scored 100%")
            if not ok_family:
                failures.append(f"{mutant.name}: did not lose points in family {mutant.family}")
            rows.append(row)
            log(f"[{variant}] {mutant.name:28s} overall {row['overall']:.3f}  {mutant.family} "
                f"{family['rate']}  hit {','.join(row['families_hit'])}")
    report = {"variant": variant, "mode": mode, "reference_overall": ref_scores["overall"],
              "mutants": rows, "failures": failures}
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(json.dumps(report, indent=1))
    return report, failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--variant", choices=list(BOTH) + ["all"], default="all")
    parser.add_argument("--docker", action="store_true", help="grade inside the grading container")
    parser.add_argument("--out-dir", default=str(ROOT / "results" / "grader_validation"))
    args = parser.parse_args(argv)
    variants = BOTH if args.variant == "all" else (args.variant,)
    all_failures = []
    for variant in variants:
        _, failures = validate(variant, mode="docker" if args.docker else "local",
                               out_path=Path(args.out_dir) / f"{variant}.json")
        all_failures += [f"[{variant}] {f}" for f in failures]
    if all_failures:
        print("GRADER VALIDATION FAILED:")
        for failure in all_failures:
            print("  " + failure)
        return 1
    print("grader validation passed: reference = 100%, every mutant < 100% in its family")
    return 0


if __name__ == "__main__":
    sys.exit(main())
