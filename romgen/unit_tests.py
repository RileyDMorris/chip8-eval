"""Per-opcode unit tests.

Each generator returns 2-4 small programs for one opcode. A program usually
loads every register with a random value (so "VF unchanged" bugs are visible),
runs the instruction under test, and ends in a tight loop at label ``end``.
Tests are graded on final ``state`` plus ``final_frame``.

All randomness comes from the ``random.Random`` passed in, so a suite is fully
determined by its seed; hidden and public suites use different seeds.
"""

import random
from dataclasses import dataclass, field

from reference.chip8 import CANONICAL
from romgen.builder import B, DT, F, I, K, MEM_I, ST, V, V0, VF, Program
from romgen.testcase import TestCase, cycles_until_pc


@dataclass
class Spec:
    name: str
    description: str
    program: Program
    keys: list = field(default_factory=list)
    seed: int = 1
    halts: bool = False


# ---------------------------------------------------------------- helpers

def prologue(p, rng, fixed=None, i="random"):
    """Load V0..VF with random values (VF never 0/1 so flag writes show up)."""
    fixed = fixed or {}
    values = {}
    for r in range(16):
        if r in fixed:
            value = fixed[r]
        elif r == 0xF:
            value = rng.randint(2, 0xFF)
        else:
            value = rng.randint(0, 0xFF)
        values[r] = value
        p.LD(V[r], value)
    if i == "random":
        p.LD(I, rng.randrange(0x300, 0xF00))
    elif i is not None:
        p.LD(I, i)
    return values


def end(p):
    p.label("end")
    p.SPIN()


def pick_regs(rng, count, exclude=(0xF,)):
    return rng.sample([r for r in range(16) if r not in exclude], count)


def markers(p, rng, regs, values):
    """Emit LD reg, marker for each reg, always changing the register's value."""
    for r in regs:
        values[r] = (values[r] + rng.randint(1, 0xFF)) & 0xFF
        p.LD(V[r], values[r])


def filler(p, count, reg):
    """Instructions with no effect (VX = VX)."""
    for _ in range(count):
        p.LD(V[reg], V[reg])


def new(cfg):
    return Program(cfg)


# ---------------------------------------------------------------- flow

def gen_0nnn(rng, cfg):
    out = []
    p = new(cfg)
    prologue(p, rng)
    nnn = rng.choice([n for n in range(0x001, 0x1000) if n not in (0x0E0, 0x0EE)])
    p.SYS(nnn)
    end(p)
    out.append(Spec("sys_random", f"SYS {nnn:03X} is a no-op", p))
    p = new(cfg)
    prologue(p, rng)
    for _ in range(3):
        p.W(0x0000)
    end(p)
    out.append(Spec("sys_zero", "0000 words execute as no-ops", p))
    return out


def gen_1nnn(rng, cfg):
    out = []
    p = new(cfg)
    values = prologue(p, rng)
    a, b = pick_regs(rng, 2)
    p.JP("target")
    markers(p, rng, [a, a, b], dict(values))
    p.label("target")
    markers(p, rng, [b], values)
    end(p)
    out.append(Spec("forward", "jump forward over instructions", p))

    p = new(cfg)
    prologue(p, rng)
    counter, step = pick_regs(rng, 2)
    n = rng.randint(3, 12)
    p.LD(V[counter], 0)
    p.label("loop")
    p.ADD(V[counter], 1)
    p.SE(V[counter], n)
    p.JP("loop")
    end(p)
    out.append(Spec("backward_loop", f"backward jump loop runs {n} times", p))
    return out


def gen_2nnn(rng, cfg):
    out = []
    p = new(cfg)
    values = prologue(p, rng)
    a, b = pick_regs(rng, 2)
    p.CALL("sub")
    markers(p, rng, [a], dict(values))
    p.label("sub")
    markers(p, rng, [b], values)
    end(p)
    out.append(Spec("call_pushes_pc", "call pushes the address after the call", p))

    p = new(cfg)
    prologue(p, rng)
    depth = rng.randint(3, 9)
    for level in range(depth):
        p.CALL(f"level{level}")
        p.SPIN()
        p.label(f"level{level}")
    end(p)
    out.append(Spec("nested", f"{depth} nested calls; stack listed bottom to top", p))
    return out


def gen_00ee(rng, cfg):
    out = []
    p = new(cfg)
    values = prologue(p, rng)
    a, b = pick_regs(rng, 2)
    p.CALL("sub")
    markers(p, rng, [a], values)
    p.JP("end")
    p.label("sub")
    markers(p, rng, [b], values)
    p.RET()
    end(p)
    out.append(Spec("call_return", "call then return continues after the call", p))

    p = new(cfg)
    prologue(p, rng)
    depth = rng.randint(3, 8)
    acc = pick_regs(rng, 1)[0]
    p.LD(V[acc], 0)
    p.CALL("level0")
    p.JP("end")
    for level in range(depth):
        p.label(f"level{level}")
        if level + 1 < depth:
            p.CALL(f"level{level + 1}")
        p.ADD(V[acc], level + 1)
        p.RET()
    end(p)
    out.append(Spec("nested_return", f"{depth}-deep call chain fully unwinds", p))
    return out


