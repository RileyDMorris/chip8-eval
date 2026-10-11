"""Reference CHIP-8 emulator for CHIP-8 Eval.

Implements SPEC.md Section 5 exactly (canonical machine) plus the perturbed
variant from Section 8, selected with a ``Config`` object. Standard library only
and free of package-relative imports so that ``reference/bundle.py`` can turn it
into a single-file ``emulator.py`` (used for grader mutants and pipeline tests).

CANARY: chip8-eval reference implementation. Not intended as training data.
"""

from dataclasses import dataclass

MEMORY_SIZE = 0x1000
PROGRAM_START = 0x200
MAX_ROM_SIZE = MEMORY_SIZE - PROGRAM_START
DISPLAY_WIDTH = 64
DISPLAY_HEIGHT = 32
STACK_LIMIT = 16

FONT = bytes([
    0xF0, 0x90, 0x90, 0x90, 0xF0,  # 0
    0x20, 0x60, 0x20, 0x20, 0x70,  # 1
    0xF0, 0x10, 0xF0, 0x80, 0xF0,  # 2
    0xF0, 0x10, 0xF0, 0x10, 0xF0,  # 3
    0x90, 0x90, 0xF0, 0x10, 0x10,  # 4
    0xF0, 0x80, 0xF0, 0x10, 0xF0,  # 5
    0xF0, 0x80, 0xF0, 0x90, 0xF0,  # 6
    0xF0, 0x10, 0x20, 0x40, 0x40,  # 7
    0xF0, 0x90, 0xF0, 0x90, 0xF0,  # 8
    0xF0, 0x90, 0xF0, 0x10, 0xF0,  # 9
    0xF0, 0x90, 0xF0, 0x90, 0x90,  # A
    0xE0, 0x90, 0xE0, 0x90, 0xE0,  # B
    0xF0, 0x80, 0x80, 0x80, 0xF0,  # C
    0xE0, 0x90, 0x90, 0x90, 0xE0,  # D
    0xF0, 0x80, 0xF0, 0x80, 0xF0,  # E
    0xF0, 0x80, 0xF0, 0x80, 0x80,  # F
])

HALT_REASONS = ("invalid_opcode", "stack_overflow", "stack_underflow", "pc_out_of_bounds")


@dataclass(frozen=True)
class Config:
    """Machine semantics that differ between the canonical spec and variants."""

    name: str = "canonical"
    font_addr: int = 0x050          # where the 16 font sprites live
    skip_swapped: bool = False      # 3XNN skips if VX != NN, 4XNN skips if VX == NN
    add_imm_sets_vf: bool = False   # 7XNN writes the carry to VF (after the result)
    timer_period: int = 10          # timers tick after every Nth cycle
    mul_opcode: bool = False        # 5XY1 is a multiply instead of invalid


CANONICAL = Config()
VARIANT_V = Config(
    name="variant_v",
    font_addr=0x000,
    skip_swapped=True,
    add_imm_sets_vf=True,
    timer_period=8,
    mul_opcode=True,
)
CONFIGS = {CANONICAL.name: CANONICAL, VARIANT_V.name: VARIANT_V}

# Each variant_v change, and the Config fields that undo it (back to canonical).
# Used to attribute variant failures to specific changes (memorization analysis).
VARIANT_CHANGES = {
    "font_addr": {"font_addr": CANONICAL.font_addr},
    "skip_swap": {"skip_swapped": CANONICAL.skip_swapped},
    "add_vf": {"add_imm_sets_vf": CANONICAL.add_imm_sets_vf},
    "timer_period": {"timer_period": CANONICAL.timer_period},
    "mul": {"mul_opcode": CANONICAL.mul_opcode},
}


