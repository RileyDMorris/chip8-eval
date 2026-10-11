"""Multi-instruction program tests.

Each generator builds a small but real CHIP-8 program (animation, counters,
menus, games) with parameters drawn from ``rng``, plus scripted key events and
a cycle budget. Programs are graded on 3-6 frame checkpoints.

Programs only use semantic mnemonics (``SE``/``SNE`` etc.), so the same
generator produces a logically equivalent program on the variant machine.
Programs never rely on VF surviving ``ADD VX, NN`` (it does not in variant_v).
"""

import random
from dataclasses import dataclass, field

from reference.chip8 import CANONICAL, run
from romgen.builder import B, DT, F, I, K, MEM_I, ST, V, VF, Program
from romgen.builder import V0, V1, V2, V3, V4, V5, V6, V7, V8, V9, VA, VB, VC, VD, VE
from romgen.testcase import TestCase


@dataclass
class ProgramSpec:
    name: str
    description: str
    program: Program
    cycles: int
    keys: list = field(default_factory=list)
    seed: int = 1


class Asm(Program):
    """Program with unique-label helpers for common idioms."""

    def __init__(self, config):
        super().__init__(config)
        self._n = 0

    def fresh(self, prefix):
        self._n += 1
        return f"{prefix}_{self._n}"

    def wait_dt(self, value_reg, tmp_reg):
        """Set DT from a register and busy-wait until it reaches 0."""
        self.LD(DT, value_reg)
        loop = self.label(self.fresh("wait"))
        self.LD(tmp_reg, DT)
        self.SE(tmp_reg, 0)
        self.JP(loop)

    def wait_release(self, key_reg):
        """Loop while the key in key_reg is held down."""
        loop = self.label(self.fresh("release"))
        self.SKNP(key_reg)
        self.JP(loop)


def key_script(rng, keys, start, gap=(40, 160), hold=(6, 40)):
    """Press-and-release events for a sequence of keys."""
    events = []
    t = start
    for key in keys:
        t += rng.randint(*gap)
        held = rng.randint(*hold)
        events.append({"cycle": t, "key": key, "down": True})
        events.append({"cycle": t + held, "key": key, "down": False})
        t += held
    return events, t


# ---------------------------------------------------------------- programs

def prog_bounce(rng, cfg):
    """A 2x2 ball bounces off the screen edges and obstacles (collision via VF)."""
    p = Asm(cfg)
    p.CLS()
    p.LD(I, "block")
    for _ in range(rng.randint(2, 4)):
        p.LD(V0, rng.randrange(8, 52))
        p.LD(V1, rng.randrange(6, 24))
        p.DRW(V0, V1, 4)
    p.LD(V0, rng.randrange(2, 60, 2))
    p.LD(V1, rng.randrange(2, 28, 2))
    p.LD(V2, rng.choice([1, 0xFF]))
    p.LD(V3, rng.choice([1, 0xFF]))
    p.LD(V4, 0)
    p.LD(I, "ball")
    p.DRW(V0, V1, 2)
    p.label("loop")
    p.LD(I, "ball")
    p.DRW(V0, V1, 2)          # erase
    p.ADD(V0, V2)
    p.ADD(V1, V3)
    p.SNE(V0, 0)
    p.LD(V2, 1)
    p.SNE(V0, 62)
    p.LD(V2, 0xFF)
    p.SNE(V1, 0)
    p.LD(V3, 1)
    p.SNE(V1, 30)
    p.LD(V3, 0xFF)
    p.DRW(V0, V1, 2)          # draw
    p.SE(VF, 0)
    p.CALL("hit")
    p.JP("loop")
    p.label("hit")
    p.ADD(V4, 1)
    p.LD(V5, 0)
    p.SUB(V5, V2)
    p.LD(V2, V5)
    p.LD(V5, 0)
    p.SUB(V5, V3)
    p.LD(V3, V5)
    p.RET()
    p.label("ball")
    p.DB(0xC0, 0xC0)
    p.label("block")
    p.DB(0xFF, 0xFF, 0xFF, 0xFF)
    return ProgramSpec("bounce", "ball bouncing off edges and obstacles", p, rng.randint(3000, 6000))