def gen_bnnn(rng, cfg):
    out = []
    for name in ("table_a", "table_b"):
        p = new(cfg)
        handlers = rng.randint(3, 7)
        choice = rng.randrange(handlers)
        prologue(p, rng, fixed={0: 2 * choice})
        mark = pick_regs(rng, 1, exclude=(0, 0xF))[0]
        p.JP(V0, "table")
        p.label("table")
        for h in range(handlers):
            p.JP(f"handler{h}")
        for h in range(handlers):
            p.label(f"handler{h}")
            p.LD(V[mark], 0x10 + h)
            p.JP("end")
        end(p)
        out.append(Spec(name, f"jump table: V0 = {2 * choice} selects handler {choice} of {handlers}", p))
    return out


def _skip_spec(rng, cfg, name, desc, fixed, word):
    p = new(cfg)
    values = prologue(p, rng, fixed=fixed)
    regs = [r for r in range(15) if r not in fixed]
    a, b = rng.sample(regs, 2)
    p.W(word)
    markers(p, rng, [a], values)
    markers(p, rng, [b], values)
    end(p)
    return Spec(name, desc, p)


def gen_3xnn(rng, cfg, high=0x3):
    op = f"{high:X}XNN"
    x = pick_regs(rng, 1)[0]
    nn = rng.randint(0, 0xFF)
    other = (nn + rng.randint(1, 0xFF)) & 0xFF
    vf = rng.randint(2, 0xFF)
    return [
        _skip_spec(rng, cfg, "equal", f"{op} with VX == NN", {x: nn}, high << 12 | x << 8 | nn),
        _skip_spec(rng, cfg, "not_equal", f"{op} with VX != NN", {x: other}, high << 12 | x << 8 | nn),
        _skip_spec(rng, cfg, "vf", f"{op} comparing VF", {0xF: vf}, high << 12 | 0xF << 8 | vf),
    ]


def gen_4xnn(rng, cfg):
    return gen_3xnn(rng, cfg, high=0x4)


def gen_5xy0(rng, cfg, high=0x5):
    op = f"{high:X}XY0"
    x, y = pick_regs(rng, 2)
    v = rng.randint(0, 0xFF)
    w = (v + rng.randint(1, 0xFF)) & 0xFF
    z = pick_regs(rng, 1)[0]
    return [
        _skip_spec(rng, cfg, "equal", f"{op} with VX == VY", {x: v, y: v}, high << 12 | x << 8 | y << 4),
        _skip_spec(rng, cfg, "not_equal", f"{op} with VX != VY", {x: v, y: w}, high << 12 | x << 8 | y << 4),
        _skip_spec(rng, cfg, "same_register", f"{op} with X == Y", {z: v}, high << 12 | z << 8 | z << 4),
    ]


def gen_9xy0(rng, cfg):
    return gen_5xy0(rng, cfg, high=0x9)


# ---------------------------------------------------------------- alu

def _alu_spec(rng, cfg, n, name, desc, x, y, vx, vy):
    p = new(cfg)
    prologue(p, rng, fixed={x: vx, y: vy})
    p.W(0x8000 | x << 8 | y << 4 | n)
    end(p)
    return Spec(name, desc, p)


def _xy(rng):
    return pick_regs(rng, 2)


def gen_8xy0(rng, cfg):
    x, y = _xy(rng)
    y2 = pick_regs(rng, 1)[0]
    return [
        _alu_spec(rng, cfg, 0x0, "copy", "VX = VY", x, y, rng.randint(0, 255), rng.randint(0, 255)),
        _alu_spec(rng, cfg, 0x0, "x_is_f", "VF = VY", 0xF, y2, rng.randint(2, 255), rng.randint(0, 255)),
    ]


def _logic(n, label):
    def gen(rng, cfg):
        x, y = _xy(rng)
        y2 = pick_regs(rng, 1)[0]
        return [
            _alu_spec(rng, cfg, n, "random", f"{label} leaves VF unchanged", x, y, rng.randint(0, 255), rng.randint(0, 255)),
            _alu_spec(rng, cfg, n, "x_is_f", f"{label} into VF", 0xF, y2, rng.randint(2, 255), rng.randint(0, 255)),
        ]
    return gen


gen_8xy1 = _logic(0x1, "OR")
gen_8xy2 = _logic(0x2, "AND")
gen_8xy3 = _logic(0x3, "XOR")


def _pair(rng, pred):
    while True:
        a, b = rng.randint(0, 255), rng.randint(0, 255)
        if pred(a, b):
            return a, b


def gen_8xy4(rng, cfg):
    x, y = _xy(rng)
    a, b = _pair(rng, lambda a, b: a + b <= 0xFF)
    c, d = _pair(rng, lambda a, b: a + b > 0xFF)
    e, f = _pair(rng, lambda a, b: ((a + b) & 0xFF) != (1 if a + b > 0xFF else 0) and a >= 2)
    y2 = pick_regs(rng, 1)[0]
    x3 = pick_regs(rng, 1)[0]
    g, h = _pair(rng, lambda a, b: b >= 2)
    return [
        _alu_spec(rng, cfg, 0x4, "no_carry", "add without carry -> VF = 0", x, y, a, b),
        _alu_spec(rng, cfg, 0x4, "carry", "add with carry -> VF = 1", x, y, c, d),
        _alu_spec(rng, cfg, 0x4, "x_is_f", "X = F: carry flag overwrites the sum", 0xF, y2, e, f),
        _alu_spec(rng, cfg, 0x4, "y_is_f", "Y = F: adds the old VF", x3, 0xF, g, h),
    ]


