"""A tiny CHIP-8 assembler DSL that emits ROM bytes.

Mnemonics follow Cowgod's reference so programs read like ordinary CHIP-8
assembly::

    p = Program()
    p.LD(V0, 0x12)          # 6012
    p.ADD(V1, V2)           # 8124
    p.LD(I, "sprite")       # A--- (label resolved at assembly)
    p.DRW(V0, V1, 5)        # D015
    p.label("spin"); p.JP("spin")
    p.label("sprite"); p.DB(0xF0, 0x90, 0xF0)
    rom = p.assemble()

Mnemonics are *semantic*: ``SE``/``SNE`` with an immediate mean "skip if
equal"/"skip if not equal" and emit whichever opcode has that meaning in the
target machine (3XNN/4XNN, swapped in ``variant_v``). Use ``W(word)`` to emit a
specific raw opcode regardless of machine.
"""

from reference.chip8 import CANONICAL


class Reg:
    __slots__ = ("n",)

    def __init__(self, n):
        self.n = n

    def __repr__(self):
        return f"V{self.n:X}"


class Special:
    __slots__ = ("name",)

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return self.name


V = [Reg(n) for n in range(16)]
V0, V1, V2, V3, V4, V5, V6, V7, V8, V9, VA, VB, VC, VD, VE, VF = V
I = Special("I")
DT = Special("DT")
ST = Special("ST")
K = Special("K")
F = Special("F")          # font address of a digit (FX29)
B = Special("B")          # BCD (FX33)
MEM_I = Special("[I]")    # memory at I (FX55 / FX65)


class AsmError(Exception):
    pass


def _reg(x):
    if not isinstance(x, Reg):
        raise AsmError(f"expected a register, got {x!r}")
    return x.n


def _byte(value):
    if not isinstance(value, int) or not 0 <= value <= 0xFF:
        raise AsmError(f"expected a byte, got {value!r}")
    return value