def prog_score_counter(rng, cfg):
    """A 3-digit BCD score counter rendered with the font, paced by DT."""
    p = Asm(cfg)
    x, y = rng.randint(0, 40), rng.randint(0, 26)
    p.LD(V8, rng.randint(0, 255))       # score
    p.LD(V9, rng.randint(1, 9))         # step
    p.LD(VA, rng.randint(3, 6))         # delay
    p.label("loop")
    p.CALL("draw")
    p.wait_dt(VA, VB)
    p.CALL("draw")                      # XOR erase
    p.ADD(V8, V9)
    p.JP("loop")
    p.label("draw")
    p.LD(I, "buf")
    p.LD(B, V8)
    p.LD(V2, MEM_I)
    p.LD(V3, x)
    p.LD(V4, y)
    for digit in (V0, V1, V2):
        p.LD(F, digit)
        p.DRW(V3, V4, 5)
        p.ADD(V3, 5)
    p.RET()
    p.label("buf")
    p.DB(0, 0, 0)
    return ProgramSpec("score_counter", "BCD score counter drawn with font sprites", p, rng.randint(2500, 5000))


def prog_timer_bar(rng, cfg):
    """A progress bar that grows one column per DT countdown, then wraps to a new row."""
    p = Asm(cfg)
    p.LD(V0, 0)
    p.LD(V1, rng.randint(0, 27))
    p.LD(V2, rng.randint(1, 3))     # delay per column
    p.LD(V5, 31)
    p.LD(I, "column")
    p.label("loop")
    p.DRW(V0, V1, 4)
    p.ADD(V0, 1)
    p.LD(ST, V2)
    p.wait_dt(V2, V3)
    p.SNE(V0, 64)
    p.CALL("reset")
    p.JP("loop")
    p.label("reset")
    p.LD(V0, 0)
    p.ADD(V1, 5)
    p.AND(V1, V5)
    p.RET()
    p.label("column")
    p.DB(0x80, 0x80, 0x80, 0x80)
    return ProgramSpec("timer_bar", "timer-paced progress bar", p, rng.randint(4000, 9000))


def prog_key_menu(rng, cfg):
    """A menu of digits with a cursor moved by keys and a select key."""
    p = Asm(cfg)
    n = rng.randint(3, 5)
    k_up, k_down, k_sel = rng.sample(range(16), 3)
    p.CLS()
    for i in range(n):
        p.LD(V0, 10)
        p.LD(V1, 2 + 6 * i)
        p.LD(V2, i + 1)
        p.LD(F, V2)
        p.DRW(V0, V1, 5)
    p.LD(V6, 0)          # selection
    p.LD(V7, 2)          # cursor y
    p.LD(V8, 0xFF)       # shown selection (none)
    p.LD(I, "arrow")
    p.LD(V0, 3)
    p.DRW(V0, V7, 5)
    p.label("main")
    p.LD(V9, k_up)
    p.SKNP(V9)
    p.CALL("up")
    p.LD(V9, k_down)
    p.SKNP(V9)
    p.CALL("down")
    p.LD(V9, k_sel)
    p.SKNP(V9)
    p.CALL("select")
    p.JP("main")

    p.label("up")
    p.SE(V6, 0)
    p.CALL("cursor_up")
    p.CALL("release")
    p.RET()
    p.label("down")
    p.SE(V6, n - 1)
    p.CALL("cursor_down")
    p.CALL("release")
    p.RET()
    p.label("cursor_up")
    p.LD(I, "arrow")
    p.LD(V0, 3)
    p.DRW(V0, V7, 5)
    p.ADD(V6, 0xFF)
    p.ADD(V7, 256 - 6)
    p.DRW(V0, V7, 5)
    p.RET()
    p.label("cursor_down")
    p.LD(I, "arrow")
    p.LD(V0, 3)
    p.DRW(V0, V7, 5)
    p.ADD(V6, 1)
    p.ADD(V7, 6)
    p.DRW(V0, V7, 5)
    p.RET()
    p.label("select")
    p.SE(V8, 0xFF)
    p.CALL("show")              # erase previous
    p.LD(V8, V6)
    p.CALL("show")
    p.CALL("release")
    p.RET()
    p.label("show")
    p.LD(V0, V8)
    p.ADD(V0, 1)
    p.LD(F, V0)
    p.LD(V1, 40)
    p.LD(V2, 12)
    p.DRW(V1, V2, 5)
    p.RET()
    p.label("release")
    p.wait_release(V9)
    p.RET()
    p.label("arrow")
    p.DB(0x20, 0x30, 0xF8, 0x30, 0x20)

    actions = [rng.choice([k_up, k_down, k_down, k_sel]) for _ in range(rng.randint(8, 12))]
    events, t_end = key_script(rng, actions, start=40)
    return ProgramSpec("key_menu", "cursor menu driven by scripted keys", p, t_end + rng.randint(50, 200), keys=events)