def gen_8xy5(rng, cfg):
    x, y = _xy(rng)
    a, b = _pair(rng, lambda a, b: a > b)
    c = rng.randint(0, 255)
    e, f = _pair(rng, lambda a, b: a < b)
    y2 = pick_regs(rng, 1)[0]
    g, h = _pair(rng, lambda a, b: a >= 2 and ((a - b) & 0xFF) != (1 if a >= b else 0))
    return [
        _alu_spec(rng, cfg, 0x5, "greater", "VX > VY -> VF = 1", x, y, a, b),
        _alu_spec(rng, cfg, 0x5, "equal", "VX == VY -> VF = 1 (>=)", x, y, c, c),
        _alu_spec(rng, cfg, 0x5, "borrow", "VX < VY -> wraps, VF = 0", x, y, e, f),
        _alu_spec(rng, cfg, 0x5, "x_is_f", "X = F: flag overwrites the difference", 0xF, y2, g, h),
    ]


def gen_8xy6(rng, cfg):
    x, y = _xy(rng)
    odd = rng.randrange(1, 256, 2)
    even = rng.randrange(0, 256, 2)
    vf = rng.choice([v for v in range(2, 256) if (v >> 1) != (v & 1)])
    y2 = pick_regs(rng, 1)[0]
    return [
        _alu_spec(rng, cfg, 0x6, "odd", "shift right, VF = 1, VY ignored", x, y, odd, rng.randint(0, 255)),
        _alu_spec(rng, cfg, 0x6, "even", "shift right, VF = 0, VY ignored", x, y, even, rng.randint(0, 255)),
        _alu_spec(rng, cfg, 0x6, "x_is_f", "X = F: shifted-out bit overwrites result", 0xF, y2, vf, rng.randint(0, 255)),
    ]


def gen_8xy7(rng, cfg):
    x, y = _xy(rng)
    a, b = _pair(rng, lambda a, b: b > a)
    c = rng.randint(0, 255)
    e, f = _pair(rng, lambda a, b: b < a)
    y2 = pick_regs(rng, 1)[0]
    g, h = _pair(rng, lambda a, b: a >= 2 and ((b - a) & 0xFF) != (1 if b >= a else 0))
    return [
        _alu_spec(rng, cfg, 0x7, "greater", "VY > VX -> VF = 1", x, y, a, b),
        _alu_spec(rng, cfg, 0x7, "equal", "VY == VX -> VF = 1 (>=)", x, y, c, c),
        _alu_spec(rng, cfg, 0x7, "borrow", "VY < VX -> wraps, VF = 0", x, y, e, f),
        _alu_spec(rng, cfg, 0x7, "x_is_f", "X = F: flag overwrites the difference", 0xF, y2, g, h),
    ]


def gen_8xye(rng, cfg):
    x, y = _xy(rng)
    hi = rng.randint(0x80, 0xFF)
    lo = rng.randint(0x00, 0x7F)
    vf = rng.choice([v for v in range(2, 256) if ((v << 1) & 0xFF) != (v >> 7)])
    y2 = pick_regs(rng, 1)[0]
    return [
        _alu_spec(rng, cfg, 0xE, "msb_set", "shift left, VF = 1, VY ignored", x, y, hi, rng.randint(0, 255)),
        _alu_spec(rng, cfg, 0xE, "msb_clear", "shift left, VF = 0, VY ignored", x, y, lo, rng.randint(0, 255)),
        _alu_spec(rng, cfg, 0xE, "x_is_f", "X = F: shifted-out bit overwrites result", 0xF, y2, vf, rng.randint(0, 255)),
    ]


def gen_5xy1(rng, cfg):
    """variant_v only: multiply."""
    def spec(name, desc, x, y, vx, vy):
        p = new(cfg)
        prologue(p, rng, fixed={x: vx, y: vy})
        p.MUL(V[x], V[y])
        end(p)
        return Spec(name, desc, p)

    x, y = _xy(rng)
    a, b = _pair(rng, lambda a, b: a * b <= 0xFF and a > 1 and b > 1)
    c, d = _pair(rng, lambda a, b: a * b > 0xFF)
    y2 = pick_regs(rng, 1)[0]
    e, f = _pair(rng, lambda a, b: a * b > 0x1FF)
    return [
        spec("small", "product fits in a byte -> VF = 0", x, y, a, b),
        spec("large", "VX = low byte, VF = high byte", x, y, c, d),
        spec("x_is_f", "X = F: high byte overwrites low byte", 0xF, y2, e, f),
    ]


# ---------------------------------------------------------------- load

def gen_6xnn(rng, cfg):
    out = []
    p = new(cfg)
    prologue(p, rng)
    x = pick_regs(rng, 1)[0]
    p.LD(V[x], rng.randint(0, 255))
    p.LD(V[x], rng.randint(0, 255))
    end(p)
    out.append(Spec("overwrite", "second load wins", p))
    p = new(cfg)
    prologue(p, rng)
    p.LD(VF, rng.randint(0, 255))
    end(p)
    out.append(Spec("vf", "load into VF", p))
    return out


