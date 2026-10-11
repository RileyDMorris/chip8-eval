"""Run the eval: N models x K runs x variants, each in a fresh sandbox, graded in a fresh container.

    python -m harness.run_eval                                  # every model in models.yaml, defaults
    python -m harness.run_eval --models claude-opus-5-5 --variants canonical --runs 1
    python -m harness.run_eval --models scripted-reference      # pipeline smoke test, no API key

Each run writes results/runs/<run_id>/: transcript.jsonl, emulator.py, outputs.json,
scores.json, meta.json. Runs whose scores.json already exists are skipped (resumable);
pass --force to redo them.
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import yaml

from grader.grade import grade, summary_line
from harness.agent_loop import Budget, Transcript, run_episode
from harness.providers import Price, ProviderError, make_client
from harness.sandbox import Sandbox, ensure_images

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results" / "runs"


def load_dotenv(path=ROOT / ".env"):
    """Minimal .env reader (KEY=VALUE lines); existing environment variables win."""
    if not Path(path).exists():
        return
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


def load_config(path):
    config = yaml.safe_load(Path(path).read_text())
    config.setdefault("defaults", {})
    return config


def git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except OSError:
        return None


def run_id_for(model_name, variant, k, tag=None):
    base = f"{model_name}__{variant}__r{k}"
    return f"{tag}__{base}" if tag else base


INFRA_END_REASONS = {"api_error", "error"}


def run_one(model_cfg, variant, k, defaults, tag=None, force=False, retry_errors=False, results_dir=RESULTS,
            log=print):
    run_id = run_id_for(model_cfg["name"], variant, k, tag)
    run_dir = Path(results_dir) / run_id
    if (run_dir / "scores.json").exists() and not force:
        previous = json.loads((run_dir / "meta.json").read_text()) if (run_dir / "meta.json").exists() else {}
        end = (previous.get("episode") or {}).get("end_reason", "error")
        if not (retry_errors and end in INFRA_END_REASONS):
            log(f"[{run_id}] already done, skipping")
            return json.loads((run_dir / "scores.json").read_text())
        log(f"[{run_id}] previous attempt ended with {end}; retrying")
    run_dir.mkdir(parents=True, exist_ok=True)
    for stale in ("transcript.jsonl", "emulator.py", "outputs.json", "scores.json", "meta.json"):
        (run_dir / stale).unlink(missing_ok=True)

    budget = Budget(**{**defaults.get("budget", {}), **model_cfg.get("budget", {})})
    price = Price(**model_cfg.get("price", {}))
    sandbox_cfg = {**defaults.get("sandbox", {}), **model_cfg.get("sandbox", {})}
    task_dir = ROOT / "tasks" / variant
    prompt = (task_dir / "prompt.md").read_text()
    public_cfg = {k: v for k, v in model_cfg.items() if k not in ("script",)}
    meta = {
        "run_id": run_id,
        "model": public_cfg,
        "variant": variant,
        "attempt": k,
        "budget": budget.__dict__,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit(),
        "host": platform.platform(),
    }
    transcript_path = run_dir / "transcript.jsonl"
    log(f"[{run_id}] starting")
    episode = None
    error = None
    sandbox = Sandbox(cpus=sandbox_cfg.get("cpus", 2), memory=sandbox_cfg.get("memory", "2g"))
    transcript = Transcript(transcript_path)
    try:
        sandbox.start()
        sandbox.copy_task(task_dir)
        client = make_client(model_cfg, log=transcript.log, variant=variant)
        episode = run_episode(client, sandbox, prompt, budget=budget, price=price, transcript=transcript)
        have_emulator = sandbox.copy_out("emulator.py", run_dir / "emulator.py")
    except ProviderError as e:
        error = f"provider setup failed: {e}"
        have_emulator = False
    except Exception:
        error = traceback.format_exc()
        have_emulator = sandbox.started and _try_copy_out(sandbox, run_dir)
    finally:
        transcript.close()
        sandbox.stop()

    meta["episode"] = episode
    meta["error"] = error
    meta["emulator_present"] = bool(have_emulator)
    meta["finished_at"] = datetime.now(timezone.utc).isoformat()
    if error and episode is None and "provider setup failed" in error:
        (run_dir / "meta.json").write_text(json.dumps(meta, indent=1))
        log(f"[{run_id}] {error}")
        return None
    scores = grade(run_dir / "emulator.py", variant, out_dir=run_dir, mode="docker")
    meta["scores"] = {k: scores[k] for k in ("overall", "unit_score", "program_score", "robustness")}
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=1))
    end = episode["end_reason"] if episode else "error"
    log(f"[{run_id}] {end}; {summary_line(scores)}")
    return scores


def _try_copy_out(sandbox, run_dir):
    try:
        return sandbox.copy_out("emulator.py", run_dir / "emulator.py")
    except Exception:
        return False


def select_models(config, names):
    models = config["models"]
    if names:
        wanted = set(names)
        chosen = [m for m in models if m["name"] in wanted]
        missing = wanted - {m["name"] for m in chosen}
        if missing:
            raise SystemExit(f"unknown model(s): {', '.join(sorted(missing))}")
        return chosen
    return [m for m in models if not m.get("smoke_only")]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=str(ROOT / "harness" / "models.yaml"))
    parser.add_argument("--models", nargs="*", help="model names from the config (default: all non-smoke models)")
    parser.add_argument("--variants", nargs="*", help="default: config defaults.variants")
    parser.add_argument("--runs", type=int, help="attempts per model per variant (default: config)")
    parser.add_argument("--parallel", type=int, default=1, help="concurrent runs")
    parser.add_argument("--tag", help="prefix for run ids (e.g. a date) to keep separate result sets")
    parser.add_argument("--force", action="store_true", help="redo runs that already have scores")
    parser.add_argument("--retry-errors", action="store_true",
                        help="redo finished runs whose episode ended on an API/infrastructure error")
    args = parser.parse_args(argv)

    load_dotenv()
    config = load_config(args.config)
    defaults = config["defaults"]
    models = select_models(config, args.models)
    variants = args.variants or defaults.get("variants", ["canonical"])
    runs = args.runs or defaults.get("runs", 3)
    ensure_images()

    jobs = [(m, v, k) for m in models for v in variants for k in range(1, runs + 1)]
    print(f"{len(jobs)} runs: {len(models)} models x {len(variants)} variants x {runs} attempts")
    start = time.time()

    def job(spec):
        model_cfg, variant, k = spec
        try:
            return run_one(model_cfg, variant, k, defaults, tag=args.tag, force=args.force,
                           retry_errors=args.retry_errors)
        except Exception:
            print(f"[{model_cfg['name']} {variant} r{k}] crashed:\n{traceback.format_exc()}", file=sys.stderr)
            return None

    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        list(pool.map(job, jobs))
    print(f"done in {time.time() - start:.0f}s; results in {RESULTS}")


if __name__ == "__main__":
    main()
