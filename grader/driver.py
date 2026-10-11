"""Grading driver: runs a candidate emulator on every test input and streams raw results.

Runs inside the grading container (as root, so it can run the candidate as an
unprivileged user it cannot interfere with) or locally for trusted candidates.
It sees only test *inputs* (ROMs, key files, run arguments); golden outputs
never reach this process. Comparison happens on the host (grader/score.py).

    python driver.py GRADE_DIR [--workers N] [--timeout S] [--user NAME]

GRADE_DIR contains emulator.py, inputs.json, roms/, keys/. Prints one JSON
object per line: {"id", "status", "returncode", "stdout", "stderr_tail", "elapsed"}.
Standard library only.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

MAX_STDOUT = 4 * 1024 * 1024
STDERR_TAIL = 2000


def build_command(grade_dir, test):
    cmd = [sys.executable, os.path.join(grade_dir, "emulator.py"),
           "--rom", os.path.join(grade_dir, test["rom"]), "--cycles", str(test["cycles"])]
    if test.get("keys"):
        cmd += ["--keys", os.path.join(grade_dir, test["keys"])]
    if test.get("seed", 1) != 1:
        cmd += ["--seed", str(test["seed"])]
    if test.get("frames_at"):
        cmd += ["--frames-at", ",".join(str(c) for c in test["frames_at"])]
    return cmd


def run_one(grade_dir, test, timeout, user):
    cmd = build_command(grade_dir, test)
    workdir = tempfile.mkdtemp(prefix="run-")
    if user:
        import pwd
        entry = pwd.getpwnam(user)
        os.chmod(workdir, 0o711)  # root (no CAP_DAC_OVERRIDE) must still chdir into it
        os.chown(workdir, entry.pw_uid, entry.pw_gid)
    extra = {"user": user, "group": user, "extra_groups": []} if user else {}
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": workdir, "PYTHONDONTWRITEBYTECODE": "1"}
    result = {"id": test["id"]}
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        start = time.monotonic()
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=err, cwd=workdir,
                                env=env, start_new_session=True, **extra)
        try:
            returncode = proc.wait(timeout=timeout)
            result["status"] = "exited"
            result["returncode"] = returncode
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            result["status"] = "timeout"
            result["returncode"] = None
        result["elapsed"] = round(time.monotonic() - start, 3)
        out.seek(0)
        data = out.read(MAX_STDOUT + 1)
        if len(data) > MAX_STDOUT:
            result["status"] = "output_too_large"
            data = b""
        result["stdout"] = data.decode("utf-8", errors="replace")
        err.seek(0, os.SEEK_END)
        size = err.tell()
        err.seek(max(0, size - STDERR_TAIL))
        result["stderr_tail"] = err.read().decode("utf-8", errors="replace")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("grade_dir")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--user", default=None, help="run the candidate as this user (needs root)")
    args = parser.parse_args(argv)

    with open(os.path.join(args.grade_dir, "inputs.json")) as f:
        tests = json.load(f)["tests"]
    lock = threading.Lock()

    def work(test):
        try:
            result = run_one(args.grade_dir, test, args.timeout, args.user)
        except Exception as e:  # never let one test take down the run
            result = {"id": test["id"], "status": "driver_error", "returncode": None,
                      "stdout": "", "stderr_tail": repr(e), "elapsed": 0}
        with lock:
            sys.stdout.write(json.dumps(result) + "\n")
            sys.stdout.flush()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(work, tests))
    sys.stdout.write(json.dumps({"done": True, "count": len(tests)}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