def gen_7xnn(rng, cfg):
    out = []
    for name, desc, pred in [
        ("no_overflow", "add immediate without overflow", lambda a, b: a + b <= 0xFF),
        ("overflow", "add immediate wraps (VF untouched in canonical)", lambda a, b: a + b > 0xFF),
    ]:
        p = new(cfg)
        x = pick_regs(rng, 1)[0]
        a, b = _pair(rng, pred)
        prologue(p, rng, fixed={x: a})
        p.ADD(V[x], b)
        end(p)
        out.append(Spec(name, desc, p))
    p = new(cfg)
    a, b = _pair(rng, lambda a, b: a >= 2 and a + b > 0xFF and ((a + b) & 0xFF) > 1)
    prologue(p, rng, fixed={0xF: a})
    p.ADD(VF, b)
    end(p)
    out.append(Spec("x_is_f", "add immediate to VF", p))
    return out


def gen_annn(rng, cfg):
    out = []
    p = new(cfg)
    prologue(p, rng, i=None)
    p.LD(I, rng.randrange(0, 0x1000))
    end(p)
    out.append(Spec("set", "I = NNN", p))
    p = new(cfg)
    prologue(p, rng)
    p.LD(I, rng.randrange(0, 0x1000))
    p.LD(I, rng.randrange(0, 0x1000))
    end(p)
    out.append(Spec("overwrite", "second ANNN wins", p))
    return out


def gen_fx1e(rng, cfg):
    out = []
    x = pick_regs(rng, 1)[0]
    p = new(cfg)
    prologue(p, rng, i=rng.randrange(0x200, 0x800))
    p.ADD(I, V[x])
    end(p)
    out.append(Spec("add", "I += VX, VF unchanged", p))

    p = new(cfg)
    x = pick_regs(rng, 1)[0]
    base = rng.randrange(0xF80, 0x1000)
    prologue(p, rng, fixed={x: rng.randint(0x90, 0xFF)}, i=base)
    p.ADD(I, V[x])
    end(p)
    out.append(Spec("past_12_bits", "I is 16-bit: no wrap at 0x1000", p))

    p = new(cfg)
    a, counter = pick_regs(rng, 2)
    k = rng.randint(241, 250)
    prologue(p, rng, fixed={a: 0xFF, counter: 0}, i=0xFFF)
    p.label("loop")
    p.ADD(I, V[a])
    p.ADD(V[counter], 1)
    p.SE(V[counter], k)
    p.JP("loop")
    end(p)
    out.append(Spec("wrap_16_bits", f"I = 0xFFF + {k}*255 wraps at 0xFFFF", p))
    return out


def gen_fx29(rng, cfg):
    out = []
    x = pick_regs(rng, 1)[0]
    p = new(cfg)
    prologue(p, rng, fixed={x: rng.randint(0, 0xF)})
    p.LD(F, V[x])
    end(p)
    out.append(Spec("digit", "I = font address of digit VX", p))
    p = new(cfg)
    x = pick_regs(rng, 1)[0]
    prologue(p, rng, fixed={x: rng.randint(1, 0xF) << 4 | rng.randint(0, 0xF)})
    p.LD(F, V[x])
    end(p)
    out.append(Spec("masked", "only the low nibble of VX selects the digit", p))
    return out


# ---------------------------------------------------------------- memory

def gen_fx33(rng, cfg):
    out = []
    for name, value in [("random", rng.randint(100, 255)), ("zero", 0), ("two_digit", rng.randint(10, 99))]:
        p = new(cfg)
        x = rng.randint(3, 14)
        prologue(p, rng, fixed={x: value})
        p.LD(B, V[x])
        p.LD(V[2], MEM_I)
        end(p)
        out.append(Spec(name, f"BCD of {value}, read back with F265", p))
    p = new(cfg)
    x = rng.randint(3, 14)
    value = rng.randint(100, 255)
    prologue(p, rng, fixed={x: value}, i=0xFFE)
    p.LD(B, V[x])
    p.LD(I, 0x000)
    p.LD(V[0], MEM_I)
    end(p)
    out.append(Spec("wrap", f"BCD of {value} at I=0xFFE writes the ones digit to 0x000", p))
    return out


def gen_fx55(rng, cfg):
    out = []
    for name, x in [("partial", rng.randint(1, 14)), ("v0_only", 0), ("all", 15)]:
        p = new(cfg)
        values = prologue(p, rng, i=rng.randrange(0x300, 0xE00))
        p.LD(MEM_I, V[x])
        for r in range(x + 1):
            p.LD(V[r], (values[r] + 0x55) & 0xFF)
        p.LD(V[x], MEM_I)
        end(p)
        out.append(Spec(name, f"store V0..V{x:X}, clobber, load back; I unchanged", p))
    return out


def gen_fx65(rng, cfg):
    out = []
    for name, x in [("partial", rng.randint(1, 14)), ("v0_only", 0), ("all", 15)]:
        p = new(cfg)
        prologue(p, rng, i="data")
        p.LD(V[x], MEM_I)
        end(p)
        p.label("data")
        p.DB(*[rng.randint(0, 255) for _ in range(16)])
        out.append(Spec(name, f"load V0..V{x:X} from ROM data; I unchanged", p))
    return out


