"""Run a candidate emulator on a test suite, in a fresh grading container or locally.

Only the candidate's ``emulator.py`` plus test inputs (ROMs, key files, run
arguments) are staged; golden outputs stay on the host.

    python -m grader.run_candidate --emulator path/emulator.py --suite tests_data/hidden/canonical \
        --out outputs.json [--local]
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRADER_IMAGE = "chip8-eval-grader:latest"
DRIVER = Path(__file__).resolve().parent / "driver.py"
TEST_TIMEOUT = 10.0


def stage(emulator, suite_dir, dest):
    """Copy emulator.py and test inputs (never goldens) into ``dest``."""
    suite_dir = Path(suite_dir)
    dest = Path(dest)
    manifest = json.loads((suite_dir / "manifest.json").read_text())
    (dest / "roms").mkdir(parents=True)
    (dest / "keys").mkdir()
    shutil.copyfile(emulator, dest / "emulator.py")
    inputs = []
    for test in manifest["tests"]:
        shutil.copyfile(suite_dir / test["rom"], dest / test["rom"])
        if test["keys"]:
            shutil.copyfile(suite_dir / test["keys"], dest / test["keys"])
        inputs.append({k: test[k] for k in ("id", "rom", "keys", "cycles", "seed", "frames_at")})
    (dest / "inputs.json").write_text(json.dumps({"tests": inputs}))
    return len(inputs)


def parse_driver_output(text, expected_count):
    results = {}
    done = False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        if record.get("done"):
            done = True
            continue
        results[record["id"]] = record
    if not done or len(results) != expected_count:
        raise RuntimeError(f"grading driver incomplete: {len(results)}/{expected_count} results, done={done}")
    return results


def run_local(emulator, suite_dir, workers=8, timeout=TEST_TIMEOUT):
    """Run on the host. Only for trusted candidates (reference, mutants)."""
    with tempfile.TemporaryDirectory(prefix="chip8-grade-") as tmp:
        count = stage(emulator, suite_dir, tmp)
        proc = subprocess.run(
            [sys.executable, str(DRIVER), tmp, "--workers", str(workers), "--timeout", str(timeout)],
            capture_output=True, text=True, check=True,
        )
    return parse_driver_output(proc.stdout, count)


def docker_available():
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def build_grader_image():
    subprocess.run(["docker", "build", "-q", "-f", str(ROOT / "docker" / "grader.Dockerfile"),
                    "-t", GRADER_IMAGE, str(ROOT)], check=True, capture_output=True)


def run_docker(emulator, suite_dir, workers=2, cpus=2, memory="2g", timeout=TEST_TIMEOUT, overall_timeout=1800):
    """Run in a fresh, network-less grading container; the candidate runs as an unprivileged user."""
    name = f"chip8-grade-{uuid.uuid4().hex[:12]}"
    with tempfile.TemporaryDirectory(prefix="chip8-grade-") as tmp:
        count = stage(emulator, suite_dir, tmp)
        create = [
            "docker", "create", "--name", name,
            "--network", "none",
            f"--cpus={cpus}", f"--memory={memory}", f"--memory-swap={memory}", "--pids-limit=256",
            "--security-opt", "no-new-privileges",
            "--cap-drop", "ALL", "--cap-add", "SETUID", "--cap-add", "SETGID", "--cap-add", "KILL",
            "--cap-add", "CHOWN",
            GRADER_IMAGE,
            "python", "/opt/grader/driver.py", "/grade",
            "--workers", str(workers), "--timeout", str(timeout), "--user", "candidate",
        ]
        subprocess.run(create, check=True, capture_output=True)
        try:
            subprocess.run(["docker", "cp", f"{tmp}/.", f"{name}:/grade"], check=True, capture_output=True)
            proc = subprocess.run(["docker", "start", "-a", name], capture_output=True, text=True,
                                  timeout=overall_timeout)
            if proc.returncode != 0:
                raise RuntimeError(f"grading container failed: {proc.stderr[-2000:]}")
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    return parse_driver_output(proc.stdout, count)


def run_candidate(emulator, suite_dir, mode="docker", **kwargs):
    if mode == "local":
        return run_local(emulator, suite_dir, **kwargs)
    if mode == "docker":
        return run_docker(emulator, suite_dir, **kwargs)
    raise ValueError(mode)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--emulator", required=True)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--local", action="store_true", help="run on the host (trusted code only)")
    args = parser.parse_args(argv)
    outputs = run_candidate(args.emulator, args.suite, mode="local" if args.local else "docker")
    Path(args.out).write_text(json.dumps(outputs))
    print(f"wrote {len(outputs)} results to {args.out}")


if __name__ == "__main__":
    main()
