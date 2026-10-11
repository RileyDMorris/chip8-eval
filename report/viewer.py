"""Write a self-contained HTML viewer for one graded run.

    python -m report.viewer results/runs/<run_id>          # -> results/runs/<run_id>/viewer.html

Lists every hidden test (failures first). For a unit test it shows the expected
and actual machine state side by side plus the final frame; for a program test,
every checkpoint frame. Frames are drawn as candidate | reference | diff.
"""

import argparse
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root { --bg: #f6f7f9; --panel: #ffffff; --text: #1d2330; --muted: #5f6b7a; --line: #dde2e8;
        --pass: #1f8a4c; --fail: #c4372d; --accent: #2f5fd0; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #14171c; --panel: #1c2027; --text: #e4e7ec; --muted: #9aa4b2; --line: #2c323c;
          --pass: #4cc38a; --fail: #f07167; --accent: #7aa2f7; }
}
* { box-sizing: border-box; }
body { margin: 0; font: 14px/1.45 system-ui, -apple-system, Segoe UI, sans-serif; background: var(--bg); color: var(--text); }
header { padding: 16px 20px; border-bottom: 1px solid var(--line); background: var(--panel); }
header h1 { font-size: 18px; margin: 0 0 4px; }
header .scores { color: var(--muted); }
main { display: grid; grid-template-columns: minmax(220px, 300px) 1fr; min-height: calc(100vh - 70px); }
nav { border-right: 1px solid var(--line); overflow-y: auto; max-height: calc(100vh - 70px); background: var(--panel); }
nav input { width: calc(100% - 20px); margin: 10px; padding: 6px 8px; border: 1px solid var(--line); border-radius: 6px;
            background: var(--bg); color: var(--text); }
nav button { display: flex; gap: 8px; align-items: center; width: 100%; text-align: left; border: 0; background: none;
             color: var(--text); padding: 5px 12px; cursor: pointer; font: inherit; }
nav button:hover, nav button.active { background: var(--bg); }
.dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.dot.pass { background: var(--pass); } .dot.fail { background: var(--fail); }
section { padding: 16px 20px; overflow-x: auto; }
h2 { font-size: 16px; margin: 0 0 4px; }
.meta { color: var(--muted); margin-bottom: 12px; }
table { border-collapse: collapse; margin: 8px 0 16px; font-variant-numeric: tabular-nums; }
td, th { border: 1px solid var(--line); padding: 3px 8px; text-align: left; }
td.bad { color: var(--fail); font-weight: 600; }
.frames { display: flex; gap: 10px; flex-wrap: wrap; margin: 6px 0 18px; }
.frames figure { margin: 0; }
.frames figcaption { color: var(--muted); font-size: 12px; }
canvas { image-rendering: pixelated; border: 1px solid var(--line); display: block; max-width: 100%; }
.legend span { display: inline-block; width: 10px; height: 10px; margin: 0 4px 0 10px; vertical-align: middle; }
@media (max-width: 700px) { main { grid-template-columns: 1fr; } nav { max-height: 240px; } }
</style>
</head>
<body>
<header>
  <h1>__HEADING__</h1>
  <div class="scores">__SCORES__</div>
</header>
<main>
  <nav><input id="filter" placeholder="Filter tests" aria-label="Filter tests"><div id="list"></div></nav>
  <section id="detail"></section>
