# CHIP-8 Machine Specification (modified machine "V")

This document is the complete and authoritative definition of the machine you
must emulate. **This is a modified CHIP-8.** Several behaviors deliberately
differ from standard CHIP-8 and from every CHIP-8 interpreter you may know.
Wherever this document differs from prior knowledge, **this document wins**.
Your emulator is graded by comparing its output, exactly, against a reference
implementation of this document.

## 1. Machine state

| Component | Definition |
|---|---|
| Memory | 4096 bytes, addresses `0x000`–`0xFFF`. Every memory read or write (instruction fetch, sprite data, `FX33`, `FX55`, `FX65`) uses `address & 0xFFF`. Memory is all writable. |
| Program | ROM bytes are loaded starting at `0x200`. ROMs are at most 3584 bytes. All other bytes start as `0x00` except the font. |
| Font | 16 sprites for hex digits `0`–`F`, 5 bytes each, stored at `0x000`–`0x04F` (digit `d` starts at `0x000 + 5*d`). Bytes are listed in Section 7. |
| Registers | `V0`–`VF`, 8-bit, all start at 0. |
| `I` | 16-bit index register, starts at 0. |
| `PC` | Program counter, starts at `0x200`. |
| Timers | Delay timer `DT` and sound timer `ST`, 8-bit, start at 0. |
| Stack | Holds up to 16 return addresses. `SP` = number of entries, starts at 0. |
| Display | 64×32 monochrome pixels, all off at start. `(0,0)` is the top-left; x grows right, y grows down. |
| Keypad | 16 keys `0x0`–`0xF`, all up at start. |

## 2. Execution and timing

Time is measured only in **cycles**. There is no wall-clock timing anywhere.
Cycles are numbered from 1. Cycle `c` does the following, in order:

1. Apply every key event scheduled for cycle `c` (Section 5.2).
2. If `PC >= 0x1000`, the machine halts with reason `pc_out_of_bounds` (Section 4).
3. Fetch the 2-byte big-endian opcode: high byte at `PC & 0xFFF`, low byte at `(PC + 1) & 0xFFF`.
4. `PC = PC + 2`.
5. Execute the opcode (Section 3). Execution may halt the machine (Section 4).
6. If `c % 8 == 0`: if `DT > 0`, `DT -= 1`; if `ST > 0`, `ST -= 1`.
7. If `c` is a requested frame checkpoint, record the display.

A cycle that halts (step 2 or step 5) is **not** counted as run: steps 6 and 7
do not happen for it and it is not included in `cycles_run`.

The sound timer only counts down; no sound is produced.

## 3. Instructions

Notation: `X`, `Y`, `N` are 4-bit fields, `NN` an 8-bit field, `NNN` a 12-bit
field of the opcode. "Skip" means `PC = PC + 2` (on top of the increment at
fetch). All arithmetic on `V` registers is modulo 256.

Wherever an instruction says "then VF = ...", the flag is written **after**
the result register. If `X` is `F`, the flag value is what remains in `VF`.
Instructions that read `VX`/`VY` read their values before anything is written.

