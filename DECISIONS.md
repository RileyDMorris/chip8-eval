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
| R1 | **[review]** Repo is public, but `tests_data/hidden/` must stay hidden from agents. | Hidden tests are committed (they are deterministic outputs of `romgen/` anyway) and every generated file carries a canary string so they can be filtered from training corpora. Agents never see them: the sandbox gets only `tasks/<variant>/`, and has no network. If contamination matters more later, regenerate hidden tests with a new private seed (`romgen` takes the seed as an argument) and keep them out of git. |
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
| T8 | Attributing variant failures | Each variant test is tagged with the changes it is sensitive to (re-running it with that one change reverted gives a different output), and the reverted outputs are stored in `golden_revert/<change>/`. A candidate output that matches a reverted golden is direct evidence it used standard CHIP-8 behavior for that change. |