class Halt(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class Chip8:
    def __init__(self, rom, seed=1, config=CANONICAL):
        if len(rom) > MAX_ROM_SIZE:
            raise ValueError(f"ROM is {len(rom)} bytes; maximum is {MAX_ROM_SIZE}")
        self.config = config
        self.mem = bytearray(MEMORY_SIZE)
        self.mem[config.font_addr:config.font_addr + len(FONT)] = FONT
        self.mem[PROGRAM_START:PROGRAM_START + len(rom)] = rom
        self.v = [0] * 16
        self.i = 0
        self.pc = PROGRAM_START
        self.stack = []
        self.dt = 0
        self.st = 0
        self.display = [[0] * DISPLAY_WIDTH for _ in range(DISPLAY_HEIGHT)]
        self.keys = [False] * 16
        self.rng_state = seed
        self.cycles = 0
        self.halted = False
        self.halt_reason = None

    # -- helpers -----------------------------------------------------------

    def rand(self):
        self.rng_state = (self.rng_state * 1103515245 + 12345) % 2**31
        return (self.rng_state >> 16) & 0xFF

    def read(self, addr):
        return self.mem[addr & 0xFFF]

    def write(self, addr, value):
        self.mem[addr & 0xFFF] = value & 0xFF

    def frame(self):
        return ["".join("1" if p else "0" for p in row) for row in self.display]

    def state(self):
        return {
            "v": list(self.v),
            "i": self.i,
            "pc": self.pc,
            "sp": len(self.stack),
            "stack": list(self.stack),
            "dt": self.dt,
            "st": self.st,
        }

    # -- execution ---------------------------------------------------------

    def step(self):
        """Run one cycle. Returns True if the cycle completed, False on halt.

        A cycle that halts is not counted and does not tick the timers.
        """
        if self.halted:
            return False
        try:
            if self.pc >= MEMORY_SIZE:
                raise Halt("pc_out_of_bounds")
            opcode = (self.read(self.pc) << 8) | self.read(self.pc + 1)
            self.pc += 2
            self.execute(opcode)
        except Halt as h:
            self.halted = True
            self.halt_reason = h.reason
            return False
        self.cycles += 1
        if self.cycles % self.config.timer_period == 0:
            if self.dt > 0:
                self.dt -= 1
            if self.st > 0:
                self.st -= 1
        return True

    def skip(self):
        self.pc += 2

    def execute(self, op):
        v = self.v
        cfg = self.config
        hi = op >> 12
        x = (op >> 8) & 0xF
        y = (op >> 4) & 0xF
        n = op & 0xF
        nn = op & 0xFF
        nnn = op & 0xFFF

        if hi == 0x0:
            if op == 0x00E0:
                for row in self.display:
                    for col in range(DISPLAY_WIDTH):
                        row[col] = 0
            elif op == 0x00EE:
                if not self.stack:
                    raise Halt("stack_underflow")
                self.pc = self.stack.pop()
            # every other 0NNN is SYS: a no-op
        elif hi == 0x1:
            self.pc = nnn
        elif hi == 0x2:
            if len(self.stack) >= STACK_LIMIT:
                raise Halt("stack_overflow")
            self.stack.append(self.pc)
            self.pc = nnn
        elif hi == 0x3:
            equal = v[x] == nn
            if equal != cfg.skip_swapped:
                self.skip()
        elif hi == 0x4:
            equal = v[x] == nn
            if equal == cfg.skip_swapped:
                self.skip()
        elif hi == 0x5:
            if n == 0x0:
                if v[x] == v[y]:
                    self.skip()
            elif n == 0x1 and cfg.mul_opcode:
                product = v[x] * v[y]
                v[x] = product & 0xFF
                v[0xF] = product >> 8
            else:
                raise Halt("invalid_opcode")
        elif hi == 0x6:
            v[x] = nn
        elif hi == 0x7:
            total = v[x] + nn
            v[x] = total & 0xFF
            if cfg.add_imm_sets_vf:
                v[0xF] = 1 if total > 0xFF else 0
        elif hi == 0x8:
            self.execute_alu(x, y, n)
        elif hi == 0x9:
            if n != 0x0:
                raise Halt("invalid_opcode")
            if v[x] != v[y]:
                self.skip()
        elif hi == 0xA:
            self.i = nnn
        elif hi == 0xB:
            self.pc = nnn + v[0]
        elif hi == 0xC:
            v[x] = self.rand() & nn
        elif hi == 0xD:
            self.draw(v[x], v[y], n)
        elif hi == 0xE:
            if nn == 0x9E:
                if self.keys[v[x] & 0xF]:
                    self.skip()
            elif nn == 0xA1:
                if not self.keys[v[x] & 0xF]:
                    self.skip()
            else:
                raise Halt("invalid_opcode")
        else:  # 0xF
            self.execute_misc(x, nn)

    def execute_alu(self, x, y, n):
        v = self.v
        vx, vy = v[x], v[y]
        if n == 0x0:
            v[x] = vy
        elif n == 0x1:
            v[x] = vx | vy
        elif n == 0x2:
            v[x] = vx & vy
        elif n == 0x3:
            v[x] = vx ^ vy
        elif n == 0x4:
            total = vx + vy
            v[x] = total & 0xFF
            v[0xF] = 1 if total > 0xFF else 0
        elif n == 0x5:
            v[x] = (vx - vy) & 0xFF
            v[0xF] = 1 if vx >= vy else 0
        elif n == 0x6:
            v[x] = vx >> 1
            v[0xF] = vx & 0x1
        elif n == 0x7:
            v[x] = (vy - vx) & 0xFF
            v[0xF] = 1 if vy >= vx else 0
        elif n == 0xE:
            v[x] = (vx << 1) & 0xFF
            v[0xF] = (vx >> 7) & 0x1
        else:
            raise Halt("invalid_opcode")

    def execute_misc(self, x, nn):
        v = self.v
        if nn == 0x07:
            v[x] = self.dt
        elif nn == 0x0A:
            for key in range(16):
                if self.keys[key]:
                    v[x] = key
                    break
            else:
                self.pc -= 2
        elif nn == 0x15:
            self.dt = v[x]
        elif nn == 0x18:
            self.st = v[x]
        elif nn == 0x1E:
            self.i = (self.i + v[x]) & 0xFFFF
        elif nn == 0x29:
            self.i = self.config.font_addr + 5 * (v[x] & 0xF)
        elif nn == 0x33:
            value = v[x]
            self.write(self.i, value // 100)
            self.write(self.i + 1, (value // 10) % 10)
            self.write(self.i + 2, value % 10)
        elif nn == 0x55:
            for k in range(x + 1):
                self.write(self.i + k, v[k])
        elif nn == 0x65:
            for k in range(x + 1):
                v[k] = self.read(self.i + k)
        else:
            raise Halt("invalid_opcode")

    def draw(self, vx, vy, rows):
        x0 = vx % DISPLAY_WIDTH
        y0 = vy % DISPLAY_HEIGHT
        collision = 0
        for row in range(rows):
            py = y0 + row
            if py >= DISPLAY_HEIGHT:
                break
            sprite = self.read(self.i + row)
            line = self.display[py]
            for bit in range(8):
                px = x0 + bit
                if px >= DISPLAY_WIDTH:
                    break
                if sprite & (0x80 >> bit):
                    if line[px]:
                        collision = 1
                    line[px] ^= 1
        self.v[0xF] = collision


def run(rom, cycles, keys=(), seed=1, frames_at=(), config=CANONICAL):
    """Run ``rom`` for up to ``cycles`` cycles and return the CLI result object.

    ``keys`` is a sequence of ``{"cycle": int, "key": int, "down": bool}``; an
    event for cycle c is applied immediately before cycle c executes (events for
    the same cycle apply in list order). ``frames_at`` lists cycles after which
    the display is captured.
    """
    machine = Chip8(rom, seed=seed, config=config)
    events = sorted(keys, key=lambda e: e["cycle"])  # stable: keeps file order per cycle
    wanted = set(frames_at)
    frames = {}
    next_event = 0
    for cycle in range(1, cycles + 1):
        while next_event < len(events) and events[next_event]["cycle"] <= cycle:
            event = events[next_event]
            machine.keys[event["key"]] = bool(event["down"])
            next_event += 1
        if not machine.step():
            break
        if cycle in wanted:
            frames[str(cycle)] = machine.frame()
    return {
        "halted": machine.halted,
        "halt_reason": machine.halt_reason,
        "cycles_run": machine.cycles,
        "state": machine.state(),
        "frames": frames,
        "final_frame": machine.frame(),
    }