def prog_random_dots(rng, cfg):
    """Random dots, then random font glyphs, from a fixed seed."""
    p = Asm(cfg)
    count = rng.randint(60, 200)
    p.LD(I, "dot")
    p.LD(V2, 0)
    p.label("dots")
    p.RND(V0, 0x3F)
    p.RND(V1, 0x1F)
    p.DRW(V0, V1, 1)
    p.ADD(V2, 1)
    p.SE(V2, count)
    p.JP("dots")
    p.label("glyphs")
    p.RND(V3, 0x0F)
    p.LD(F, V3)
    p.RND(V0, 0x3F)
    p.RND(V1, 0x1F)
    p.DRW(V0, V1, 5)
    p.JP("glyphs")
    p.label("dot")
    p.DB(0x80)
    return ProgramSpec("random_dots", "random dots and glyphs from a seeded RNG", p,
                       rng.randint(1500, 3000), seed=rng.randint(2, 2**31 - 1))


def prog_nested_calls(rng, cfg):
    """A grid of glyphs drawn through four levels of subroutines, redrawn per frame."""
    p = Asm(cfg)
    rows, cols = rng.randint(2, 4), rng.randint(4, 8)
    p.LD(VA, rng.randint(0, 15))       # frame counter feeds glyph choice
    p.LD(VC, rng.randint(2, 4))        # delay
    p.label("frame")
    p.CLS()
    p.CALL("grid")
    p.wait_dt(VC, VD)
    p.ADD(VA, 1)
    p.JP("frame")
    p.label("grid")
    p.LD(V1, 1)
    p.LD(V5, rows)
    p.label("grid_loop")
    p.CALL("row")
    p.ADD(V1, 7)
    p.ADD(V5, 0xFF)
    p.SE(V5, 0)
    p.JP("grid_loop")
    p.RET()
    p.label("row")
    p.LD(V0, 2)
    p.LD(V6, cols)
    p.label("row_loop")
    p.CALL("cell")
    p.ADD(V0, 7)
    p.ADD(V6, 0xFF)
    p.SE(V6, 0)
    p.JP("row_loop")
    p.RET()
    p.label("cell")
    p.CALL("glyph")
    p.DRW(V0, V1, 5)
    p.RET()
    p.label("glyph")
    p.LD(V2, V0)
    p.XOR(V2, V1)
    p.ADD(V2, VA)
    p.LD(F, V2)
    p.RET()
    return ProgramSpec("nested_calls", "glyph grid drawn via 4-deep subroutine calls", p, rng.randint(3000, 7000))


def prog_font_grid(rng, cfg):
    """All 16 digits in a grid, re-drawn shifted each pass with XOR overlap."""
    p = Asm(cfg)
    p.LD(V6, rng.randint(0, 3))     # x offset
    p.LD(V7, rng.randint(0, 3))     # y offset
    p.LD(V9, rng.randint(1, 3))     # shift per pass
    p.label("pass")
    p.LD(V2, 0)                     # digit
    p.LD(V0, V6)
    p.LD(V1, V7)
    p.label("digit")
    p.LD(F, V2)
    p.DRW(V0, V1, 5)
    p.ADD(V0, 8)
    p.ADD(V2, 1)
    p.SNE(V2, 8)
    p.CALL("newline")
    p.SE(V2, 16)
    p.JP("digit")
    p.ADD(V6, V9)
    p.ADD(V7, 1)
    p.JP("pass")
    p.label("newline")
    p.LD(V0, V6)
    p.ADD(V1, 7)
    p.RET()
    return ProgramSpec("font_grid", "hex font grid redrawn with shifting offsets", p, rng.randint(600, 1500))