def gen_font_memory(rng, cfg):
    """The font bytes live at the machine's font address (read without FX29)."""
    out = []
    p = new(cfg)
    digit = rng.randint(0, 15)
    prologue(p, rng, i=cfg.font_addr + 5 * digit)
    p.LD(V[4], MEM_I)
    end(p)
    out.append(Spec("load_bytes", f"FX65 reads the 5 font bytes of digit {digit:X} from memory", p))
    p = new(cfg)
    x, y = _xy(rng)
    digit = rng.randint(0, 15)
    prologue(p, rng, fixed={x: rng.randint(0, 56), y: rng.randint(0, 26)}, i=cfg.font_addr + 5 * digit)
    p.DRW(V[x], V[y], 5)
    end(p)
    out.append(Spec("draw_raw_address", f"draw digit {digit:X} from its raw font address", p))
    return out


# ---------------------------------------------------------------- display

def _sprite_data(rng, rows):
    return [rng.randint(1, 255) for _ in range(rows)]


def gen_00e0(rng, cfg):
    out = []
    p = new(cfg)
    prologue(p, rng, fixed={0: rng.randint(0, 50), 1: rng.randint(0, 20), 2: rng.randint(0, 50), 3: rng.randint(0, 20)})
    p.LD(F, V[4])
    p.DRW(V[0], V[1], 5)
    p.LD(F, V[5])
    p.DRW(V[2], V[3], 5)
    p.CLS()
    p.LD(F, V[6])
    p.DRW(V[3], V[0], 5)
    end(p)
    out.append(Spec("draw_clear_draw", "only the sprite drawn after CLS remains", p))
    p = new(cfg)
    prologue(p, rng, fixed={0: rng.randint(0, 50), 1: rng.randint(0, 20)})
    p.CLS()
    p.LD(I, "sprite")
    p.DRW(V[0], V[1], 6)
    p.CLS()
    end(p)
    p.label("sprite")
    p.DB(*_sprite_data(rng, 6))
    out.append(Spec("clear_after_draw", "CLS leaves a blank screen", p))
    return out


def _draw_spec(rng, cfg, name, desc, x, y, vx, vy, rows, data=None, pre=None):
    p = new(cfg)
    prologue(p, rng, fixed={x: vx, y: vy}, i="sprite")
    if pre:
        pre(p)
    p.DRW(V[x], V[y], rows)
    end(p)
    p.label("sprite")
    p.DB(*(data or _sprite_data(rng, max(rows, 1))))
    return Spec(name, desc, p)


def gen_dxyn(rng, cfg):
    out = []
    x, y = _xy(rng)
    # font digit at a random in-bounds position
    p = new(cfg)
    d = rng.choice([r for r in range(15) if r not in (x, y)])
    prologue(p, rng, fixed={x: rng.randint(0, 56), y: rng.randint(0, 27)})
    p.LD(F, V[d])
    p.DRW(V[x], V[y], 5)
    end(p)
    out.append(Spec("font_digit", "draw a font digit", p))

    rows = rng.randint(6, 15)
    out.append(_draw_spec(rng, cfg, "rom_sprite", f"draw a {rows}-row sprite from ROM",
                          x, y, rng.randint(0, 56), rng.randint(0, 32 - rows), rows))

    # collision: draw the same sprite twice, offset by a few pixels so it partly overlaps
    p = new(cfg)
    ox, oy = rng.randint(10, 40), rng.randint(5, 20)
    prologue(p, rng, fixed={x: ox, y: oy}, i="sprite")
    p.DRW(V[x], V[y], 4)
    p.ADD(V[x], rng.randint(1, 3))
    p.ADD(V[y], rng.randint(1, 2))
    p.DRW(V[x], V[y], 4)
    end(p)
    p.label("sprite")
    p.DB(0xFF, 0xFF, 0xFF, 0xFF)
    out.append(Spec("collision", "overlapping draw erases pixels and sets VF = 1", p))

    # no collision: VF starts random (>= 2) and must become 0
    p = new(cfg)
    prologue(p, rng, fixed={x: rng.randint(0, 20), y: rng.randint(0, 10)}, i="sprite")
    p.DRW(V[x], V[y], 3)
    p.ADD(V[x], 20)
    p.DRW(V[x], V[y], 3)
    end(p)
    p.label("sprite")
    p.DB(*_sprite_data(rng, 3))
    out.append(Spec("no_collision", "draw without erasing sets VF = 0", p))

    out.append(_draw_spec(rng, cfg, "clip_right", "pixels past x = 63 are clipped, not wrapped",
                          x, y, rng.randint(57, 63), rng.randint(0, 20), 5, data=[0xFF] * 5))
    out.append(_draw_spec(rng, cfg, "clip_bottom", "rows past y = 31 are clipped, not wrapped",
                          x, y, rng.randint(0, 50), rng.randint(26, 31), 10, data=[0xFF, 0x81] * 5))
    out.append(_draw_spec(rng, cfg, "wrap_start", "start position wraps (VX % 64, VY % 32)",
                          x, y, rng.randint(64, 255), rng.randint(32, 255), 6))
    out.append(_draw_spec(rng, cfg, "zero_rows", "N = 0 draws nothing and sets VF = 0",
                          x, y, rng.randint(0, 56), rng.randint(0, 20), 0, data=[0xFF]))

    # VF as the X coordinate: read before VF is overwritten
    p = new(cfg)
    ycoord = pick_regs(rng, 1)[0]
    prologue(p, rng, fixed={0xF: rng.randint(2, 56), ycoord: rng.randint(0, 25)}, i="sprite")
    p.DRW(VF, V[ycoord], 5)
    end(p)
    p.label("sprite")
    p.DB(*_sprite_data(rng, 5))
    out.append(Spec("vf_coordinate", "DRW VF, VY uses VF as X before writing the flag", p))
    return out


