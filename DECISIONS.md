# Decisions log

SPEC.md Section 11 says to stop and ask when the spec is ambiguous. The first
build session ran unattended (owner away ~10 hours, asked for maximum
progress), so ambiguities were resolved with the choice below, recorded here,
and written into `machine/canonical.md` so agents are graded only on behavior
the task text actually states. Items marked **[review]** are the ones most
worth a second look from the owner.

## Machine semantics (M0)

| # | Question | Decision | Why |
|---|---|---|---|
| D1 | When is "PC reaching 0x1000" checked? | At the start of each cycle, before fetch: `PC >= 0x1000` halts with `pc_out_of_bounds`. Any value `>= 0x1000` counts (e.g. `BNNN` to `0x10FE`). | Only check consistent with "12-bit effective" PC plus a halt reason. Covers BNNN and returns. |
| D2 | **[review]** Does a halting cycle count in `cycles_run`? Do timers tick on it? | No and no. `cycles_run` counts completed cycles only. | A halted instruction did not execute. Stated explicitly in the agent spec. |
| D3 | `pc` reported after an execute-time halt | The fetch increment has already happened (`pc` = faulting address + 2). Nothing else changes (nothing is pushed on overflow). | Follows "PC += 2 after fetch, before execute". |
| D4 | Fetch at odd address `0xFFF` | Low byte comes from `(PC+1) & 0xFFF` = `0x000`. | "All memory access is masked". |
| D5 | If N cycles finish with `PC >= 0x1000`, is that a halt? | No. The halt happens only when another cycle is attempted. | Keeps "runs exactly N cycles" literal. |
| D6 | `0000` and `00E1` etc. | No-ops (SYS), per the `0NNN` row. Running off the end of a program executes zeroed memory as no-ops until `pc_out_of_bounds`. | Literal reading of the table. |
| D7 | `DXYN` with `N = 0` | Draws nothing, `VF = 0`. | "Draw N rows" with N = 0; no SUPER-CHIP 16x16 in v1. |
| D8 | `DXYN` with X or Y = F | Coordinates are read before VF is written. | Matches "flag written after" rule. |
| D9 | Key events: same cycle, unsorted files, out-of-range cycles | Applied at the start of their cycle; same-cycle events in file order; files may be unsorted (stable sort by cycle). Test data only uses cycles >= 1. | Deterministic and easy to state. |
| D10 | `--frames-at` cycles that never complete | Omitted from `frames`. Duplicates recorded once. | A frame "after executing" a cycle that did not execute does not exist. |
| D11 | Seed range | `0 <= seed < 2^31`, used directly as LCG state. | Spec says "Initial state = seed". |
| D12 | Output formatting | Any valid JSON with the required keys; grader parses it, so whitespace/key order are irrelevant. Frames keyed by decimal cycle strings. | Matches Section 5.7 example. |

## Repository / process

| # | Question | Decision |
|---|---|---|
| R1 | **[review]** Repo is public, but `tests_data/hidden/` must stay hidden from agents. | Hidden tests are committed (they are deterministic outputs of `romgen/` anyway); each suite manifest and the reference source carry a canary GUID so they can be filtered from training corpora. Agents never see them: the sandbox gets only `tasks/<variant>/`, and has no network. If contamination matters more later, regenerate hidden tests with a new private seed (`romgen` takes the seed as an argument) and keep them out of git. |
| R2 | Grading container contents | Only the candidate's `emulator.py`, the test ROMs/key files, and a driver script. **Golden outputs never enter the grading container**; comparison happens on the host. This stops a candidate from reading the answers at grade time. |
| R3 | Commit identity | Commits use the owner's GitHub noreply address, not their personal email, since the repo is public. |

## Test generation (M1)

| # | Question | Decision |
|---|---|---|
| T1 | Is `cycles_run` part of a unit test's pass condition? | No. SPEC 6.4 lists `halted`, `halt_reason`, every `state` field and `final_frame`; `cycles_run` is not in `state`, so it is reported but not graded. (This also makes decision D2 low-stakes.) |
| T2 | How do unit tests make "VF unchanged" bugs visible? | Every unit test starts by loading all 16 registers with random values, VF always in 2..255, so any stray 0/1 flag write changes the final state. |
| T3 | Unit test count | 111 canonical (114 variant, adding 5XY1). Every opcode in Section 5.3 has 2-4 tests; halts cover all four reasons. |
| T4 | Program test count | 16 programs (spec says ~15): bounce, score_counter, timer_bar, key_menu, random_dots, nested_calls, font_grid, typing, scroller, memcopy, fibonacci, collision_counter, jump_table, shift_art, maze, pong. |
| T5 | Program checkpoints | 3-6 per program, drawn from the generator's RNG over the run, re-drawn until at least two distinct frames and at most one blank frame (so a checkpoint rarely lands in an erase/redraw gap). |
| T6 | Public set | 6 unit tests (one each from flow, alu, memory, display, timers, input) + 2 programs (score_counter, typing), all from a separate seed. Generation asserts no public test shares ROM bytes or run arguments with a hidden test. |
| T7 | Variant programs | The assembler's `SE`/`SNE` are semantic, so the same program generator emits 4XNN/3XNN on the variant machine and the program stays logically identical. Unit tests use raw opcodes, so they test the literal variant semantics. |
| T8 | Attributing variant failures | Each variant test is tagged with the changes it is sensitive to (re-running it with that one change reverted changes the graded output). At grading time, for each failed variant test the host re-runs the reference with every subset of the test's changes reverted and records the smallest subset that reproduces the candidate's output exactly: direct evidence the candidate used standard CHIP-8 behavior for those changes. (An earlier version stored single-change reverted goldens; that missed failures caused by several changes at once.) |

