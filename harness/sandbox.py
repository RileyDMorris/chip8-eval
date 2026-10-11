"""Docker sandbox for one agent episode.

The container runs ``chip8-eval-agent`` (python:3.12-slim, non-root ``agent``
user, ``/workspace``) with ``--network none``, CPU/memory/pids limits, and all
capabilities dropped. Only ``tasks/<variant>/`` is copied in. Model API calls
happen on the host; no keys ever enter the container.
"""

import io
import posixpath
import subprocess
import tarfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT_IMAGE = "chip8-eval-agent:latest"
WORKSPACE = "/workspace"
AGENT_UID = 1000
BASH_TIMEOUT = 60
MAX_OUTPUT_CHARS = 10_000
MAX_READ_CHARS = 100_000


class SandboxError(Exception):
    pass


def truncate(text, limit=MAX_OUTPUT_CHARS):
    """Keep the head and tail of long output (errors are usually at the end)."""
    if len(text) <= limit:
        return text
    head = limit * 2 // 5
    tail = limit - head
    omitted = len(text) - head - tail
    return f"{text[:head]}\n\n[... {omitted} characters omitted ...]\n\n{text[-tail:]}"


def resolve_workspace_path(path):
    """Map an agent-supplied path to an absolute path inside /workspace, or raise."""
    if not isinstance(path, str) or not path.strip():
        raise SandboxError("path must be a non-empty string")
    if "\x00" in path:
        raise SandboxError("path contains a NUL byte")
    full = path if path.startswith("/") else posixpath.join(WORKSPACE, path)
    full = posixpath.normpath(full)
    if full != WORKSPACE and not full.startswith(WORKSPACE + "/"):
        raise SandboxError(f"path must be inside {WORKSPACE}: {path}")
    return full


def build_agent_image():
    subprocess.run(["docker", "build", "-q", "-f", str(ROOT / "docker" / "agent.Dockerfile"),
                    "-t", AGENT_IMAGE, str(ROOT)], check=True, capture_output=True)


def ensure_images():
    """Build the agent and grader images if they are missing."""
    from grader.run_candidate import GRADER_IMAGE, build_grader_image

    for image, build in ((AGENT_IMAGE, build_agent_image), (GRADER_IMAGE, build_grader_image)):
        if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode != 0:
            build()


def tar_directory(src, uid=AGENT_UID):
    """Tar ``src`` with files owned by the agent user."""
    src = Path(src)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for path in sorted(src.rglob("*")):
            info = tar.gettarinfo(str(path), arcname=str(path.relative_to(src)))
            info.uid = info.gid = uid
            info.uname = info.gname = "agent"
            info.mode = 0o755 if path.is_dir() else 0o644
            if path.is_file():
                with open(path, "rb") as f:
                    tar.addfile(info, f)
            else:
                tar.addfile(info)
    return buf.getvalue()


class Sandbox:
    def __init__(self, name=None, cpus=2, memory="2g", pids=256, image=AGENT_IMAGE):
        self.name = name or f"chip8-agent-{uuid.uuid4().hex[:12]}"
        self.cpus = cpus
        self.memory = memory
        self.pids = pids
        self.image = image
        self.started = False

    # -- lifecycle -------------------------------------------------------------

    def start(self):
        cmd = [
            "docker", "run", "-d", "--name", self.name,
            "--network", "none",
            f"--cpus={self.cpus}", f"--memory={self.memory}", f"--memory-swap={self.memory}",
            f"--pids-limit={self.pids}",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", "agent", "--workdir", WORKSPACE,
            self.image, "sleep", "infinity",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise SandboxError(f"docker run failed: {proc.stderr.strip()}")
        self.started = True
        return self

    def stop(self):
        if self.started:
            subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
            self.started = False

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    def copy_task(self, task_dir):
        """Copy the public task materials into /workspace, owned by the agent user."""
        data = tar_directory(task_dir)
        proc = subprocess.run(["docker", "cp", "-a", "-", f"{self.name}:{WORKSPACE}"], input=data, capture_output=True)
        if proc.returncode != 0:
            raise SandboxError(f"docker cp failed: {proc.stderr.decode(errors='replace')}")

    def copy_out(self, path, dest):
        """Copy a file out of the container. Returns False if it does not exist."""
        full = resolve_workspace_path(path)
        proc = subprocess.run(["docker", "exec", self.name, "test", "-f", full], capture_output=True)
        if proc.returncode != 0:
            return False
        data = subprocess.run(["docker", "exec", self.name, "cat", "--", full], capture_output=True, check=True).stdout
        Path(dest).write_bytes(data)
        return True

    # -- tools -----------------------------------------------------------------

    def bash(self, command, timeout=BASH_TIMEOUT):
        """Run a shell command in /workspace. Returns (exit_code, output, timed_out)."""
        if not isinstance(command, str):
            raise SandboxError("command must be a string")
        cmd = ["docker", "exec", "-u", "agent", "-w", WORKSPACE, self.name,
               "timeout", "-k", "5", str(timeout), "bash", "-c", command]
        start = time.monotonic()
        try:
            proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  timeout=timeout + 20)
            code = proc.returncode
            output = proc.stdout.decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired as e:
            code = 124
            output = (e.stdout or b"").decode("utf-8", errors="replace")
        timed_out = code == 124 or (code in (137, -9) and time.monotonic() - start >= timeout)
        return code, output, timed_out

    def read_file(self, path):
        full = resolve_workspace_path(path)
        proc = subprocess.run(["docker", "exec", "-u", "agent", self.name, "cat", "--", full],
                              capture_output=True, timeout=30)
        if proc.returncode != 0:
            raise SandboxError(proc.stderr.decode("utf-8", errors="replace").strip() or f"cannot read {full}")
        return proc.stdout.decode("utf-8", errors="replace")

    def write_file(self, path, content):
        full = resolve_workspace_path(path)
        if not isinstance(content, str):
            raise SandboxError("content must be a string")
        script = 'mkdir -p -- "$(dirname -- "$1")" && cat > "$1"'
        proc = subprocess.run(["docker", "exec", "-i", "-u", "agent", self.name, "sh", "-c", script, "sh", full],
                              input=content.encode("utf-8"), capture_output=True, timeout=30)
        if proc.returncode != 0:
            raise SandboxError(proc.stderr.decode("utf-8", errors="replace").strip() or f"cannot write {full}")
        return full
