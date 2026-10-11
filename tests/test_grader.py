"""Grader tests (milestone M2): scoring rules, mutant validation, container isolation."""

import json
from pathlib import Path

import pytest

from grader.grade import grade
from grader.mutants import validate
from grader.run_candidate import docker_available, run_docker
from grader.score import WEIGHTS, score_suite
from reference.bundle import write_bundle

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "tests_data" / "public" / "canonical"
needs_docker = pytest.mark.skipif(not docker_available(), reason="Docker daemon not available")


def golden_records(suite=PUBLIC):
    manifest = json.loads((suite / "manifest.json").read_text())
    records = {}
    for test in manifest["tests"]:
        stdout = (suite / manifest["expected_dir"] / f"{test['id']}.json").read_text()
        records[test["id"]] = {"id": test["id"], "status": "exited", "returncode": 0,
                               "stdout": stdout, "stderr_tail": "", "elapsed": 0.1}
    return manifest, records


def test_perfect_outputs_score_one():
    _, records = golden_records()
    scores = score_suite(PUBLIC, records)
    assert scores["overall"] == 1.0
    assert scores["unit_score"] == scores["program_score"] == scores["robustness"] == 1.0


def test_failure_modes_are_recorded():
    manifest, records = golden_records()
    units = [t["id"] for t in manifest["tests"] if t["kind"] == "unit"]
    records[units[0]].update(status="timeout", returncode=None, stdout="")
    records[units[1]].update(stdout="not json", returncode=0)
    records[units[2]].update(stdout="Traceback ...", returncode=1)
    records[units[3]].update(stdout="[1, 2]")
    scores = score_suite(PUBLIC, records)
    by_id = {t["id"]: t for t in scores["tests"]}
    assert by_id[units[0]]["error"] == "timeout"
    assert by_id[units[1]]["error"] == "invalid_json"
    assert by_id[units[2]]["error"] == "crash"
    assert by_id[units[3]]["error"] == "invalid_json"
    assert scores["robust_tests"] == len(manifest["tests"]) - 4
    assert scores["unit"]["passed"] == len(units) - 4


def test_unit_mismatch_fields_and_ungraded_cycles_run():
    manifest, records = golden_records()
    unit = next(t["id"] for t in manifest["tests"] if t["kind"] == "unit")
    out = json.loads(records[unit]["stdout"])
    out["cycles_run"] += 1           # not graded
    out["state"]["v"][3] ^= 1
    out["final_frame"][0] = "1" + out["final_frame"][0][1:] if out["final_frame"][0][0] == "0" else "0" + out["final_frame"][0][1:]
    records[unit]["stdout"] = json.dumps(out)
    scores = score_suite(PUBLIC, records)
    record = next(t for t in scores["tests"] if t["id"] == unit)
    assert record["passed"] is False
    assert record["mismatches"] == ["state.v", "final_frame"]


def test_program_checkpoints_and_pixel_accuracy():
    manifest, records = golden_records()
    program = next(t for t in manifest["tests"] if t["kind"] == "program")
    out = json.loads(records[program["id"]]["stdout"])
    first = str(program["frames_at"][0])
    row = out["frames"][first][5]
    out["frames"][first][5] = "".join("1" if c == "0" else "0" for c in row[:4]) + row[4:]
    del out["frames"][str(program["frames_at"][1])]
    records[program["id"]]["stdout"] = json.dumps(out)
    scores = score_suite(PUBLIC, records)
    record = next(t for t in scores["tests"] if t["id"] == program["id"])
    cps = {cp["cycle"]: cp for cp in record["checkpoints"]}
    assert cps[program["frames_at"][0]] == {"cycle": program["frames_at"][0], "match": False, "accuracy": round(2044 / 2048, 4)}
    assert cps[program["frames_at"][1]]["match"] is False and cps[program["frames_at"][1]]["accuracy"] == 0.0
    total = scores["program"]["checkpoints_total"]
    assert scores["program"]["checkpoints_matched"] == total - 2
    expected = WEIGHTS["unit"] * 1.0 + WEIGHTS["program"] * (total - 2) / total + WEIGHTS["robustness"] * 1.0
    assert scores["overall"] == round(expected, 4)


def test_missing_emulator_scores_zero(tmp_path):
    scores = grade(tmp_path / "emulator.py", "canonical", split="public", mode="local")
    assert scores["overall"] == 0.0
    assert scores["emulator_present"] is False


def test_variant_analysis_detects_standard_chip8_behavior(tmp_path):
    """A canonical emulator graded on variant_v should be caught reverting to standard CHIP-8."""
    emu = write_bundle(tmp_path / "emulator.py", "canonical")
    scores = grade(emu, "variant_v", mode="local")
    analysis = scores["variant_analysis"]
    assert scores["overall"] < 0.8
    # every failure of a fully canonical emulator is explained by standard CHIP-8 behavior
    # (attributed to the smallest set of reverted changes that reproduces it)
    failed = [t for t in scores["tests"] if not t["passed"]]
    assert failed and all(t.get("explained_by_standard") for t in failed)
    assert analysis["mul"]["explained_by_standard"] == analysis["mul"]["tests"] - analysis["mul"]["passed"]
    for stats in analysis.values():
        assert stats["explained_by_standard"] <= stats["tests"] - stats["passed"]


@pytest.mark.slow
@pytest.mark.parametrize("variant", ["canonical", "variant_v"])
def test_grader_validation_with_mutants(variant):
    report, failures = validate(variant, mode="local", log=lambda *_: None)
    assert report["reference_overall"] == 1.0
    assert not failures
    assert len(report["mutants"]) >= 6


@needs_docker
@pytest.mark.docker
def test_reference_scores_100_in_grading_container(tmp_path):
    emu = write_bundle(tmp_path / "emulator.py", "canonical")
    scores = grade(emu, "canonical", mode="docker")
    assert scores["overall"] == 1.0


@needs_docker
@pytest.mark.docker
def test_grading_container_isolates_candidate(tmp_path):
    probe = tmp_path / "emulator.py"
    probe.write_text(
        "import json, os, socket\n"
        "r = {'uid': os.getuid(), 'files': sorted(os.listdir('/grade'))}\n"
        "try:\n    open('/grade/x', 'w'); r['write'] = True\nexcept OSError:\n    r['write'] = False\n"
        "try:\n    socket.create_connection(('1.1.1.1', 53), timeout=3); r['net'] = True\n"
        "except OSError:\n    r['net'] = False\n"
        "print(json.dumps(r))\n")
    out = run_docker(probe, PUBLIC)
    report = json.loads(next(iter(out.values()))["stdout"])
    assert report["uid"] != 0
    assert report["write"] is False
    assert report["net"] is False
    assert report["files"] == ["emulator.py", "inputs.json", "keys", "roms"]   # no goldens