## Grader (M2)

| # | Question | Decision |
|---|---|---|
| G1 | How is the grading container hardened? | `--network none`, CPU/memory/pids limits, all capabilities dropped except those the driver needs to launch the candidate as an unprivileged user (`SETUID`, `SETGID`, `KILL`, `CHOWN`), `no-new-privileges`. The driver (root) streams results on its own stdout, which the candidate process cannot write to. Verified by `tests/test_grader.py::test_grading_container_isolates_candidate`. |
| G2 | Valid JSON but non-zero exit code? | Accepted: grading is on the JSON. A crash is only recorded when stdout is not a JSON object. |
| G3 | When is `--seed` / `--keys` / `--frames-at` passed? | Only when needed (seed != 1, keys present, checkpoints present), so the CLI defaults are part of what is tested (e.g. default seed 1). |
| G4 | Test parallelism | 2 tests at a time inside a 2-CPU container, so a 10 s timeout is not eaten by contention. Local (trusted) mode uses 8 workers. |
| G5 | Mutant set | 31 canonical mutants across all 9 families (the six from SPEC 7.1 plus common real-world quirks: COSMAC shift/logic quirks, BNNN VX quirk, FX1E VF quirk, wrap-vs-clip, FX0A highest key, ...) and 4 more on variant_v, plus 5 "revert" mutants on variant_v that each restore one standard CHIP-8 behavior. All detected in their own family. |
| G6 | Variant tests tagged by the graded fields only | A change "affects" a test only if reverting it changes what the grader compares (unit: halted, halt_reason, state, final_frame; program: checkpoint frames). |
| G7 | **[review]** Font relocation was weakly tested | A variant emulator that keeps the standard font at 0x050 *and* a matching FX29 draws correct glyphs, so only `I` values reveal it. Added font-in-memory unit tests (FX65 / DRW from the raw font address) and a `font_dump` program that copies font bytes out of memory, on both machines. |

## Sandbox and agent loop (M3)

| # | Question | Decision |
|---|---|---|
| H1 | Which APIs? | The loop is model-agnostic; each provider client keeps its native, append-only history: OpenAI-compatible Chat Completions (OpenRouter, Gemini's OpenAI endpoint, any compatible server), the OpenAI Responses API (so reasoning items are passed back between turns, which OpenAI's reasoning models expect), and the Anthropic Messages API through the official SDK (streaming, automatic prompt caching, thinking blocks preserved). Assistant turns are re-sent exactly as received so provider extras (thought signatures, reasoning items) survive. |
| H2 | **[review]** Default model lineup | `harness/models.yaml`: Claude Opus 5.5 (`effort: high`), GPT-6 Astra (Responses API, `reasoning.effort: high`), Gemini 3.1 Pro preview (`reasoning_effort: high`). IDs and prices were checked against the provider docs on 2026-10-11; cheaper alternatives are listed commented out. Each model runs at its provider's "high" reasoning setting for comparability. |
| H3 | Refusal fallbacks (Anthropic) | Off. A server-side fallback would silently grade a different model. A refusal ends the episode with `end_reason: refusal`. Can be enabled per model with `fallbacks: default`. |
| H4 | Budgets | 150 tool calls, 2 h wall clock, $30 per run (configurable). Every tool result ends with a short budget line (`[budget: 37/150 tool calls used, 23 min of 2 h 00 min elapsed]`) so models can pace themselves. Three consecutive turns without a tool call end the episode (each one first gets a nudge). |
| H5 | Context growth | No client-side history edits by default (Anthropic's preserved thinking requires append-only history, and all three default models have ~1M-token windows). OpenAI-compatible chat models can opt into eliding old tool outputs with `compact_after_tokens`. A context-overflow error ends the episode and the current `emulator.py` is graded. |
| H6 | Tool semantics | `bash` exit codes are reported in the output, not as tool errors; `read_file`/`write_file` paths are normalized and must stay inside `/workspace` (symlinks inside the agent's own container are not policed: it can `cat` anything there with `bash` anyway, and nothing secret is in it). `read_file` returns at most 100k characters. |
| H7 | Sandbox hardening | `--network none`, `--cap-drop ALL`, `no-new-privileges`, non-root `agent` user, 2 CPUs / 2 GB / 256 pids. Task files are copied in as a tar owned by `agent`; `reference/`, `tests_data/` and `grader/` never enter the image or container. Verified by `tests/test_harness.py`. |
| H8 | M3 "one model end to end" | **Blocked on API keys.** Verified instead with (a) a scripted agent (`harness/scripts/reference_agent.json`) that drives the real sandbox, transcript, grading and results pipeline, and (b) mock HTTP servers that check each provider client's exact request/response handling (including the Anthropic SDK's streaming path). |

## Report (M4 tooling)

| # | Question | Decision |
|---|---|---|
| P1 | Heatmap format | Markdown tables with colored squares plus the number (🟩 ≥ 95%, 🟨 ≥ 75%, 🟧 ≥ 40%, 🟥 below), so the report reads on GitHub with no image tooling. |
| P2 | "Most interesting failures" | Per model and variant, up to 3 distinct tests: near-misses first (checkpoint pixel accuracy ≥ 90% but not exact; these are the subtle bugs), then unit tests with a wrong final frame, then the worst checkpoint. PNGs are candidate / reference / diff (red = only candidate, green = only reference), written with a stdlib PNG encoder. |
| P3 | Demo report | `report/demo/REPORT.md` is built from synthetic "models" (reference + patched-in bugs) to show the format before real runs exist. It is clearly labelled as synthetic. |
