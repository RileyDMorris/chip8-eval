# CHIP-8 Eval — Results

No graded runs found yet. Run `python -m harness.run_eval` (needs API keys in `.env`).

## Grader validation (mutants)

Before any model runs, the grader is checked against deliberately broken copies of the reference emulator (`grader/mutants.py`). It must score the reference 100%, every mutant below 100%, and each mutant must lose points in the opcode family it broke.

### `canonical`: reference 100%, 31 mutants, validation passed

| Mutant | Bug | Family | Family pass rate | Overall | Families that lost points |
|---|---|---|---|---|---|
| `alu_8xy5_flag_gt` | 8XY5 sets VF with > instead of >= | alu | 96% | 99.6% | alu |
| `alu_flag_before_result` | 8XY4/5/6/7/E write VF before the result | alu | 81% | 97.8% | alu |
| `alu_shift_uses_vy` | 8XY6/8XYE shift VY into VX (COSMAC quirk) | alu | 85% | 96.7% | alu |
| `alu_logic_resets_vf` | 8XY1/2/3 reset VF to 0 (COSMAC quirk) | alu | 77% | 96.9% | alu, random |
| `display_wrap_not_clip` | sprites wrap around the edges instead of clipping | display | 82% | 93.3% | display |
| `display_no_start_wrap` | DXYN start position is not wrapped (off-screen sprites vanish) | display | 82% | 94.9% | display |
| `display_vf_untouched` | DXYN never writes the collision flag | display | 0% | 89.4% | display, memory |
| `display_cls_noop` | 00E0 does not clear the screen | display | 82% | 93.3% | display |
| `memory_fx55_increments_i` | FX55 leaves I = I + X + 1 | memory | 75% | 92.9% | memory |
| `memory_fx65_increments_i` | FX65 leaves I = I + X + 1 | memory | 8% | 95.1% | memory |
| `memory_bcd_reversed` | FX33 stores the digits ones-first | memory | 75% | 93.9% | memory |
| `memory_unmasked` | memory addresses are not masked to 12 bits | memory | 92% | 99.5% | memory |
| `timers_period_off_by_one` | timers tick every 9 cycles (7 on variant_v) | timers | 29% | 85.2% | input, timers |
| `timers_tick_before_execute` | timers tick at the start of the cycle instead of the end | timers | 71% | 95.4% | timers |
| `timers_st_frozen` | the sound timer never counts down | timers | 71% | 99.1% | timers |
| `random_ignores_seed` | CXNN ignores --seed (always seed 1) | random | 25% | 92.4% | random |
| `random_low_bits` | rand() returns the low byte of the state | random | 0% | 91.9% | random |
| `input_fx0a_highest_key` | FX0A picks the highest-numbered key | input | 90% | 99.0% | input |
| `input_key_unmasked` | EX9E/EXA1 index keys with VX instead of VX & 0xF | input | 90% | 99.5% | input |
| `input_skip_ops_swapped` | EX9E and EXA1 swapped | input | 40% | 92.1% | input |
| `flow_call_pushes_old_pc` | 2NNN pushes the address of the call itself | flow | 82% | 71.6% | flow, halt |
| `flow_bnnn_uses_vx` | BNNN adds VX instead of V0 (SUPER-CHIP quirk) | flow | 91% | 95.5% | flow, halt |
| `flow_3xnn_inverted` | 3XNN skips on the wrong condition | flow | 82% | 63.2% | flow, load, random, timers |
| `load_7xnn_sets_vf` | 7XNN writes a carry flag | load | 67% | 96.5% | flow, load, random, timers |
| `load_fx1e_sets_vf` | FX1E sets VF when I passes 0xFFF (Amiga quirk) | load | 75% | 98.7% | load |
| `load_i_12_bit` | FX1E wraps I at 12 bits | load | 92% | 99.6% | load |
| `load_fx29_other_base` | FX29 uses the other machine's font base | load | 83% | 78.2% | display, load |
| `halt_invalid_is_noop` | invalid opcodes are ignored | halt | 44% | 97.8% | halt |
| `halt_stack_limit_17` | stack overflow only at 17 entries | halt | 89% | 99.6% | halt |
| `halt_pc_wraps` | PC wraps at 0x1000 instead of halting | halt | 78% | 99.1% | halt |
| `halt_underflow_ignored` | 00EE with an empty stack is a no-op | halt | 89% | 99.6% | halt |

### `variant_v`: reference 100%, 35 mutants, validation passed