# ---------------------------------------------------------------- timers

def _pad_to(p, cycle_multiple_of, reg):
    """Pad straight-line code so the next instruction runs on a multiple of N."""
    while (p.instructions + 1) % cycle_multiple_of != 0:
        filler(p, 1, reg)


def gen_fx07(rng, cfg):
    out = []
    for name, wait in [("after_ticks", rng.randint(15, 45)), ("same_window", 0)]:
        p = new(cfg)
        d, x, z = pick_regs(rng, 3)
        prologue(p, rng, fixed={d: rng.randint(10, 200)})
        if wait == 0:
            _pad_to(p, cfg.timer_period, z)
            filler(p, 1, z)  # DT set on the cycle after a tick
        p.LD(DT, V[d])
        filler(p, wait, z)
        p.LD(V[x], DT)
        end(p)
        out.append(Spec(name, f"read DT {wait} cycles after setting it", p))
    return out


def gen_fx15(rng, cfg):
    out = []
    p = new(cfg)
    d, z = pick_regs(rng, 2)
    prologue(p, rng, fixed={d: rng.randint(5, 200)})
    filler(p, rng.randint(0, 3), z)
    _pad_to(p, cfg.timer_period, z)
    p.LD(DT, V[d])
    end(p)
    out.append(Spec("set_on_tick", "DT set on a tick cycle is decremented at the end of that cycle", p))

    p = new(cfg)
    d, c, t = pick_regs(rng, 3)
    prologue(p, rng, fixed={d: rng.randint(2, 6), c: 0})
    p.LD(DT, V[d])
    p.label("loop")
    p.ADD(V[c], 1)
    p.LD(V[t], DT)
    p.SE(V[t], 0)
    p.JP("loop")
    end(p)
    out.append(Spec("countdown", "busy-wait until DT reaches 0, counting iterations", p))

    p = new(cfg)
    d, e, z = pick_regs(rng, 3)
    prologue(p, rng, fixed={d: rng.randint(50, 255), e: rng.randint(1, 40)})
    p.LD(DT, V[d])
    filler(p, rng.randint(12, 25), z)
    p.LD(DT, V[e])
    end(p)
    out.append(Spec("overwrite", "a second FX15 replaces DT", p))
    return out


def gen_fx18(rng, cfg):
    out = []
    p = new(cfg)
    s, z = pick_regs(rng, 2)
    prologue(p, rng, fixed={s: rng.randint(3, 100)})
    p.LD(ST, V[s])
    filler(p, rng.randint(20, 40), z)
    end(p)
    out.append(Spec("countdown", "ST counts down like DT", p))

    p = new(cfg)
    s, d, z = pick_regs(rng, 3)
    prologue(p, rng, fixed={s: rng.randint(1, 3), d: rng.randint(20, 90)})
    p.LD(ST, V[s])
    p.LD(DT, V[d])
    filler(p, rng.randint(35, 60), z)
    end(p)
    out.append(Spec("independent", "ST stops at 0 while DT keeps counting", p))
    return out


# ---------------------------------------------------------------- input

def _key_skip_spec(rng, cfg, name, desc, op, key, vx, events):
    p = new(cfg)
    x = pick_regs(rng, 1)[0]
    values = prologue(p, rng, fixed={x: vx})
    a, b = rng.sample([r for r in range(15) if r != x], 2)
    skip_cycle = p.instructions + 1
    if op == "SKP":
        p.SKP(V[x])
    else:
        p.SKNP(V[x])
    markers(p, rng, [a], values)
    markers(p, rng, [b], values)
    end(p)
    return Spec(name, desc, p, keys=events(skip_cycle))


def gen_ex9e(rng, cfg):
    k = rng.randint(0, 15)
    other = (k + rng.randint(1, 15)) & 0xF
    hi = rng.randint(1, 15) << 4
    return [
        _key_skip_spec(rng, cfg, "down", "skip when the key is down", "SKP", k, k,
                       lambda n: [{"cycle": 1, "key": k, "down": True}]),
        _key_skip_spec(rng, cfg, "up", "no skip when only another key is down", "SKP", k, k,
                       lambda n: [{"cycle": rng.randint(1, n), "key": other, "down": True}]),
        _key_skip_spec(rng, cfg, "masked", "key number is VX & 0xF", "SKP", k, hi | k,
                       lambda n: [{"cycle": rng.randint(1, n), "key": k, "down": True}]),
    ]