| Opcode | Behavior |
|---|---|
| `00E0` | Clear the display (all pixels off). |
| `00EE` | Return: if the stack is empty, halt with `stack_underflow`. Otherwise pop the top entry into `PC`. |
| `0NNN` | Any other opcode starting with `0` (including `0000`) is SYS: it does nothing. |
| `1NNN` | `PC = NNN`. |
| `2NNN` | Call: if the stack already holds 16 entries, halt with `stack_overflow`. Otherwise push `PC` (already advanced past this instruction) and set `PC = NNN`. |
| `3XNN` | Skip if `VX != NN`. |
| `4XNN` | Skip if `VX == NN`. |
| `5XY0` | Skip if `VX == VY`. |
| `5XY1` | Multiply: `product = VX * VY`; `VX = product & 0xFF`, then `VF = product >> 8` (the high byte). |
| `6XNN` | `VX = NN`. |
| `7XNN` | `VX = (VX + NN) & 0xFF`, then `VF = 1` if `VX + NN > 0xFF` (original value) else `0`. |
| `8XY0` | `VX = VY`. |
| `8XY1` | `VX = VX OR VY`. VF unchanged (unless X is F). |
| `8XY2` | `VX = VX AND VY`. VF unchanged (unless X is F). |
| `8XY3` | `VX = VX XOR VY`. VF unchanged (unless X is F). |
| `8XY4` | `VX = (VX + VY) & 0xFF`, then `VF = 1` if `VX + VY > 0xFF` (original values) else `0`. |
| `8XY5` | `VX = (VX - VY) & 0xFF`, then `VF = 1` if original `VX >= VY` else `0`. |
| `8XY6` | `VX = VX >> 1`, then `VF` = the bit shifted out (original `VX & 1`). `VY` is ignored. |
| `8XY7` | `VX = (VY - VX) & 0xFF`, then `VF = 1` if original `VY >= VX` else `0`. |
| `8XYE` | `VX = (VX << 1) & 0xFF`, then `VF` = the bit shifted out (original `VX >> 7`). `VY` is ignored. |
| `9XY0` | Skip if `VX != VY`. |
| `ANNN` | `I = NNN`. |
| `BNNN` | `PC = NNN + V0` (no masking; a result `>= 0x1000` halts on the next cycle, see Section 4). |
| `CXNN` | `VX = rand() & NN` (Section 6). |
| `DXYN` | Draw a sprite (Section 5.1). |
| `EX9E` | Skip if key `VX & 0xF` is down. |
| `EXA1` | Skip if key `VX & 0xF` is up. |
| `FX07` | `VX = DT`. |
| `FX0A` | Wait for a key (Section 5.3). |
| `FX15` | `DT = VX`. |
| `FX18` | `ST = VX`. |
| `FX1E` | `I = (I + VX) & 0xFFFF`. VF unchanged (unless X is F). |
| `FX29` | `I = 5 * (VX & 0xF)` (address of the font sprite for digit `VX & 0xF`). |
| `FX33` | Store the decimal digits of `VX`: hundreds at `I`, tens at `I+1`, ones at `I+2` (addresses masked with `& 0xFFF`). `I` unchanged. |
| `FX55` | Store `V0`..`VX` (inclusive) to memory `I`..`I+X` (each address masked). `I` unchanged. |
| `FX65` | Load `V0`..`VX` (inclusive) from memory `I`..`I+X` (each address masked). `I` unchanged. |

**Every opcode not listed above is invalid** and halts the machine with
`invalid_opcode`. That includes `5XYN` with `N` other than `0` or `1`, `8XYN` with `N` in
`8`–`D` or `F`, `9XYN` with `N != 0`, `EXNN` other than `9E`/`A1`, and `FXNN`
other than `07 0A 15 18 1E 29 33 55 65`.

## 4. Halting

The machine halts, and stops running cycles, when:

| Reason | When |
|---|---|
| `invalid_opcode` | An invalid opcode is executed (step 5). |
| `stack_overflow` | `2NNN` executes while the stack holds 16 entries. Nothing is pushed. |
| `stack_underflow` | `00EE` executes while the stack is empty. |
| `pc_out_of_bounds` | At step 2 of a cycle, `PC >= 0x1000`. |

On halt, the state reported is the state at the moment of the halt. For a halt
during execution, `PC` has already been advanced by 2 at fetch and the
instruction made no other change. For `pc_out_of_bounds`, nothing was fetched.

Reaching `PC >= 0x1000` is not itself a halt: it only halts if another cycle is
attempted. If the requested number of cycles runs out first, `halted` is false.

## 5. Display, keys, and key waiting

### 5.1 Drawing (`DXYN`)

1. `x0 = VX % 64`, `y0 = VY % 32` (the start position wraps).
2. For each row `r` in `0`..`N-1`: the sprite byte is memory at `(I + r) & 0xFFF`;
   bit 7 (MSB) is the leftmost pixel at `x0`, bit 0 is at `x0 + 7`.
3. Pixels with `x >= 64` or `y >= 32` are **clipped** (not drawn, never wrapped).
4. Each sprite bit that is 1 XORs the display pixel at that position.
5. Then `VF = 1` if any pixel was turned from on to off, else `VF = 0`.

