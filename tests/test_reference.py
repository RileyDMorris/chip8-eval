"""Hand-written tests for the reference emulator.

Every expected value here is derived by hand from the spec text (SPEC.md
Section 5 / Section 8), not by running the implementation. ROMs are assembled
from raw opcode words so these tests do not depend on romgen.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from reference.chip8 import CANONICAL, VARIANT_V, run

ROOT = Path(__file__).resolve().parent.parent
BLANK_ROW = "0" * 64


def rom(*words):
    return b"".join(w.to_bytes(2, "big") for w in words)


def go(words, cycles, config=CANONICAL, **kw):
    return run(rom(*words), cycles, config=config, **kw)


def regs(result):
    return result["state"]["v"]


def lcg_sequence(seed, count):
    """Independent restatement of SPEC 5.4."""
    state = seed
    out = []
    for _ in range(count):
        state = (state * 1103515245 + 12345) % 2**31
        out.append((state >> 16) & 0xFF)
    return out


# ---------------------------------------------------------------- initial state

def test_initial_state_with_zero_cycles():
    r = go([0x1200], 0)
    assert r["halted"] is False
    assert r["halt_reason"] is None
    assert r["cycles_run"] == 0
    assert r["state"] == {"v": [0] * 16, "i": 0, "pc": 0x200, "sp": 0, "stack": [], "dt": 0, "st": 0}
    assert r["frames"] == {}
    assert r["final_frame"] == [BLANK_ROW] * 32


def test_output_shape():
    r = go([0x1200], 5, frames_at=[1, 3])
    assert set(r) == {"halted", "halt_reason", "cycles_run", "state", "frames", "final_frame"}
    assert set(r["state"]) == {"v", "i", "pc", "sp", "stack", "dt", "st"}
    assert list(r["frames"]) == ["1", "3"]
    for frame in [r["final_frame"], *r["frames"].values()]:
        assert len(frame) == 32 and all(len(row) == 64 for row in frame)


# ---------------------------------------------------------------- flow

def test_0nnn_sys_is_noop():
    r = go([0x0123, 0x0000, 0x6005], 3)
    assert regs(r)[0] == 5
    assert r["state"]["pc"] == 0x206
    assert r["halted"] is False


def test_1nnn_jump():
    # 0x200: jump 0x206; 0x202/0x204 would set V0 = 1; 0x206: V0 = 0x42
    r = go([0x1206, 0x6001, 0x6001, 0x6042, 0x1208], 3)
    assert regs(r)[0] == 0x42
    assert r["state"]["pc"] == 0x208


def test_2nnn_call_pushes_return_address_and_00ee_returns():
    words = [0x2206, 0x6107, 0x1204, 0x6005, 0x00EE]
    mid = go(words, 2)
    assert mid["state"]["stack"] == [0x202]
    assert mid["state"]["sp"] == 1
    assert mid["state"]["pc"] == 0x208
    end = go(words, 6)
    assert regs(end)[0] == 5 and regs(end)[1] == 7
    assert end["state"]["sp"] == 0 and end["state"]["stack"] == []
    assert end["state"]["pc"] == 0x204


def test_nested_calls_stack_bottom_to_top():
    # 0x200: call 0x204; 0x202: loop; 0x204: call 0x208; 0x206: ret; 0x208: loop here
    words = [0x2204, 0x1202, 0x2208, 0x00EE, 0x1208]
    r = go(words, 3)
    assert r["state"]["stack"] == [0x202, 0x206]
    assert r["state"]["sp"] == 2
    assert r["state"]["pc"] == 0x208


def test_bnnn_jump_plus_v0():
    # V0 = 4; jump 0x208 + 4 = 0x20C; 0x20C: V1 = 0x99
    words = [0x6004, 0xB208, 0x0000, 0x0000, 0x0000, 0x0000, 0x6199]
    r = go(words, 3)
    assert regs(r)[1] == 0x99
    assert r["state"]["pc"] == 0x20E


@pytest.mark.parametrize(
    "setup,skip_op,skipped",
    [
        ([0x6342], 0x3342, True),   # 3XNN equal -> skip
        ([0x6342], 0x3341, False),  # 3XNN not equal -> no skip
        ([0x6342], 0x4342, False),  # 4XNN equal -> no skip
        ([0x6342], 0x4341, True),   # 4XNN not equal -> skip
        ([0x6107, 0x6207], 0x5120, True),   # 5XY0 equal
        ([0x6107, 0x6208], 0x5120, False),  # 5XY0 not equal
        ([0x6107, 0x6208], 0x9120, True),   # 9XY0 not equal
        ([0x6107, 0x6207], 0x9120, False),  # 9XY0 equal
    ],
)
def test_skips(setup, skip_op, skipped):
    words = setup + [skip_op, 0x6501, 0x6602]
    base = 0x200 + 2 * len(words)
    r = go(words + [0x1000 | base], len(setup) + 3)
    if skipped:
        assert regs(r)[5] == 0 and regs(r)[6] == 2
    else:
        assert regs(r)[5] == 1 and regs(r)[6] == 2


# ---------------------------------------------------------------- load

def test_6xnn():
    r = go([0x60AB, 0x6F01], 2)
    assert regs(r)[0] == 0xAB and regs(r)[0xF] == 1


def test_7xnn_wraps_and_leaves_vf():
    r = go([0x60FF, 0x6F55, 0x7002], 3)
    assert regs(r)[0] == 0x01
    assert regs(r)[0xF] == 0x55


def test_7xnn_on_vf():
    r = go([0x6F05, 0x7F01], 2)
    assert regs(r)[0xF] == 6


def test_annn():
    r = go([0xA123], 1)
    assert r["state"]["i"] == 0x123


def test_fx1e_adds_and_leaves_vf():
    r = go([0xA100, 0x6210, 0x6F77, 0xF21E], 4)
    assert r["state"]["i"] == 0x110
    assert regs(r)[0xF] == 0x77


def test_fx1e_wraps_at_16_bits():
    # I = 0xFFF, then add 255 241 times: 0xFFF + 241*255 = 65550 -> 65550 & 0xFFFF = 14
    words = [
        0x6F33,  # 0x200 VF = 0x33
        0xAFFF,  # 0x202 I = 0xFFF
        0x60FF,  # 0x204 V0 = 255
        0x6100,  # 0x206 V1 = 0
        0xF01E,  # 0x208 I += V0
        0x7101,  # 0x20A V1 += 1
        0x31F1,  # 0x20C skip if V1 == 241
        0x1208,  # 0x20E loop
        0x1210,  # 0x210 done
    ]
    r = go(words, 2000)
    assert regs(r)[1] == 241
    assert r["state"]["i"] == 14
    assert regs(r)[0xF] == 0x33
    assert r["state"]["pc"] == 0x210


@pytest.mark.parametrize("value,addr", [(0x0, 0x050), (0xA, 0x050 + 50), (0x1A, 0x050 + 50), (0xF, 0x050 + 75)])
def test_fx29_font_address(value, addr):
    r = go([0x6000 | value, 0xF029], 2)
    assert r["state"]["i"] == addr


# ---------------------------------------------------------------- alu

@pytest.mark.parametrize(
    "vx,vy,op,result",
    [
        (0x12, 0x34, 0x0, 0x34),
        (0x0F, 0xF0, 0x1, 0xFF),
        (0x3C, 0x0F, 0x2, 0x0C),
        (0xFF, 0x0F, 0x3, 0xF0),
    ],
)
def test_8xy0_to_8xy3_leave_vf(vx, vy, op, result):
    r = go([0x6000 | vx, 0x6100 | vy, 0x6F42, 0x8010 | op], 4)
    assert regs(r)[0] == result
    assert regs(r)[1] == vy
    assert regs(r)[0xF] == 0x42


@pytest.mark.parametrize(
    "vx,vy,result,flag",
    [(0x10, 0x20, 0x30, 0), (0xF0, 0x20, 0x10, 1), (0xFF, 0x01, 0x00, 1), (0x80, 0x7F, 0xFF, 0)],
)
def test_8xy4(vx, vy, result, flag):
    r = go([0x6000 | vx, 0x6100 | vy, 0x8014], 3)
    assert regs(r)[0] == result and regs(r)[0xF] == flag


def test_8xy4_flag_wins_when_x_is_f():
    # VF = 0x10, V1 = 0x20: result 0x30 is overwritten by carry 0
    r = go([0x6F10, 0x6120, 0x8F14], 3)
    assert regs(r)[0xF] == 0


def test_8xy4_with_y_f_uses_old_vf():
    r = go([0x6080, 0x6F90, 0x80F4], 3)
    assert regs(r)[0] == 0x10 and regs(r)[0xF] == 1


@pytest.mark.parametrize(
    "vx,vy,result,flag",
    [(9, 4, 5, 1), (5, 5, 0, 1), (3, 5, 0xFE, 0), (0, 0xFF, 0x01, 0)],
)
def test_8xy5(vx, vy, result, flag):
    r = go([0x6000 | vx, 0x6100 | vy, 0x8015], 3)
    assert regs(r)[0] == result and regs(r)[0xF] == flag


def test_8xy5_flag_wins_when_x_is_f():
    # VF = 0x30, V1 = 0x10: result 0x20, then flag 1
    r = go([0x6F30, 0x6110, 0x8F15], 3)
    assert regs(r)[0xF] == 1


@pytest.mark.parametrize("vx,result,flag", [(0x05, 0x02, 1), (0x04, 0x02, 0), (0xFF, 0x7F, 1)])
def test_8xy6_ignores_vy(vx, result, flag):
    r = go([0x6000 | vx, 0x61FF, 0x8016], 3)
    assert regs(r)[0] == result and regs(r)[0xF] == flag
    assert regs(r)[1] == 0xFF


def test_8xy6_flag_wins_when_x_is_f():
    r = go([0x6F06, 0x8F06], 2)  # result 3, shifted-out bit 0
    assert regs(r)[0xF] == 0


@pytest.mark.parametrize(
    "vx,vy,result,flag",
    [(3, 5, 2, 1), (5, 3, 0xFE, 0), (7, 7, 0, 1)],
)
def test_8xy7(vx, vy, result, flag):
    r = go([0x6000 | vx, 0x6100 | vy, 0x8017], 3)
    assert regs(r)[0] == result and regs(r)[0xF] == flag


def test_8xy7_flag_wins_when_x_is_f():
    # VF = 0x10, V1 = 0x30: result 0x20, then flag 1
    r = go([0x6F10, 0x6130, 0x8F17], 3)
    assert regs(r)[0xF] == 1


@pytest.mark.parametrize("vx,result,flag", [(0x81, 0x02, 1), (0x41, 0x82, 0), (0xFF, 0xFE, 1)])
def test_8xye_ignores_vy(vx, result, flag):
    r = go([0x6000 | vx, 0x6101, 0x801E], 3)
    assert regs(r)[0] == result and regs(r)[0xF] == flag


def test_8xye_flag_wins_when_x_is_f():
    r = go([0x6F40, 0x8F0E], 2)  # result 0x80, shifted-out bit 0
    assert regs(r)[0xF] == 0


# ---------------------------------------------------------------- random

def test_cxnn_default_seed():
    expected = lcg_sequence(1, 2)
    assert expected[0] == 198  # 1103527590 >> 16 = 16838; 16838 & 0xFF = 198
    r = go([0xC0FF, 0xC10F], 2)
    assert regs(r)[0] == expected[0]
    assert regs(r)[1] == expected[1] & 0x0F


def test_cxnn_with_seed():
    expected = lcg_sequence(12345, 3)
    r = go([0xC0FF, 0xC1FF, 0xC2F0], 3, seed=12345)
    assert regs(r)[:3] == [expected[0], expected[1], expected[2] & 0xF0]


# ---------------------------------------------------------------- display

def test_draw_font_zero_and_clear():
    words = [0x6000, 0xF029, 0xD005, 0x00E0, 0x1208]
    r = go(words, 5, frames_at=[3, 4])
    f = r["frames"]["3"]
    assert f[0][:8] == "11110000"
    assert f[1][:8] == "10010000"
    assert f[4][:8] == "11110000"
    assert f[5] == BLANK_ROW
    assert r["frames"]["4"] == [BLANK_ROW] * 32
    assert r["final_frame"] == [BLANK_ROW] * 32


def test_draw_collision_sets_vf_and_erases():
    words = [0x6000, 0xF029, 0xD005, 0xD005]
    first = go(words, 3)
    assert regs(first)[0xF] == 0
    second = go(words, 4)
    assert regs(second)[0xF] == 1
    assert second["final_frame"] == [BLANK_ROW] * 32


def test_draw_without_overlap_clears_vf():
    # Draw '0' at x=0, then '1' at x=8: no pixel turns off -> VF = 0
    words = [0x6F07, 0x6000, 0xF029, 0xD005, 0x6108, 0x6201, 0xF229, 0xD105]
    r = go(words, 8)
    assert regs(r)[0xF] == 0
    assert r["final_frame"][0][:16] == "1111000000100000"


def test_draw_clips_right_edge():
    # sprite 0xF0 (top row of '0') at x=62 -> only x=62,63 lit; nothing wraps to x=0,1
    r = go([0x603E, 0x6100, 0xA050, 0xD011], 4)
    assert r["final_frame"][0] == "0" * 62 + "11"


def test_draw_clips_bottom_edge():
    # '0' at y=30: rows 30, 31 drawn; rows 0..2 untouched
    r = go([0x6000, 0x611E, 0xA050, 0xD015], 4)
    f = r["final_frame"]
    assert f[30][:4] == "1111" and f[31][:4] == "1001"
    assert f[0] == f[1] == f[2] == BLANK_ROW


def test_draw_start_position_wraps():
    # x = 67 -> 3, y = 33 -> 1
    r = go([0x6043, 0x6121, 0xA050, 0xD011], 4)
    f = r["final_frame"]
    assert f[1] == "000" + "1111" + "0" * 57
    assert f[0] == BLANK_ROW


def test_draw_zero_rows_draws_nothing_and_clears_vf():
    r = go([0x6F05, 0x6000, 0xA050, 0xD000], 4)
    assert regs(r)[0xF] == 0
    assert r["final_frame"] == [BLANK_ROW] * 32


def test_draw_with_vf_as_coordinate():
    # VF = 10 is read as X before VF is overwritten with the collision flag
    r = go([0x6F0A, 0x6100, 0xA050, 0xDF11], 4)
    assert r["final_frame"][0] == "0" * 10 + "1111" + "0" * 50
    assert regs(r)[0xF] == 0


def test_draw_reads_sprite_from_i_with_wrap():
    # I = 0xFFF: row 0 from 0xFFF (0 -> blank), row 1 from 0x000 (0 -> blank) in canonical;
    # write 0xAA at 0xFFF first via FX55 with V0 = 0xAA.
    words = [0x60AA, 0xAFFF, 0xF055, 0x6000, 0x6100, 0xD011]
    r = go(words, 6)
    assert r["final_frame"][0][:8] == "10101010"


# ---------------------------------------------------------------- memory

@pytest.mark.parametrize("value,digits", [(0, [0, 0, 0]), (255, [2, 5, 5]), (137, [1, 3, 7]), (9, [0, 0, 9])])
def test_fx33_bcd(value, digits):
    words = [0x6000 | value, 0xA300, 0xF033, 0xF265]
    r = go(words, 4)
    assert regs(r)[:3] == digits
    assert r["state"]["i"] == 0x300


def test_fx33_address_wraps():
    # I = 0xFFF, BCD(123) -> 0xFFF=1, 0x000=2, 0x001=3; read back through the same wrap
    words = [0x607B, 0xAFFF, 0xF033, 0xF265]
    r = go(words, 4)
    assert regs(r)[:3] == [1, 2, 3]


def test_fx55_fx65_round_trip_i_unchanged():
    words = [0x6011, 0x6122, 0x6233, 0x6344, 0xA300, 0xF355,
             0x6000, 0x6100, 0x6200, 0x6300, 0xF265]
    r = go(words, 11)
    assert regs(r)[:4] == [0x11, 0x22, 0x33, 0]
    assert r["state"]["i"] == 0x300


def test_fx55_x_zero_stores_only_v0():
    words = [0x6055, 0x6166, 0xA300, 0xF055, 0x6000, 0x6100, 0xF165]
    r = go(words, 7)
    assert regs(r)[:2] == [0x55, 0x00]


def test_fx65_loads_vf():
    # memory at 0x300.. is zero except what we store; store V0..VF then load all
    words = [0x6F99, 0xA300, 0xFF55, 0x6F00, 0xFF65]
    r = go(words, 5)
    assert regs(r)[0xF] == 0x99


# ---------------------------------------------------------------- timers

def test_fx07_reads_dt():
    r = go([0x600C, 0xF015, 0xF107], 3)
    assert regs(r)[1] == 12


def test_timers_tick_after_every_tenth_cycle():
    # cycle 1: V0=20; cycle 2: DT=20; cycle 3: ST=20; then loop at 0x206
    words = [0x6014, 0xF015, 0xF018, 0x1206]
    assert go(words, 9)["state"]["dt"] == 20
    r10 = go(words, 10)["state"]
    assert r10["dt"] == 19 and r10["st"] == 19
    assert go(words, 19)["state"]["dt"] == 19
    assert go(words, 30)["state"]["dt"] == 17


def test_timer_set_on_tick_cycle_decrements_immediately():
    # 9 no-ops, then DT=5 executes as cycle 10 and is decremented after it
    words = [0x6005] + [0x0000] * 8 + [0xF015, 0x1214]
    r = go(words, 10)
    assert r["state"]["dt"] == 4


def test_timers_do_not_go_below_zero():
    r = go([0x6001, 0xF015, 0x1204], 40)
    assert r["state"]["dt"] == 0 and r["state"]["st"] == 0


# ---------------------------------------------------------------- input

def test_ex9e_skips_when_key_down():
    words = [0x6013, 0xE09E, 0x6101, 0x6202, 0x1208]  # key = V0 & 0xF = 3
    down = go(words, 4, keys=[{"cycle": 1, "key": 3, "down": True}])
    assert regs(down)[1] == 0 and regs(down)[2] == 2
    up = go(words, 4)
    assert regs(up)[1] == 1


def test_exa1_skips_when_key_up():
    words = [0x6005, 0xE0A1, 0x6101, 0x6202, 0x1208]
    up = go(words, 4)
    assert regs(up)[1] == 0 and regs(up)[2] == 2
    down = go(words, 4, keys=[{"cycle": 2, "key": 5, "down": True}])
    assert regs(down)[1] == 1


def test_key_event_applies_before_its_cycle():
    words = [0x6005, 0xE0A1, 0x6101, 0x6202, 0x1208]
    # key 5 down at cycle 3 is too late for EXA1 at cycle 2 -> skip happens
    late = go(words, 4, keys=[{"cycle": 3, "key": 5, "down": True}])
    assert regs(late)[1] == 0


def test_fx0a_waits_then_reads_key():
    words = [0xF00A, 0x6101, 0x1204]
    waiting = go(words, 4, keys=[{"cycle": 5, "key": 7, "down": True}])
    assert waiting["cycles_run"] == 4
    assert waiting["state"]["pc"] == 0x200
    assert regs(waiting)[0] == 0
    done = go(words, 6, keys=[{"cycle": 5, "key": 7, "down": True}])
    assert regs(done)[0] == 7 and regs(done)[1] == 1
    assert done["state"]["pc"] == 0x204


def test_fx0a_picks_lowest_down_key():
    keys = [{"cycle": 1, "key": 9, "down": True}, {"cycle": 1, "key": 3, "down": True}]
    r = go([0xF00A, 0x1202], 1, keys=keys)
    assert regs(r)[0] == 3


def test_fx0a_released_key_does_not_count():
    keys = [{"cycle": 1, "key": 4, "down": True}, {"cycle": 2, "key": 4, "down": False}]
    r = go([0x6000, 0xF10A, 0x1204], 5, keys=keys)
    assert r["state"]["pc"] == 0x202


def test_timers_tick_while_waiting_for_key():
    r = go([0x6005, 0xF015, 0xF10A], 30)
    assert r["state"]["dt"] == 2
    assert r["state"]["pc"] == 0x204
    assert r["cycles_run"] == 30


# ---------------------------------------------------------------- halts

@pytest.mark.parametrize("bad", [0x5121, 0x8128, 0x812F, 0x9121, 0xE0FF, 0xF0FF, 0xF001])
def test_invalid_opcode_halts(bad):
    r = go([0x6001, bad, 0x6002], 10)
    assert r["halted"] is True
    assert r["halt_reason"] == "invalid_opcode"
    assert r["cycles_run"] == 1
    assert r["state"]["pc"] == 0x204  # PC was advanced at fetch
    assert regs(r)[0] == 1


def test_stack_overflow():
    r = go([0x2200], 100)  # call self forever
    assert r["halted"] is True
    assert r["halt_reason"] == "stack_overflow"
    assert r["cycles_run"] == 16
    assert r["state"]["sp"] == 16
    assert r["state"]["stack"] == [0x202] * 16
    assert r["state"]["pc"] == 0x202


def test_stack_underflow():
    r = go([0x00EE], 5)
    assert r["halted"] is True
    assert r["halt_reason"] == "stack_underflow"
    assert r["cycles_run"] == 0
    assert r["state"]["pc"] == 0x202


def test_pc_out_of_bounds_after_running_off_memory():
    # jump to 0xFFE; the zero word there is a no-op; PC becomes 0x1000 and the next cycle halts
    r = go([0x1FFE], 10)
    assert r["halted"] is True
    assert r["halt_reason"] == "pc_out_of_bounds"
    assert r["cycles_run"] == 2
    assert r["state"]["pc"] == 0x1000


def test_pc_at_limit_is_not_a_halt_until_next_cycle_runs():
    r = go([0x1FFE], 2)
    assert r["halted"] is False
    assert r["state"]["pc"] == 0x1000


def test_bnnn_beyond_memory_halts():
    r = go([0x60FF, 0xBFFF], 5)
    assert r["halted"] is True and r["halt_reason"] == "pc_out_of_bounds"
    assert r["state"]["pc"] == 0xFFF + 0xFF
    assert r["cycles_run"] == 2


def test_odd_pc_fetch_wraps_memory():
    # jump to 0xFFF: fetch reads 0xFFF and 0x000 (both zero) -> no-op, PC = 0x1001 -> halt
    r = go([0x1FFF], 10)
    assert r["halt_reason"] == "pc_out_of_bounds"
    assert r["state"]["pc"] == 0x1001
    assert r["cycles_run"] == 2


def test_frames_after_halt_are_omitted():
    r = go([0x6001, 0x00EE], 10, frames_at=[1, 2, 5])
    assert list(r["frames"]) == ["1"]


# ---------------------------------------------------------------- variant_v (Section 8)

def test_variant_font_at_zero():
    r = go([0x600A, 0xF029, 0x6100, 0xD115], 4, config=VARIANT_V)
    assert r["state"]["i"] == 50
    assert r["final_frame"][0][:8] == "11110000"   # 'A' = F0 90 F0 90 90
    assert r["final_frame"][1][:8] == "10010000"


@pytest.mark.parametrize(
    "skip_op,skipped",
    [(0x3342, False), (0x3341, True), (0x4342, True), (0x4341, False)],
)
def test_variant_swapped_skips(skip_op, skipped):
    words = [0x6342, skip_op, 0x6501, 0x6602, 0x1208]
    r = go(words, 4, config=VARIANT_V)
    assert (regs(r)[5] == 0) == skipped


def test_variant_7xnn_sets_vf():
    assert regs(go([0x60FF, 0x7002], 2, config=VARIANT_V))[0xF] == 1
    assert regs(go([0x6010, 0x6F55, 0x7002], 3, config=VARIANT_V))[0xF] == 0
    # X == F: the flag is written after the result
    assert regs(go([0x6FFF, 0x7F02], 2, config=VARIANT_V))[0xF] == 1


def test_variant_timer_period_8():
    words = [0x6014, 0xF015, 0x1204]
    assert go(words, 7, config=VARIANT_V)["state"]["dt"] == 20
    assert go(words, 8, config=VARIANT_V)["state"]["dt"] == 19
    assert go(words, 24, config=VARIANT_V)["state"]["dt"] == 17


def test_variant_5xy1_multiply():
    r = go([0x6020, 0x6110, 0x5011], 3, config=VARIANT_V)  # 32 * 16 = 512 = 0x200
    assert regs(r)[0] == 0x00 and regs(r)[0xF] == 2
    r = go([0x6007, 0x6106, 0x5011], 3, config=VARIANT_V)
    assert regs(r)[0] == 42 and regs(r)[0xF] == 0
    r = go([0x6FFF, 0x61FF, 0x5F11], 3, config=VARIANT_V)  # 0xFE01: flag wins
    assert regs(r)[0xF] == 0xFE


def test_variant_other_5xyn_still_invalid():
    r = go([0x5012], 3, config=VARIANT_V)
    assert r["halt_reason"] == "invalid_opcode"


def test_canonical_5xy1_invalid():
    assert go([0x5011], 3)["halt_reason"] == "invalid_opcode"


# ---------------------------------------------------------------- CLI + determinism

def _cli(tmp_path, *extra):
    romfile = tmp_path / "t.ch8"
    romfile.write_bytes(rom(0x6000, 0xF029, 0xC1FF, 0xD015, 0x7001, 0x1202))
    keys = tmp_path / "keys.json"
    keys.write_text(json.dumps([{"cycle": 3, "key": 1, "down": True}]))
    cmd = [sys.executable, "-m", "reference.cli", "--rom", str(romfile), "--cycles", "500",
           "--keys", str(keys), "--seed", "7", "--frames-at", "10,100,250", *extra]
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, check=True).stdout


def test_cli_outputs_one_json_object(tmp_path):
    out = _cli(tmp_path)
    data = json.loads(out)
    assert data["cycles_run"] == 500
    assert sorted(data["frames"], key=int) == ["10", "100", "250"]


def test_cli_is_byte_identical_across_runs(tmp_path):
    assert _cli(tmp_path) == _cli(tmp_path)
    assert _cli(tmp_path, "--variant", "variant_v") == _cli(tmp_path, "--variant", "variant_v")


def test_bundled_single_file_matches_module(tmp_path):
    from reference.bundle import write_bundle

    emu = write_bundle(tmp_path / "emulator.py")
    romfile = tmp_path / "t.ch8"
    romfile.write_bytes(rom(0x6000, 0xF029, 0xC1FF, 0xD015, 0x7001, 0x1202))
    args = ["--rom", str(romfile), "--cycles", "300", "--frames-at", "5,50"]
    bundled = subprocess.run([sys.executable, str(emu), *args], capture_output=True, check=True, cwd=tmp_path).stdout
    module = subprocess.run([sys.executable, "-m", "reference.cli", *args], capture_output=True, check=True, cwd=ROOT).stdout
    assert bundled == module
