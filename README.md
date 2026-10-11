# CHIP-8 Eval

**A benchmark that measures how well AI coding agents can build a CHIP-8 emulator from a written
specification**, alone, inside a sandbox with no internet. It is a scaled-down cousin of
Mechanize's GBA Eval: small enough to run on a laptop, strict enough that "almost right" shows up
in the score, and instrumented so you can see *where* each model breaks.

> **Status (2026-10-11):** the environment, hidden test suites, grader (validated against 66
> mutants), agent harness, variant spec and report generator are built and tested
> (`pytest`: 161 tests, including Docker isolation tests). **No real model has been evaluated
> yet**: that needs API keys (see [Running a real eval](#running-a-real-eval)). The report
> format is shown with synthetic data in [`report/demo/REPORT.md`](report/demo/REPORT.md).

## What it measures

An agent gets a task prompt, a [machine specification](machine/canonical.md), 8 public example
tests and a self-check script. It has `bash`, `read_file`, `write_file` and `submit` tools in a
Docker container with no network and only the Python 3.12 standard library, and a budget of 150
tool calls and 2 hours. Whatever is in `/workspace/emulator.py` at the end is graded.

The grader runs the emulator on a **hidden** suite and compares its output exactly against a
reference implementation:

| Part | What | Weight |
|---|---|---|
| Unit tests | 113 small programs, 2–4 per opcode, graded on final registers, `I`, `PC`, stack, timers and display | 0.5 |
| Program tests | 17 longer programs (bouncing ball with collisions, BCD score counter, key-driven menu, pong, a random maze, nested subroutine rendering, ...) with scripted key presses, graded on 3–6 display frames at checkpoint cycles (76 checkpoints) | 0.4 |
| Robustness | share of tests where the emulator produced valid JSON within the 10 s timeout | 0.1 |

Unit tests are grouped into nine opcode families (`flow`, `alu`, `load`, `memory`, `display`,
`timers`, `input`, `random`, `halt`) so the score has partial credit and the report shows which
parts of the machine each model gets wrong. Each model runs K = 3 times; the report gives the mean
and min–max.

### The memorization test

CHIP-8 is everywhere in training data, so a model can score well by recalling a typical
emulator instead of reading the spec. The **`variant_v`** task is the same machine with five
deliberate changes ([spec](machine/variant_v.md)), and the prompt says plainly that the spec
overrides prior knowledge:

1. the font lives at `0x000`, so `FX29` sets `I = 5 * VX`;
2. `3XNN` skips if *not* equal and `4XNN` skips if equal;
3. `7XNN` sets `VF` on overflow;
4. timers tick every 8 cycles instead of 10;
5. a new opcode, `5XY1`, multiplies (`VF` = high byte).

The headline number is the **memorization gap**: canonical score minus variant score. The grader
also attributes each variant failure: it re-runs the reference with every subset of the changes
the test depends on reverted to standard CHIP-8 behavior, and records the smallest subset that
reproduces the candidate's output *exactly*. A failure explained that way is direct evidence the
model followed its prior instead of the document in front of it.

## How it works

```
 tasks/<variant>/  ──copy──▶  agent sandbox (Docker, --network none, non-root, no keys)
 prompt, spec, public tests          │  model API calls happen on the host
                                     ▼
                              /workspace/emulator.py
                                     │  copied alone into a fresh container
                                     ▼
 tests_data/hidden/<variant>/ ──inputs only──▶ grading container ──raw outputs──▶ host compares
 (ROMs, key scripts, goldens)                  (candidate runs as an                with goldens
                                                unprivileged user)                → scores.json
                                                                                  → report/REPORT.md
```

| Directory | Contents |
|---|---|
| [`machine/`](machine/) | The specs agents see: [`canonical.md`](machine/canonical.md), [`variant_v.md`](machine/variant_v.md) |
| [`reference/`](reference/) | Reference emulator (both machines via a `Config`), CLI, and a bundler that makes single-file emulators |
| [`romgen/`](romgen/) | A small CHIP-8 assembler DSL, unit and program test generators, suite builder |
| [`tests_data/`](tests_data/) | Generated hidden and public suites with golden outputs |
| [`tasks/`](tasks/) | Exactly what an agent's sandbox receives |
| [`grader/`](grader/) | Grading driver and container runner, scoring, mutant validation |
| [`harness/`](harness/) | Sandbox, agent loop, provider clients, eval orchestrator, `models.yaml` |
| [`report/`](report/) | Report generator, frame-diff renderer, demo report |
| [`docker/`](docker/) | Agent and grader images (`python:3.12-slim`) |
| [`DECISIONS.md`](DECISIONS.md) | Every place the spec was ambiguous and how it was resolved |

## Design decisions

**Determinism.** Time is counted in executed instructions, never wall-clock: timers tick after
every 10th cycle, key presses are scripted by cycle number, and `CXNN` uses a fixed linear
congruential generator with a seed. The same ROM and inputs always produce byte-identical output
(tested). Test generation is deterministic too: regenerating from the fixed seeds reproduces the
committed suites byte for byte (also tested).

**A spec that can actually be graded exactly.** Exact-match grading is only fair if the spec pins
down every observable detail. Writing the reference surfaced a dozen questions the original brief
left open: when does a halting instruction count as a cycle, what is `PC` after a fault, does
`BNNN` past `0xFFF` wrap or halt, what does `DXY0` do, what happens to a frame checkpoint after a
halt. Each was decided, written into the agent-facing spec, and logged in
[`DECISIONS.md`](DECISIONS.md). The reference's 109 hand-written tests derive their expected values
from the spec text, not from running the implementation.

**Hidden tests stay hidden.** The agent's container receives only `tasks/<variant>/`. The
public tests come from a different seed, and generation asserts that no public test shares ROM
bytes or run arguments with a hidden one. Golden outputs never enter the grading container
either: it gets the candidate, the ROMs and key scripts, and a driver; the host compares outputs.
So a candidate cannot find the answers at grading time.

**Hard to game.** In the grading container the candidate runs as an unprivileged user with no
network, cannot write the driver's files or signal it, and results travel on the driver's own
stdout. A dedicated test runs an adversarial "emulator" that tries all of this. The CLI defaults
(`--seed` omitted means seed 1, and so on) are part of what is graded.

**The grader is validated before any model runs.** [`grader/mutants.py`](grader/mutants.py)
builds 31 broken copies of the reference for the canonical machine and 35 for the variant, each
with one realistic bug: the classic COSMAC shift and logic quirks, `BNNN` using `VX`, sprites
wrapping instead of clipping, `FX55` advancing `I`, timers off by one cycle, a seed that is
ignored, plus five "revert" mutants that each restore one standard CHIP-8 behavior on the variant.
The reference must score 100%, every mutant less than 100%, and every mutant must lose points in
the family it broke. All do; the table is in the report.

**Variant tests are logically equivalent programs.** The assembler's `SE`/`SNE` mnemonics are
semantic, so the same program generator emits `4XNN` where the variant machine needs it. A variant
program is the same program for a different machine, not a broken one. Unit tests use raw opcodes
so they test the literal variant semantics.

**A minimal, model-agnostic harness.** Each provider client keeps the conversation in its native
format and never edits history, so reasoning items, thinking blocks and thought signatures are
sent back exactly as received. The loop shows the remaining budget after every tool result, and
the same tools, prompt and budgets apply to every model. Clients: OpenAI Responses API, OpenAI
compatible Chat Completions (Gemini, OpenRouter, anything compatible), and the Anthropic Messages
API through the official SDK (streaming, prompt caching).

## Results

No model has been run yet. Once runs exist, `python -m report.make_report` writes
[`report/REPORT.md`](report/REPORT.md) with:

- overall, unit, program and robustness scores per model (mean, min–max, cost, tool calls);
- a per-family heatmap table and an opcode-level failure table;
- program-by-program checkpoint results;
- the most common failing tests and which state fields were wrong;
- frame diffs (candidate | reference | diff) for the most informative failures;
- the memorization gap and per-change attribution;
- the grader validation (mutant) results.

[`report/demo/REPORT.md`](report/demo/REPORT.md) shows the full format with three synthetic
"models" built from patched reference emulators. In two of its three variant runs, the
`demo-memorizer` model is simply a correct *standard* CHIP-8 emulator, which is what a model that
ignores the spec would submit: those runs score 50.3% on the variant (against about 100% on
canonical), and every one of their variant failures is attributed to a reverted change.

### Findings so far (from building and validating the benchmark)

- **The `3XNN`/`4XNN` swap dominates the variant.** Every non-trivial program branches with skips,
  so this one memorized behavior breaks nearly every variant program: an emulator that is correct
  except for this single change matches only 2 of the 73 variant checkpoints (program score 3%).
- **Relocating the font is nearly invisible to programs.** An emulator that keeps the standard
  font at `0x050` *and* a matching `FX29` draws exactly the right glyphs; only the value of `I`
  gives it away. The first version of the suite barely caught this, so font-in-memory unit tests
  and a `font_dump` program (which reads font bytes directly) were added.
- **Many variant failures have more than one cause.** In a program that depends on both the skip
  swap and the timer period, reverting only one change usually does not reproduce a standard
  CHIP-8 emulator's output. Comparing against single-change reverts explained only 68 of that
  emulator's 81 skip-swap failures; searching subsets of changes explains all 81.

## Limitations

- **Contamination.** This repository is public, including the hidden suites and the reference.
  Agents can't see them during a run (no network), but future training data could. The suite
  manifests and the reference carry a canary GUID. For a clean evaluation, regenerate the hidden
  suites with new seeds (`romgen/generate.py`) and keep them private.
- **Small sample.** K = 3 runs per model gives a rough spread, not tight confidence intervals.
- **One language, one harness.** Candidates must be single-file, stdlib-only Python. Results
  reflect this minimal scaffold as well as the model; an off-the-shelf coding agent in the same
  sandbox could do better or worse (see the extensions below).
- **The spec is ours.** Where CHIP-8 interpreters disagree (shift quirks, `FX55` and `I`, clipping)
  the spec picks one behavior and states it. Tests check the spec, not "real" CHIP-8.
- **Coverage.** No sound output, no SUPER-CHIP/XO-CHIP. Programs are generated by one author's
  assembler, so idioms are less varied than real games.
- **Timeouts depend on hardware.** The reference runs every test in about 0.1 s against a 10 s
  limit, so only a pathologically slow emulator should be affected.

## Running it

Requirements: Python 3.12 and Docker.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest            # ~1.5 min; Docker tests are skipped if Docker is down
```

Useful commands (run from the repo root with the venv's `python`):

| Command | What it does |
|---|---|
| `python -m romgen.generate` | Regenerate hidden and public suites and task directories |
| `python -m grader.mutants` | Validate the grader against all mutants (reference = 100%, every mutant caught) |
| `python -m grader.grade --emulator path/emulator.py --variant canonical` | Grade any emulator in a fresh grading container (`--local` for trusted code) |
| `python -m harness.run_eval --models scripted-reference --runs 1` | Full pipeline smoke test with a scripted agent; no API key needed |
| `python -m report.demo` | Rebuild the synthetic demo report |
| `python -m report.make_report` | Build `report/REPORT.md` from `results/runs/` |

### Running a real eval

1. Put keys in `.env` (copy [`.env.example`](.env.example)); only the ones for the models you run
   are needed. Keys stay on the host and are never passed into a sandbox.
2. Check model IDs, prices and budgets in [`harness/models.yaml`](harness/models.yaml). The
   defaults are Claude Opus 5.5, GPT-6 Astra and Gemini 3.1 Pro (preview), each at its provider's
   "high" reasoning setting, with a $30 cost cap per run.
3. Preflight (one tiny request per model, which catches bad keys, model IDs or parameters):
   `python -m harness.preflight`
4. Run: `python -m harness.run_eval --parallel 3` (3 models × 3 runs × 2 variants = 18 runs).
   Runs are resumable; finished runs are skipped.
5. Report: `python -m report.make_report`.

Each run leaves `results/runs/<run_id>/` with `transcript.jsonl` (every message and tool call),
`emulator.py`, `outputs.json`, `scores.json` and `meta.json`.

## Possible extensions

- Run agent sandboxes as Kubernetes Jobs for parallelism.
- A small viewer that replays a candidate's frames next to the reference.
- A "vendor agent" track: off-the-shelf coding agents in the same sandbox, to separate model
  ability from scaffolding.
- A SUPER-CHIP tier if models saturate the canonical task.