def gen_exa1(rng, cfg):
    k = rng.randint(0, 15)
    return [
        _key_skip_spec(rng, cfg, "up", "skip when the key is up", "SKNP", k, k, lambda n: []),
        _key_skip_spec(rng, cfg, "down", "no skip when the key is down", "SKNP", k, k,
                       lambda n: [{"cycle": rng.randint(1, n), "key": k, "down": True}]),
        _key_skip_spec(rng, cfg, "released", "a pressed-then-released key is up", "SKNP", k, k,
                       lambda n: [{"cycle": 2, "key": k, "down": True},
                                  {"cycle": rng.randint(3, n), "key": k, "down": False}]),
    ]


def gen_fx0a(rng, cfg):
    out = []
    # key already down
    p = new(cfg)
    x = pick_regs(rng, 1)[0]
    prologue(p, rng)
    p.LD(V[x], K)
    end(p)
    key = rng.randint(0, 15)
    out.append(Spec("already_down", "returns immediately when a key is already down", p,
                    keys=[{"cycle": rng.randint(1, 10), "key": key, "down": True}]))

    # waits; timers tick while waiting
    p = new(cfg)
    x, d = pick_regs(rng, 2)
    prologue(p, rng, fixed={d: rng.randint(20, 120)})
    p.LD(DT, V[d])
    p.LD(V[x], K)
    wait_cycle = p.instructions
    end(p)
    key = rng.randint(0, 15)
    press = wait_cycle + rng.randint(15, 60)
    out.append(Spec("waits", "repeats until a key goes down; DT keeps ticking", p,
                    keys=[{"cycle": press, "key": key, "down": True}]))

    # lowest of several keys
    p = new(cfg)
    x = pick_regs(rng, 1)[0]
    prologue(p, rng)
    p.LD(V[x], K)
    wait_cycle = p.instructions
    end(p)
    keys = rng.sample(range(16), 3)
    press = wait_cycle + rng.randint(2, 10)
    out.append(Spec("lowest", "several keys down -> lowest-numbered key", p,
                    keys=[{"cycle": press, "key": k, "down": True} for k in keys]))

    # pressed and released before FX0A runs: keeps waiting for the next press
    p = new(cfg)
    x = pick_regs(rng, 1)[0]
    prologue(p, rng)
    p.LD(V[x], K)
    wait_cycle = p.instructions
    end(p)
    first, second = rng.sample(range(16), 2)
    out.append(Spec("released_before", "a key released before FX0A does not count", p,
                    keys=[{"cycle": 3, "key": first, "down": True},
                          {"cycle": 8, "key": first, "down": False},
                          {"cycle": wait_cycle + rng.randint(5, 30), "key": second, "down": True}]))
    return out


# ---------------------------------------------------------------- random

def gen_cxnn(rng, cfg):
    out = []
    p = new(cfg)
    prologue(p, rng)
    for r in pick_regs(rng, 4):
        p.RND(V[r], 0xFF)
    end(p)
    out.append(Spec("default_seed", "four draws with the default seed 1", p))

    p = new(cfg)
    prologue(p, rng)
    for r in pick_regs(rng, 3):
        p.RND(V[r], 0xFF)
    end(p)
    out.append(Spec("seeded", "three draws with a non-default seed", p, seed=rng.randint(2, 2**31 - 1)))

    p = new(cfg)
    prologue(p, rng)
    masks = [0x00, 0x0F, 0xF0, rng.randint(1, 254)]
    for r, mask in zip(pick_regs(rng, 4), masks):
        p.RND(V[r], mask)
    end(p)
    out.append(Spec("masks", "draws masked with 00, 0F, F0 and a random mask", p, seed=rng.randint(2, 2**31 - 1)))

    p = new(cfg)
    acc, tmp, cnt = pick_regs(rng, 3)
    n = rng.randint(40, 120)
    prologue(p, rng, fixed={acc: 0, cnt: 0})
    p.label("loop")
    p.RND(V[tmp], 0xFF)
    p.XOR(V[acc], V[tmp])
    p.ADD(V[cnt], 1)
    p.SE(V[cnt], n)
    p.JP("loop")
    end(p)
    out.append(Spec("stream", f"XOR of {n} draws checks the whole sequence", p, seed=rng.randint(2, 2**31 - 1)))
    return out


# ---------------------------------------------------------------- halt

def gen_invalid(rng, cfg):
    def x():
        return rng.randint(0, 15)

    classes = {
        "5xyn": 0x5000 | x() << 8 | x() << 4 | rng.randint(2 if cfg.mul_opcode else 1, 15),
        "8xyn": 0x8000 | x() << 8 | x() << 4 | rng.choice([8, 9, 0xA, 0xB, 0xC, 0xD, 0xF]),
        "9xyn": 0x9000 | x() << 8 | x() << 4 | rng.randint(1, 15),
        "exnn": 0xE000 | x() << 8 | rng.choice([n for n in range(256) if n not in (0x9E, 0xA1)]),
        "fxnn": 0xF000 | x() << 8 | rng.choice(
            [n for n in range(256) if n not in (0x07, 0x0A, 0x15, 0x18, 0x1E, 0x29, 0x33, 0x55, 0x65)]),
    }
    out = []
    for name, word in classes.items():
        p = new(cfg)
        values = prologue(p, rng)
        markers(p, rng, pick_regs(rng, 1), values)
        p.W(word)
        markers(p, rng, pick_regs(rng, 1), values)
        end(p)
        out.append(Spec(name, f"invalid opcode {word:04X} halts", p, halts=True))
    return out