class Program:
    def __init__(self, config=CANONICAL, origin=0x200):
        self.config = config
        self.origin = origin
        self.addr = origin
        self.items = []        # (address, bytes | callable(labels) -> int word)
        self.labels = {}
        self.instructions = 0  # count of instruction words emitted (not data)

    # -- layout --------------------------------------------------------------

    def label(self, name):
        if name in self.labels:
            raise AsmError(f"duplicate label {name!r}")
        self.labels[name] = self.addr
        return name

    def here(self):
        return self.addr

    def _word(self, word_or_fn):
        if isinstance(word_or_fn, int) and not 0 <= word_or_fn <= 0xFFFF:
            raise AsmError(f"bad word {word_or_fn!r}")
        self.items.append((self.addr, word_or_fn))
        self.addr += 2
        self.instructions += 1

    def _addr(self, target, labels):
        if isinstance(target, str):
            if target not in labels:
                raise AsmError(f"undefined label {target!r}")
            target = labels[target]
        if not isinstance(target, int) or not 0 <= target <= 0xFFF:
            raise AsmError(f"bad address {target!r}")
        return target

    def _with_addr(self, high, target):
        if isinstance(target, str):
            self._word(lambda labels: (high << 12) | self._addr(target, labels))
        else:
            self._word((high << 12) | self._addr(target, {}))

    def DB(self, *values):
        data = bytes(_byte(v) for v in values)
        self.items.append((self.addr, data))
        self.addr += len(data)

    def W(self, word):
        """Emit a raw 16-bit word (any opcode, valid or not)."""
        self._word(word)

    def ALIGN(self):
        if self.addr % 2:
            self.DB(0)

    def SPIN(self):
        """Tight loop: jump to self."""
        self._with_addr(0x1, self.addr)

    def assemble(self):
        out = bytearray()
        for addr, item in self.items:
            assert addr == self.origin + len(out)
            if isinstance(item, bytes):
                out += item
            else:
                word = item(self.labels) if callable(item) else item
                out += word.to_bytes(2, "big")
        return bytes(out)

    def address_of(self, name):
        return self.labels[name]

    # -- instructions --------------------------------------------------------

    def CLS(self):
        self._word(0x00E0)

    def RET(self):
        self._word(0x00EE)

    def SYS(self, nnn):
        if nnn in (0x0E0, 0x0EE):
            raise AsmError("SYS 0E0/0EE would encode CLS/RET")
        self._word(nnn & 0xFFF)

    def JP(self, a, b=None):
        if b is None:
            self._with_addr(0x1, a)
        else:
            if not (isinstance(a, Reg) and a.n == 0):
                raise AsmError("JP with offset must use V0")
            self._with_addr(0xB, b)

    def CALL(self, target):
        self._with_addr(0x2, target)

    def SE(self, x, y):
        if isinstance(y, Reg):
            self._word(0x5000 | _reg(x) << 8 | y.n << 4)
        else:
            high = 0x4 if self.config.skip_swapped else 0x3
            self._word(high << 12 | _reg(x) << 8 | _byte(y))

    def SNE(self, x, y):
        if isinstance(y, Reg):
            self._word(0x9000 | _reg(x) << 8 | y.n << 4)
        else:
            high = 0x3 if self.config.skip_swapped else 0x4
            self._word(high << 12 | _reg(x) << 8 | _byte(y))

    def LD(self, a, b):
        if isinstance(a, Reg):
            x = a.n
            if isinstance(b, Reg):
                self._word(0x8000 | x << 8 | b.n << 4)
            elif b is DT:
                self._word(0xF007 | x << 8)
            elif b is K:
                self._word(0xF00A | x << 8)
            elif b is MEM_I:
                self._word(0xF065 | x << 8)
            else:
                self._word(0x6000 | x << 8 | _byte(b))
        elif a is I:
            self._with_addr(0xA, b)
        elif a is DT:
            self._word(0xF015 | _reg(b) << 8)
        elif a is ST:
            self._word(0xF018 | _reg(b) << 8)
        elif a is F:
            self._word(0xF029 | _reg(b) << 8)
        elif a is B:
            self._word(0xF033 | _reg(b) << 8)
        elif a is MEM_I:
            self._word(0xF055 | _reg(b) << 8)
        else:
            raise AsmError(f"bad LD operands {a!r}, {b!r}")

    def ADD(self, a, b):
        if a is I:
            self._word(0xF01E | _reg(b) << 8)
        elif isinstance(b, Reg):
            self._word(0x8004 | _reg(a) << 8 | b.n << 4)
        else:
            self._word(0x7000 | _reg(a) << 8 | _byte(b))

    def _alu(self, n, x, y):
        self._word(0x8000 | _reg(x) << 8 | _reg(y) << 4 | n)

    def OR(self, x, y):
        self._alu(0x1, x, y)

    def AND(self, x, y):
        self._alu(0x2, x, y)

    def XOR(self, x, y):
        self._alu(0x3, x, y)

    def SUB(self, x, y):
        self._alu(0x5, x, y)

    def SHR(self, x, y=None):
        self._alu(0x6, x, y if y is not None else V0)

    def SUBN(self, x, y):
        self._alu(0x7, x, y)

    def SHL(self, x, y=None):
        self._alu(0xE, x, y if y is not None else V0)

    def MUL(self, x, y):
        """Variant-only 5XY1: VX = low byte of VX*VY, VF = high byte."""
        if not self.config.mul_opcode:
            raise AsmError("MUL (5XY1) is not an instruction on this machine")
        self._word(0x5001 | _reg(x) << 8 | _reg(y) << 4)

    def RND(self, x, nn):
        self._word(0xC000 | _reg(x) << 8 | _byte(nn))

    def DRW(self, x, y, n):
        if not 0 <= n <= 0xF:
            raise AsmError(f"bad sprite height {n}")
        self._word(0xD000 | _reg(x) << 8 | _reg(y) << 4 | n)

    def SKP(self, x):
        self._word(0xE09E | _reg(x) << 8)

    def SKNP(self, x):
        self._word(0xE0A1 | _reg(x) << 8)
