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