| Mutant | Bug | Family | Family pass rate | Overall | Families that lost points |
|---|---|---|---|---|---|
| `alu_8xy5_flag_gt` | 8XY5 sets VF with > instead of >= | alu | 97% | 99.6% | alu |
| `alu_flag_before_result` | 8XY4/5/6/7/E write VF before the result | alu | 83% | 97.8% | alu |
| `alu_shift_uses_vy` | 8XY6/8XYE shift VY into VX (COSMAC quirk) | alu | 86% | 96.6% | alu |
| `alu_logic_resets_vf` | 8XY1/2/3 reset VF to 0 (COSMAC quirk) | alu | 79% | 97.4% | alu |
| `display_wrap_not_clip` | sprites wrap around the edges instead of clipping | display | 73% | 92.1% | display |
| `display_no_start_wrap` | DXYN start position is not wrapped (off-screen sprites vanish) | display | 82% | 95.3% | display |
| `display_vf_untouched` | DXYN never writes the collision flag | display | 9% | 89.8% | display, memory |
| `display_cls_noop` | 00E0 does not clear the screen | display | 82% | 94.2% | display |
| `memory_fx55_increments_i` | FX55 leaves I = I + X + 1 | memory | 75% | 93.8% | memory |
| `memory_fx65_increments_i` | FX65 leaves I = I + X + 1 | memory | 8% | 95.3% | memory |
| `memory_bcd_reversed` | FX33 stores the digits ones-first | memory | 75% | 93.8% | memory |
| `memory_unmasked` | memory addresses are not masked to 12 bits | memory | 92% | 99.5% | memory |
| `timers_period_off_by_one` | timers tick every 9 cycles (7 on variant_v) | timers | 0% | 83.4% | input, timers |
| `timers_tick_before_execute` | timers tick at the start of the cycle instead of the end | timers | 71% | 96.4% | timers |
| `timers_st_frozen` | the sound timer never counts down | timers | 71% | 99.1% | timers |
| `random_ignores_seed` | CXNN ignores --seed (always seed 1) | random | 25% | 91.6% | random |
| `random_low_bits` | rand() returns the low byte of the state | random | 0% | 91.1% | random |
| `input_fx0a_highest_key` | FX0A picks the highest-numbered key | input | 90% | 99.0% | input |
| `input_key_unmasked` | EX9E/EXA1 index keys with VX instead of VX & 0xF | input | 90% | 99.5% | input |
| `input_skip_ops_swapped` | EX9E and EXA1 swapped | input | 40% | 90.3% | input |
| `flow_call_pushes_old_pc` | 2NNN pushes the address of the call itself | flow | 82% | 72.8% | flow, halt |
| `flow_bnnn_uses_vx` | BNNN adds VX instead of V0 (SUPER-CHIP quirk) | flow | 91% | 97.1% | flow, halt |
| `flow_3xnn_inverted` | 3XNN skips on the wrong condition | flow | 86% | 82.3% | flow |
| `load_fx1e_sets_vf` | FX1E sets VF when I passes 0xFFF (Amiga quirk) | load | 83% | 99.1% | load |
| `load_i_12_bit` | FX1E wraps I at 12 bits | load | 92% | 99.6% | load |
| `load_fx29_other_base` | FX29 uses the other machine's font base | load | 83% | 76.9% | display, load |
| `halt_invalid_is_noop` | invalid opcodes are ignored | halt | 44% | 97.8% | halt |
| `halt_stack_limit_17` | stack overflow only at 17 entries | halt | 89% | 99.6% | halt |
| `halt_pc_wraps` | PC wraps at 0x1000 instead of halting | halt | 78% | 99.1% | halt |
| `halt_underflow_ignored` | 00EE with an empty stack is a no-op | halt | 89% | 99.6% | halt |
| `revert_font_addr` | variant_v with the font at 0x050 (standard CHIP-8) | load | 83% | 94.1% | display, load, memory |
| `revert_skip_swap` | variant_v with standard 3XNN/4XNN | flow | 68% | 56.8% | flow, load, random, timers |
| `revert_add_vf` | variant_v with standard 7XNN (VF untouched) | load | 67% | 96.5% | flow, load, random, timers |
| `revert_timer_period` | variant_v with timers every 10 cycles | timers | 57% | 85.0% | timers |
| `revert_mul` | variant_v without the 5XY1 multiply | alu | 90% | 98.7% | alu |