def prog_typing(rng, cfg):
    """Each key press (FX0A) is echoed as its hex digit, like typing."""
    p = Asm(cfg)
    spacing = rng.choice([5, 6])
    per_line = rng.randint(7, 64 // spacing)
    left = rng.randint(0, 64 - spacing * per_line)
    p.CLS()
    p.LD(V1, left)
    p.LD(V2, rng.randint(0, 4))
    p.label("loop")
    p.LD(V0, K)
    p.LD(F, V0)
    p.DRW(V1, V2, 5)
    p.ADD(V1, spacing)
    p.SNE(V1, left + spacing * per_line)
    p.CALL("newline")
    p.wait_release(V0)
    p.JP("loop")
    p.label("newline")
    p.LD(V1, left)
    p.ADD(V2, 6)
    p.RET()
    presses = [rng.randint(0, 15) for _ in range(rng.randint(14, 24))]
    events, t_end = key_script(rng, presses, start=5, gap=(10, 80), hold=(3, 30))
    # one chord: two keys down together; the lower one must be read
    a, b = sorted(rng.sample(range(16), 2))
    t = t_end + rng.randint(20, 60)
    events += [{"cycle": t, "key": b, "down": True}, {"cycle": t, "key": a, "down": True},
               {"cycle": t + 20, "key": a, "down": False}, {"cycle": t + 20, "key": b, "down": False}]
    return ProgramSpec("typing", "FX0A key echo with scripted typing", p, t + rng.randint(60, 200), keys=events)


def prog_scroller(rng, cfg):
    """Sprites scroll off the right and bottom edges (clipped), wrapping their start."""
    p = Asm(cfg)
    p.LD(V0, rng.randint(30, 50))   # x of the horizontal mover
    p.LD(V1, rng.randint(2, 10))
    p.LD(V2, rng.randint(0, 40))    # x of the vertical mover
    p.LD(V3, rng.randint(10, 20))   # y of the vertical mover
    p.LD(V4, 1)
    p.LD(V5, rng.randint(1, 2))     # vertical speed
    p.CALL("draw")
    p.label("loop")
    p.wait_dt(V4, V6)
    p.CALL("draw")                  # erase
    p.ADD(V0, 1)
    p.ADD(V3, V5)
    p.CALL("draw")
    p.JP("loop")
    p.label("draw")
    p.LD(I, "logo")
    p.DRW(V0, V1, 8)
    p.LD(I, "bar")
    p.DRW(V2, V3, 6)
    p.RET()
    p.label("logo")
    p.DB(*[rng.randint(1, 255) for _ in range(8)])
    p.label("bar")
    p.DB(0xFF, 0x81, 0xBD, 0xBD, 0x81, 0xFF)
    return ProgramSpec("scroller", "sprites crossing the right/bottom edges", p, rng.randint(2500, 5000))


def prog_memcopy(rng, cfg):
    """Copy ROM data to a RAM buffer in 4-byte chunks with FX65/FX55, transforming each round."""
    p = Asm(cfg)
    buf = rng.randrange(0x800, 0xE00, 16)
    p.LD(V8, rng.randint(1, 255))   # xor key
    p.LD(VA, 0)                     # x position of the drawing
    p.label("round")
    p.LD(V9, 0)                     # chunk index * 4
    p.label("chunk")
    p.LD(I, "data")
    p.ADD(I, V9)
    p.LD(V3, MEM_I)
    p.XOR(V0, V8)
    p.XOR(V1, V8)
    p.SHL(V2)
    p.SHR(V3)
    p.LD(I, buf)
    p.ADD(I, V9)
    p.LD(MEM_I, V3)
    p.ADD(V9, 4)
    p.SE(V9, 16)
    p.JP("chunk")
    p.LD(I, buf)
    p.LD(V7, VA)
    p.LD(VB, 4)
    p.DRW(V7, VB, 8)
    p.LD(I, buf + 8)
    p.LD(VB, 14)
    p.DRW(V7, VB, 8)
    p.ADD(V8, 37)
    p.ADD(VA, 9)
    p.SE(VA, 63)
    p.JP("round")
    p.CLS()                         # page full: start over with the evolved key
    p.LD(VA, 0)
    p.JP("round")
    p.label("data")
    p.DB(*[rng.randint(0, 255) for _ in range(16)])
    return ProgramSpec("memcopy", "chunked FX65/FX55 copies with transforms, drawn as sprites", p, rng.randint(900, 1600))


def prog_fibonacci(rng, cfg):
    """Fibonacci mod 256 with carry flags, rendered as BCD rows."""
    p = Asm(cfg)
    p.LD(V8, rng.randint(0, 3))     # a
    p.LD(V9, rng.randint(1, 5))     # b
    p.LD(VD, 0)                     # row y
    p.label("step")
    p.LD(VA, V8)
    p.ADD(VA, V9)                   # VF = carry
    p.LD(VC, VF)
    p.LD(V8, V9)
    p.LD(V9, VA)
    p.CALL("show")
    p.ADD(VD, 6)
    p.SNE(VD, 30)
    p.CALL("page")
    p.JP("step")
    p.label("page")
    p.LD(VE, 3)
    p.wait_dt(VE, V7)
    p.CLS()
    p.LD(VD, 0)
    p.RET()
    p.label("show")
    p.LD(I, "buf")
    p.LD(B, V9)
    p.LD(V2, MEM_I)
    p.LD(V3, 0)
    for digit in (V0, V1, V2):
        p.LD(F, digit)
        p.DRW(V3, VD, 5)
        p.ADD(V3, 5)
    p.SE(VC, 1)
    p.RET()
    p.LD(I, "carry")
    p.LD(V3, 20)
    p.DRW(V3, VD, 5)
    p.RET()
    p.label("buf")
    p.DB(0, 0, 0)
    p.label("carry")
    p.DB(0x20, 0x70, 0xF8, 0x70, 0x20)
    return ProgramSpec("fibonacci", "Fibonacci mod 256 shown as BCD with carry markers", p, rng.randint(1500, 3500))


def prog_collision_counter(rng, cfg):
    """Random blocks; every collision reported by VF increments an on-screen counter."""
    p = Asm(cfg)
    p.LD(V8, 0)                     # collisions
    p.CALL("score")
    p.label("loop")
    p.RND(V0, 0x3F)
    p.RND(V1, 0x1F)
    p.LD(I, "block")
    p.DRW(V0, V1, 3)
    p.SE(VF, 1)
    p.JP("loop")
    p.CALL("score")                 # erase
    p.ADD(V8, 1)
    p.CALL("score")
    p.JP("loop")
    p.label("score")
    p.LD(I, "buf")
    p.LD(B, V8)
    p.LD(V2, MEM_I)
    p.LD(V3, 48)
    p.LD(V4, 0)
    for digit in (V0, V1, V2):
        p.LD(F, digit)
        p.DRW(V3, V4, 5)
        p.ADD(V3, 5)
    p.RET()
    p.label("buf")
    p.DB(0, 0, 0)
    p.label("block")
    p.DB(0xE0, 0xA0, 0xE0)
    return ProgramSpec("collision_counter", "random blocks with a VF collision counter", p,
                       rng.randint(1500, 4000), seed=rng.randint(2, 2**31 - 1))


def prog_jump_table(rng, cfg):
    """BNNN dispatch: each step jumps through a table to a handler that draws a glyph."""
    p = Asm(cfg)
    handlers = 8
    p.LD(V5, rng.randint(0, 7))     # index
    p.LD(V6, 0)                     # x
    p.LD(V7, 0)                     # y
    p.LD(V8, rng.randint(1, 7))     # stride
    p.label("loop")
    p.LD(V0, V5)
    p.SHL(V0)                       # 2 bytes per table entry
    p.JP(V0, "table")
    p.label("back")
    p.ADD(V5, V8)
    p.LD(V9, 7)
    p.AND(V5, V9)
    p.ADD(V6, 6)
    p.SNE(V6, 60)
    p.CALL("newline")
    p.JP("loop")
    p.label("newline")
    p.LD(V6, 0)
    p.ADD(V7, 6)
    p.SNE(V7, 30)
    p.LD(V7, 0)
    p.RET()
    p.label("table")
    for h in range(handlers):
        p.JP(f"h{h}")
    glyphs = rng.sample(range(16), handlers)
    for h in range(handlers):
        p.label(f"h{h}")
        p.LD(V1, glyphs[h])
        p.LD(F, V1)
        p.DRW(V6, V7, 5)
        p.JP("back")
    return ProgramSpec("jump_table", "BNNN jump-table dispatch drawing glyphs", p, rng.randint(1200, 3000))


def prog_shift_art(rng, cfg):
    """Sprite rows generated with shifts and XOR, stored with FX55, drawn as columns."""
    p = Asm(cfg)
    buf = rng.randrange(0x900, 0xE00, 32)
    p.LD(V4, rng.randint(1, 255))   # seed byte
    p.LD(V6, 0)                     # column x
    p.label("column")
    p.LD(I, buf)
    p.LD(V5, 0)
    p.LD(V0, V4)
    p.label("gen")
    p.LD(V1, V0)
    p.SHL(V1)                       # VF = top bit
    p.LD(V2, V0)
    p.SHR(V2)
    p.XOR(V1, V2)
    p.LD(V0, V1)
    p.LD(MEM_I, V0)
    p.LD(V3, 1)
    p.ADD(I, V3)
    p.ADD(V5, 1)
    p.SE(V5, 15)
    p.JP("gen")
    p.LD(I, buf)
    p.LD(V7, 1)
    p.DRW(V6, V7, 15)
    p.LD(V7, 16)
    p.DRW(V6, V7, 15)
    p.ADD(V4, 29)
    p.ADD(V6, 8)
    p.SE(V6, 64)
    p.JP("column")
    p.LD(V6, 3)
    p.JP("column")
    return ProgramSpec("shift_art", "patterns generated with 8XY6/8XYE and stored with FX55", p, rng.randint(1500, 3000))


def prog_maze(rng, cfg):
    """10 PRINT style maze: random diagonal tiles across the screen."""
    p = Asm(cfg)
    p.label("start")
    p.LD(V1, 0)
    p.label("row")
    p.LD(V0, 0)
    p.label("cell")
    p.LD(I, "slash")
    p.RND(V2, 1)
    p.SE(V2, 0)
    p.LD(I, "backslash")
    p.DRW(V0, V1, 4)
    p.ADD(V0, 4)
    p.SE(V0, 64)
    p.JP("cell")
    p.ADD(V1, 4)
    p.SE(V1, 32)
    p.JP("row")
    p.JP("start")                   # redraw: XOR produces a new pattern
    p.label("slash")
    p.DB(0x10, 0x20, 0x40, 0x80)
    p.label("backslash")
    p.DB(0x80, 0x40, 0x20, 0x10)
    return ProgramSpec("maze", "random maze of diagonal tiles", p, rng.randint(1500, 4000), seed=rng.randint(2, 2**31 - 1))


def prog_pong(rng, cfg):
    """Two paddles on scripted keys, a ball, scores, and DT pacing."""
    p = Asm(cfg)
    k1u, k1d, k2u, k2d = rng.sample(range(16), 4)
    p.LD(VA, 12)                    # left paddle y
    p.LD(VB, 12)                    # right paddle y
    p.LD(V0, rng.randint(20, 40))   # ball x
    p.LD(V1, rng.randint(5, 25))    # ball y
    p.LD(V2, rng.choice([1, 0xFF]))
    p.LD(V3, rng.choice([1, 0xFF]))
    p.LD(VC, 0)                     # left score
    p.LD(VD, 0)                     # right score
    p.LD(VE, 1)
    p.CALL("paddles")
    p.CALL("scores")
    p.LD(I, "ball")
    p.DRW(V0, V1, 1)
    p.label("loop")
    p.wait_dt(VE, V9)
    p.CALL("paddles")               # erase paddles
    p.LD(V9, k1u)
    p.SKNP(V9)
    p.ADD(VA, 0xFF)
    p.LD(V9, k1d)
    p.SKNP(V9)
    p.ADD(VA, 1)
    p.LD(V9, k2u)
    p.SKNP(V9)
    p.ADD(VB, 0xFF)
    p.LD(V9, k2d)
    p.SKNP(V9)
    p.ADD(VB, 1)
    p.CALL("paddles")
    p.LD(I, "ball")
    p.DRW(V0, V1, 1)                # erase ball
    p.ADD(V0, V2)
    p.ADD(V1, V3)
    p.SNE(V1, 0)
    p.LD(V3, 1)
    p.SNE(V1, 31)
    p.LD(V3, 0xFF)
    p.SNE(V0, 0)
    p.CALL("right_scores")
    p.SNE(V0, 63)
    p.CALL("left_scores")
    p.LD(I, "ball")
    p.DRW(V0, V1, 1)
    p.SE(VF, 0)
    p.CALL("bounce")
    p.JP("loop")
    p.label("bounce")
    p.LD(V9, 0)
    p.SUB(V9, V2)
    p.LD(V2, V9)
    p.RET()
    p.label("right_scores")
    p.CALL("scores")
    p.ADD(VD, 1)
    p.CALL("scores")
    p.LD(V2, 1)
    p.RET()
    p.label("left_scores")
    p.CALL("scores")
    p.ADD(VC, 1)
    p.CALL("scores")
    p.LD(V2, 0xFF)
    p.RET()
    p.label("paddles")
    p.LD(I, "paddle")
    p.LD(V9, 2)
    p.DRW(V9, VA, 6)
    p.LD(V9, 61)
    p.DRW(V9, VB, 6)
    p.RET()
    p.label("scores")
    p.LD(V9, 22)
    p.LD(V8, 1)
    p.LD(F, VC)
    p.DRW(V9, V8, 5)
    p.LD(V9, 38)
    p.LD(F, VD)
    p.DRW(V9, V8, 5)
    p.RET()
    p.label("ball")
    p.DB(0x80)
    p.label("paddle")
    p.DB(0x80, 0x80, 0x80, 0x80, 0x80, 0x80)
    keys = [rng.choice([k1u, k1d, k2u, k2d]) for _ in range(rng.randint(10, 16))]
    events, t_end = key_script(rng, keys, start=30, gap=(30, 200), hold=(20, 120))
    return ProgramSpec("pong", "pong with scripted paddles, scores and DT pacing", p, t_end + rng.randint(100, 400), keys=events)


PROGRAMS = [
    prog_bounce,
    prog_score_counter,
    prog_timer_bar,
    prog_key_menu,
    prog_random_dots,
    prog_nested_calls,
    prog_font_grid,
    prog_typing,
    prog_scroller,
    prog_memcopy,
    prog_fibonacci,
    prog_collision_counter,
    prog_jump_table,
    prog_shift_art,
    prog_maze,
    prog_pong,
]


# ---------------------------------------------------------------- checkpoints

def choose_checkpoints(rng, spec, rom, config, attempts=25):
    """3-6 checkpoints spread over the run; at least two distinct, non-blank frames.

    Candidate sets are re-drawn (deterministically, from ``rng``) until one is
    informative, so a checkpoint rarely lands in a program's erase/redraw gap.
    """
    blank = tuple("0" * 64 for _ in range(32))
    total = spec.cycles
    span_start = max(1, total // 8)
    for _ in range(attempts):
        count = rng.randint(3, 6)
        step = (total - span_start) / count
        points = []
        for k in range(count):
            lo = span_start + int(step * k)
            hi = span_start + int(step * (k + 1)) - 1
            points.append(rng.randint(lo, max(lo, hi)))
        points[-1] = total
        points = sorted(set(points))
        result = run(rom, total, keys=spec.keys, seed=spec.seed, frames_at=points, config=config)
        if result["halted"]:
            raise AssertionError(f"program {spec.name} halted: {result['halt_reason']}")
        frames = [tuple(result["frames"][str(c)]) for c in points]
        nonblank = [f for f in frames if f != blank]
        if len(set(frames)) >= 2 and len(nonblank) >= len(frames) - 1:
            return points
    raise AssertionError(f"program {spec.name}: could not find informative checkpoints")


def generate_program_tests(seed, config=CANONICAL, prefix="", only=None):
    rng = random.Random(seed)
    tests = []
    for generator in PROGRAMS:
        spec = generator(rng, config)
        if only is not None and spec.name not in only:
            continue
        rom = spec.program.assemble()
        points = choose_checkpoints(rng, spec, rom, config)
        tests.append(TestCase(
            id=f"{prefix}program_{spec.name}",
            kind="program",
            family="program",
            opcode="",
            description=spec.description,
            rom=rom,
            cycles=spec.cycles,
            seed=spec.seed,
            keys=sorted(spec.keys, key=lambda e: e["cycle"]),
            frames_at=points,
        ))
    return tests
