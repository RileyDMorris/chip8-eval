# Task: build an emulator for a modified CHIP-8

Your working directory is `/workspace`. It contains:

| Path | What it is |
|---|---|
| `machine_spec.md` | The complete specification of the machine to emulate, including the exact command-line interface and JSON output format. Read all of it. |
| `public_tests/` | 8 example tests (ROMs, key scripts, and expected outputs). |
| `run_public_tests.py` | Runs your emulator on the public tests and reports pass/fail: `python run_public_tests.py` (add `-v` for every mismatch). |

## Important: this is not standard CHIP-8

The machine in `machine_spec.md` is a **deliberately modified CHIP-8**. Some
instructions and behaviors differ from standard CHIP-8 and from any CHIP-8
emulator you may have seen. **The spec overrides anything you already know
about CHIP-8.** Implement what the spec says, line by line, even where it
looks like a mistake.

## What to build

Write the emulator as a **single file, `/workspace/emulator.py`**, that
implements `machine_spec.md` exactly, including the command-line contract in
its Section 8.

Rules:

- Python 3.12, **standard library only**. There is no network access and no
  package installation.
- Only `/workspace/emulator.py` is graded. Nothing else you write is kept.
- Everything is headless. Do not build a GUI or produce sound.

## How you are graded

Your `emulator.py` is copied into a fresh container and run on a hidden test
suite: about 110 small per-instruction tests (graded on the final machine state
and display) and about 16 longer programs (graded on display frames at
checkpoint cycles, with scripted key presses). Each run has a 10 second
timeout; the longest tests run about 10,000 cycles. Outputs are compared
exactly against a reference implementation of `machine_spec.md`.

The public tests are only a sample. The hidden tests cover every instruction,
every halt condition, and the edge cases the spec describes, so check your work
against the spec itself, not just the public tests.

## Finishing

When you are done, call the `submit` tool. If you run out of budget first,
whatever is in `/workspace/emulator.py` at that point is graded, so keep a
working version saved as you go.
