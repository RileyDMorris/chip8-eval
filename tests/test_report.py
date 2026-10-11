"""Report generation tests (milestone M4 tooling)."""

import json
import struct
import zlib

from grader.grade import grade
from grader.mutants import MUTANTS
from reference.bundle import write_bundle
from report.make_report import build_report
from report.png import render_diff, write_png


def read_png_size(path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    length, kind = struct.unpack(">I4s", data[8:16])
    assert kind == b"IHDR"
    width, height = struct.unpack(">II", data[16:24])
    crc = struct.unpack(">I", data[16 + length:20 + length])[0]
    assert crc == zlib.crc32(data[12:16 + length]) & 0xFFFFFFFF
    return width, height


def test_png_writer(tmp_path):
    write_png(tmp_path / "a.png", [[(255, 0, 0), (0, 255, 0)], [(0, 0, 255), (0, 0, 0)]])
    assert read_png_size(tmp_path / "a.png") == (2, 2)


def test_frame_diff_handles_missing_candidate(tmp_path):
    reference = ["1" * 64] + ["0" * 64] * 31
    render_diff(tmp_path / "d.png", None, reference, scale=2, gap=1)
    assert read_png_size(tmp_path / "d.png") == (3 * 128 + 4, 64 + 2)


def make_run(runs_dir, tmp_path, model, variant, attempt, patches=(), machine=None):
    emulator = write_bundle(tmp_path / f"{model}{variant}{attempt}.py", machine or variant, patches)
    run_id = f"{model}__{variant}__r{attempt}"
    scores = grade(emulator, variant, out_dir=runs_dir / run_id, mode="local")
    meta = {"run_id": run_id, "model": {"name": model}, "variant": variant, "attempt": attempt,
            "episode": {"end_reason": "submitted", "tool_calls": 42, "wall_clock_s": 600, "cost_usd": 1.5}}
    (runs_dir / run_id / "meta.json").write_text(json.dumps(meta))
    return scores


def test_report_end_to_end(tmp_path):
    runs = tmp_path / "runs"
    wrap = next(m for m in MUTANTS if m.name == "display_wrap_not_clip")
    make_run(runs, tmp_path, "good", "canonical", 1)
    make_run(runs, tmp_path, "buggy", "canonical", 1, wrap.patches)
    make_run(runs, tmp_path, "buggy", "variant_v", 1, machine="canonical")
    out = build_report(runs, tmp_path / "report" / "REPORT.md")
    text = out.read_text()
    for heading in ("## Results — `canonical`", "### Per-family pass rates — `canonical`", "### Frame diffs — `canonical`",
                    "## Memorization gap", "### Which changes each model got wrong", "## Grader validation (mutants)",
                    "## All runs"):
        assert heading in text, heading
    assert "| **good** | 1 | 100.0 |" in text
    images = list((tmp_path / "report" / "frames").glob("*.png"))
    assert images, "expected frame-diff images for the buggy model"
    for image in images:
        assert f"](frames/{image.name})" in text
        read_png_size(image)


def test_report_with_no_runs(tmp_path):
    (tmp_path / "runs").mkdir()
    text = build_report(tmp_path / "runs", tmp_path / "REPORT.md").read_text()
    assert "No graded runs found yet" in text
    assert "## Grader validation (mutants)" in text


def test_viewer_embeds_every_test(tmp_path):
    from report.viewer import build_viewer

    runs = tmp_path / "runs"
    wrap = next(m for m in MUTANTS if m.name == "display_wrap_not_clip")
    scores = make_run(runs, tmp_path, "buggy", "canonical", 1, wrap.patches)
    page = build_viewer(runs / "buggy__canonical__r1").read_text()
    data = json.loads(page.split("const DATA = ", 1)[1].split(";\n", 1)[0])
    assert len(data["tests"]) == scores["total_tests"]
    failed = [t for t in data["tests"] if not t["passed"]]
    assert failed and data["tests"][0]["passed"] is False      # failures listed first
    assert "</script>" not in page.split("const DATA = ", 1)[1].split(";\n", 1)[0]