</main>
<script>
const DATA = __DATA__;
const COLORS = {off: [16,18,22], on: [230,232,236], cand: [235,80,70], ref: [60,190,120], both: [70,74,82]};
const SCALE = 6;
function frameCanvas(frame, other, diff) {
  const c = document.createElement("canvas");
  c.width = 64 * SCALE; c.height = 32 * SCALE;
  const ctx = c.getContext("2d");
  const img = ctx.createImageData(64, 32);
  const valid = Array.isArray(frame) && frame.length === 32;
  for (let y = 0; y < 32; y++) for (let x = 0; x < 64; x++) {
    const a = valid && frame[y] && frame[y][x] === "1";
    let col = a ? COLORS.on : COLORS.off;
    if (diff) { const b = other[y][x] === "1"; col = a && b ? COLORS.both : a ? COLORS.cand : b ? COLORS.ref : COLORS.off; }
    const i = 4 * (y * 64 + x); img.data[i] = col[0]; img.data[i+1] = col[1]; img.data[i+2] = col[2]; img.data[i+3] = 255;
  }
  const tmp = document.createElement("canvas"); tmp.width = 64; tmp.height = 32; tmp.getContext("2d").putImageData(img, 0, 0);
  ctx.imageSmoothingEnabled = false; ctx.drawImage(tmp, 0, 0, 64 * SCALE, 32 * SCALE);
  return c;
}
function figure(canvas, caption) {
  const f = document.createElement("figure"); f.appendChild(canvas);
  const cap = document.createElement("figcaption"); cap.textContent = caption; f.appendChild(cap); return f;
}
function frameRow(cand, ref, label) {
  const row = document.createElement("div"); row.className = "frames";
  row.appendChild(figure(frameCanvas(cand), "candidate" + (Array.isArray(cand) ? "" : " (no frame)")));
  row.appendChild(figure(frameCanvas(ref), "reference"));
  row.appendChild(figure(frameCanvas(cand, ref, true), "diff " + label));
  return row;
}
function esc(s) { const d = document.createElement("div"); d.textContent = String(s); return d.innerHTML; }
function fmt(v) { return v === undefined ? "–" : JSON.stringify(v); }
function show(test) {
  document.querySelectorAll("nav button").forEach(b => b.classList.toggle("active", b.dataset.id === test.id));
  const el = document.getElementById("detail"); el.innerHTML = "";
  const h = document.createElement("h2"); h.textContent = test.id; el.appendChild(h);
  const m = document.createElement("div"); m.className = "meta";
  m.innerHTML = esc(test.description) + " · family " + esc(test.family) + (test.opcode ? " · " + esc(test.opcode) : "") +
    " · " + (test.passed ? "passed" : "<strong>failed</strong>") + (test.error ? " · error: " + esc(test.error) : "") +
    (test.explained ? " · reproduced by standard CHIP-8 behavior for: " + esc(test.explained.join(", ")) : "");
  el.appendChild(m);
  if (test.stderr) { const p = document.createElement("pre"); p.textContent = test.stderr; el.appendChild(p); }
  const cand = test.candidate || {};
  if (test.kind === "unit") {
    const t = document.createElement("table");
    t.innerHTML = "<tr><th>field</th><th>expected</th><th>candidate</th></tr>";
    const exp = test.expected, cs = cand.state || {};
    const rows = [["halted", exp.halted, cand.halted], ["halt_reason", exp.halt_reason, cand.halt_reason],
                  ["cycles_run (not graded)", exp.cycles_run, cand.cycles_run]];
    for (let r = 0; r < 16; r++) rows.push(["V" + r.toString(16).toUpperCase(), exp.state.v[r], (cs.v || [])[r]]);
    for (const k of ["i", "pc", "sp", "stack", "dt", "st"]) rows.push([k, exp.state[k], cs[k]]);
    for (const [name, a, b] of rows) {
      const tr = document.createElement("tr"); const bad = JSON.stringify(a) !== JSON.stringify(b);
      tr.innerHTML = "<td>" + esc(name) + "</td><td>" + esc(fmt(a)) + "</td><td class='" + (bad ? "bad" : "") + "'>" + esc(fmt(b)) + "</td>";
      t.appendChild(tr);
    }
    el.appendChild(t);
    el.appendChild(frameRow(cand.final_frame, exp.final_frame, "(final frame)"));
  } else {
    for (const cp of test.checkpoints) {
      const label = "cycle " + cp.cycle + " · " + (cp.match ? "match" : (100 * cp.accuracy).toFixed(1) + "% pixels");
      const sub = document.createElement("div"); sub.className = "meta"; sub.textContent = label; el.appendChild(sub);
      el.appendChild(frameRow((cand.frames || {})[String(cp.cycle)], test.expected.frames[String(cp.cycle)], ""));
    }
  }
  const lg = document.createElement("div"); lg.className = "legend meta";
  lg.innerHTML = "diff:<span style='background:rgb(235,80,70)'></span>only candidate<span style='background:rgb(60,190,120)'></span>only reference<span style='background:rgb(70,74,82)'></span>both";
  el.appendChild(lg);
}
function renderList() {
  const q = document.getElementById("filter").value.toLowerCase();
  const list = document.getElementById("list"); list.innerHTML = "";
  for (const test of DATA.tests) {
    if (q && !(test.id + " " + test.family + " " + test.opcode).toLowerCase().includes(q)) continue;
    const b = document.createElement("button"); b.dataset.id = test.id;
    b.innerHTML = "<span class='dot " + (test.passed ? "pass" : "fail") + "'></span>" + esc(test.id.replace(/^(unit|program)_/, ""));
    b.onclick = () => show(test); list.appendChild(b);
  }
}
document.getElementById("filter").addEventListener("input", renderList);
renderList();
if (DATA.tests.length) show(DATA.tests[0]);
</script>
</body>
</html>
"""


def build_viewer(run_dir, out_path=None):
    run_dir = Path(run_dir)
    meta = json.loads((run_dir / "meta.json").read_text())
    scores = json.loads((run_dir / "scores.json").read_text())
    outputs = json.loads((run_dir / "outputs.json").read_text()) if (run_dir / "outputs.json").exists() else {}
    variant = meta["variant"]
    suite = ROOT / "tests_data" / "hidden" / variant
    manifest = {t["id"]: t for t in json.loads((suite / "manifest.json").read_text())["tests"]}
    tests = []
    for record in scores["tests"]:
        golden = json.loads((suite / "golden" / f"{record['id']}.json").read_text())
        raw = outputs.get(record["id"]) or {}
        try:
            candidate = json.loads(raw.get("stdout", ""))
            candidate = candidate if isinstance(candidate, dict) else None
        except ValueError:
            candidate = None
        if record["kind"] == "unit":
            expected = {k: golden[k] for k in ("halted", "halt_reason", "cycles_run", "state", "final_frame")}
            if candidate:
                candidate = {k: candidate.get(k) for k in ("halted", "halt_reason", "cycles_run", "state", "final_frame")}
        else:
            expected = {"frames": golden["frames"]}
            if candidate:
                candidate = {"frames": candidate.get("frames")}
        tests.append({
            "id": record["id"], "kind": record["kind"], "family": record["family"], "opcode": record["opcode"],
            "description": manifest[record["id"]]["description"], "passed": record["passed"],
            "error": record.get("error"), "stderr": record.get("stderr_tail"),
            "explained": record.get("explained_by_standard"), "checkpoints": record.get("checkpoints", []),
            "expected": expected, "candidate": candidate,
        })
    tests.sort(key=lambda t: (t["passed"], t["kind"] != "unit", t["id"]))
    title = f"{meta['model']['name']} · {variant} · run {meta.get('attempt', 1)}"
    summary = (f"overall {100 * scores['overall']:.1f}% · unit {scores['unit']['passed']}/{scores['unit']['total']} · "
               f"program checkpoints {scores['program']['checkpoints_matched']}/{scores['program']['checkpoints_total']} · "
               f"robust {scores['robust_tests']}/{scores['total_tests']}")
    data = json.dumps({"tests": tests}, separators=(",", ":")).replace("</", "<\\/")
    page = (PAGE.replace("__TITLE__", html.escape(f"Run viewer: {title}"))
            .replace("__HEADING__", html.escape(title))
            .replace("__SCORES__", html.escape(summary))
            .replace("__DATA__", data))
    out_path = Path(out_path) if out_path else run_dir / "viewer.html"
    out_path.write_text(page)
    return out_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dirs", nargs="+")
    args = parser.parse_args(argv)
    for run_dir in args.run_dirs:
        print(f"wrote {build_viewer(run_dir)}")


if __name__ == "__main__":
    main()