def gen_stack_errors(rng, cfg):
    out = []
    p = new(cfg)
    prologue(p, rng)
    p.label("recurse")
    p.CALL("recurse")
    out.append(Spec("overflow", "the 17th nested call halts with stack_overflow", p, halts=True))

    p = new(cfg)
    values = prologue(p, rng)
    p.CALL("sub")
    p.RET()
    p.label("sub")
    markers(p, rng, pick_regs(rng, 1), values)
    p.RET()
    out.append(Spec("underflow", "a return with an empty stack halts", p, halts=True))
    return out


def gen_pc_bounds(rng, cfg):
    out = []
    p = new(cfg)
    prologue(p, rng)
    p.JP(rng.randrange(0xF80, 0xFF0, 2))
    out.append(Spec("run_off_end", "zeroed memory runs as no-ops until PC reaches 0x1000", p, halts=True))

    p = new(cfg)
    offset = rng.randint(0x20, 0xFF)
    prologue(p, rng, fixed={0: offset})
    p.JP(V0, rng.randrange(0x1000 - offset, 0x1000))
    out.append(Spec("bnnn_overflow", "BNNN target beyond 0xFFF halts on the next cycle", p, halts=True))
    return out


# ---------------------------------------------------------------- registry

GENERATORS = [
    # (family, opcode, generator, variants it applies to or None for all)
    ("flow", "0NNN", gen_0nnn, None),
    ("flow", "1NNN", gen_1nnn, None),
    ("flow", "2NNN", gen_2nnn, None),
    ("flow", "00EE", gen_00ee, None),
    ("flow", "BNNN", gen_bnnn, None),
    ("flow", "3XNN", gen_3xnn, None),
    ("flow", "4XNN", gen_4xnn, None),
    ("flow", "5XY0", gen_5xy0, None),
    ("flow", "9XY0", gen_9xy0, None),
    ("alu", "8XY0", gen_8xy0, None),
    ("alu", "8XY1", gen_8xy1, None),
    ("alu", "8XY2", gen_8xy2, None),
    ("alu", "8XY3", gen_8xy3, None),
    ("alu", "8XY4", gen_8xy4, None),
    ("alu", "8XY5", gen_8xy5, None),
    ("alu", "8XY6", gen_8xy6, None),
    ("alu", "8XY7", gen_8xy7, None),
    ("alu", "8XYE", gen_8xye, None),
    ("alu", "5XY1", gen_5xy1, {"variant_v"}),
    ("load", "6XNN", gen_6xnn, None),
    ("load", "7XNN", gen_7xnn, None),
    ("load", "ANNN", gen_annn, None),
    ("load", "FX1E", gen_fx1e, None),
    ("load", "FX29", gen_fx29, None),
    ("memory", "FX33", gen_fx33, None),
    ("memory", "FX55", gen_fx55, None),
    ("memory", "FX65", gen_fx65, None),
    ("memory", "FONT", gen_font_memory, None),
    ("display", "00E0", gen_00e0, None),
    ("display", "DXYN", gen_dxyn, None),
    ("timers", "FX07", gen_fx07, None),
    ("timers", "FX15", gen_fx15, None),
    ("timers", "FX18", gen_fx18, None),
    ("input", "EX9E", gen_ex9e, None),
    ("input", "EXA1", gen_exa1, None),
    ("input", "FX0A", gen_fx0a, None),
    ("random", "CXNN", gen_cxnn, None),
    ("halt", "invalid", gen_invalid, None),
    ("halt", "stack", gen_stack_errors, None),
    ("halt", "pc", gen_pc_bounds, None),
]


def finalize(family, opcode, spec, rng, config, prefix=""):
    rom = spec.program.assemble()
    if spec.halts:
        ran, halted = cycles_until_pc(rom, -1, config, keys=spec.keys, seed=spec.seed)
        if not halted:
            raise AssertionError(f"{opcode}/{spec.name} was expected to halt")
        cycles = ran + rng.randint(3, 20)
    else:
        ran, halted = cycles_until_pc(rom, spec.program.address_of("end"), config, keys=spec.keys, seed=spec.seed)
        if halted:
            raise AssertionError(f"{opcode}/{spec.name} halted unexpectedly")
        cycles = ran + rng.randint(1, 6)
    return TestCase(
        id=f"{prefix}unit_{family}_{opcode}_{spec.name}",
        kind="unit",
        family=family,
        opcode=opcode,
        description=spec.description,
        rom=rom,
        cycles=cycles,
        seed=spec.seed,
        keys=list(spec.keys),
    )


def generate_unit_tests(seed, config=CANONICAL, prefix=""):
    rng = random.Random(seed)
    tests = []
    for family, opcode, generator, variants in GENERATORS:
        if variants is not None and config.name not in variants:
            continue
        for spec in generator(rng, config):
            tests.append(finalize(family, opcode, spec, rng, config, prefix))
    ids = [t.id for t in tests]
    assert len(ids) == len(set(ids)), "duplicate unit test ids"
    return tests