`N = 0` draws nothing and sets `VF = 0`.

### 5.2 Key events

Key presses are scripted as events `{"cycle": c, "key": k, "down": true|false}`.
An event for cycle `c` is applied at the start of cycle `c` (step 1), before
that cycle's instruction executes. A key stays in the state the last applied
event put it in. Multiple events for the same cycle are applied in the order
they appear in the file. Event cycles are always `>= 1`.

### 5.3 Waiting for a key (`FX0A`)

If at least one key is down when `FX0A` executes, `VX` = the lowest-numbered
key that is down, and execution continues with the next instruction. If no key
is down, `PC = PC - 2` so the same `FX0A` executes again next cycle. Cycles keep
counting and timers keep ticking while waiting.

## 6. Random numbers (`CXNN`)

A 31-bit linear congruential generator. Initially `state = seed` (the `--seed`
argument, default 1; always `0 <= seed < 2^31`). Each call to `rand()`:

```
state = (state * 1103515245 + 12345) % 2**31
return (state >> 16) & 0xFF
```

`CXNN` calls `rand()` exactly once.

## 7. Font bytes

```
0: F0 90 90 90 F0    4: 90 90 F0 10 10    8: F0 90 F0 90 F0    C: F0 80 80 80 F0
1: 20 60 20 20 70    5: F0 80 F0 10 F0    9: F0 90 F0 10 F0    D: E0 90 90 90 E0
2: F0 10 F0 80 F0    6: F0 80 F0 90 F0    A: F0 90 F0 90 90    E: F0 80 F0 80 F0
3: F0 10 F0 10 F0    7: F0 10 20 40 40    B: E0 90 E0 90 E0    F: F0 80 F0 80 80
```

## 8. Command-line contract

Your emulator is a single file, `emulator.py`, run with Python 3.12 using only
the standard library:

```
python emulator.py --rom PATH --cycles N [--keys KEYS.json] [--seed S] [--frames-at C1,C2,...]
```

- `--rom`: path to the ROM file (raw bytes).
- `--cycles`: run cycles `1..N` (`N >= 0`), stopping early if the machine halts.
- `--keys`: optional JSON file containing a list of key events (Section 5.2). Events may be listed in any cycle order.
- `--seed`: optional RNG seed (Section 6), default 1.
- `--frames-at`: optional comma-separated cycle numbers. After each listed cycle
  completes, record the display. Listed cycles that never complete (beyond `N`
  or at/after a halt) are left out. Duplicates are recorded once.

The emulator prints **exactly one JSON object** to stdout and nothing else
(anything on stderr is ignored), then exits with status 0:

```json
{
  "halted": false,
  "halt_reason": null,
  "cycles_run": 1000,
  "state": {"v": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], "i": 0, "pc": 512, "sp": 0, "stack": [], "dt": 0, "st": 0},
  "frames": {"500": ["<row 0>", "...", "<row 31>"]},
  "final_frame": ["<row 0>", "...", "<row 31>"]
}
```

| Field | Meaning |
|---|---|
| `halted` | `true` if the machine halted, else `false`. |
| `halt_reason` | One of `"invalid_opcode"`, `"stack_overflow"`, `"stack_underflow"`, `"pc_out_of_bounds"`, or `null` when not halted. |
| `cycles_run` | Number of cycles that completed (a halting cycle does not count). |
| `state.v` | List of the 16 register values `V0`..`VF` as integers. |
| `state.i`, `state.pc`, `state.dt`, `state.st` | Integers. `pc` is the raw value, which can be `>= 0x1000` after a `pc_out_of_bounds` halt. |
| `state.sp` | Number of stack entries. |
| `state.stack` | The stack entries as integers, bottom (oldest) first; length `sp`. |
| `frames` | Object mapping each recorded checkpoint cycle, as a decimal string, to a frame. `{}` if none. |
| `final_frame` | The display when the run ended. |

A frame is a list of 32 strings (row 0 = top), each 64 characters of `0`
(pixel off) or `1` (pixel on), column 0 first.

All numbers are plain JSON integers (decimal). Key order and whitespace in the
JSON do not matter.
