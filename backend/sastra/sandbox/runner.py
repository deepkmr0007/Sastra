"""Isolated code execution.

Backends, strongest first:
  docker   disposable container: no network, read-only root, all capabilities
           dropped, no-new-privileges, non-root user, pid/memory/CPU limits,
           only the project workspace mounted. Optional gVisor (runsc) runtime.
  process  WEAK fallback for development: separate network namespace
           (unshare -rn) when available, rlimits, empty environment, timeout,
           process-group kill. It shares the host filesystem view — never use
           it for untrusted code on a machine holding secrets. Disabled unless
           SASTRA_ALLOW_WEAK_SANDBOX=1.

Docker alone is not a complete security boundary (shared kernel). See
docs/THREAT_MODEL.md for residual risks and the gVisor/Firecracker upgrade path.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

LANG_IMAGES = {"python": "python:3.12-slim", "javascript": "node:22-slim", "bash": "debian:stable-slim"}
LANG_CMDS = {"python": ["python3", "-I", "-B"], "javascript": ["node"], "bash": ["bash", "--noprofile", "--norc"]}
MAX_OUTPUT = 200_000


class SandboxUnavailable(Exception):
    pass


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool
    backend: str
    isolation: str  # strong | weak

    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _clip(b: bytes) -> str:
    s = b.decode("utf-8", "replace")
    return s if len(s) <= MAX_OUTPUT else s[:MAX_OUTPUT] + f"\n…[truncated {len(s) - MAX_OUTPUT} chars]"


class DockerRunner:
    backend = "docker"
    isolation = "strong"

    def __init__(self, memory_mb: int = 512, cpus: float = 1.0, pids: int = 128, runtime: str | None = None,
                 egress_proxy: str | None = None):
        self.memory_mb, self.cpus, self.pids, self.runtime, self.egress_proxy = memory_mb, cpus, pids, runtime, egress_proxy

    @staticmethod
    def available() -> bool:
        if not shutil.which("docker"):
            return False
        try:
            return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0  # noqa: S603,S607
        except Exception:  # noqa: BLE001
            return False

    def command(self, workdir: Path, language: str, entry: list[str], network: str) -> list[str]:
        cmd = ["docker", "run", "--rm", "-i",
               "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
               "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
               "--pids-limit", str(self.pids), "--memory", f"{self.memory_mb}m", "--memory-swap", f"{self.memory_mb}m",
               "--cpus", str(self.cpus), "--user", "65534:65534", "--ulimit", "fsize=104857600",
               "-v", f"{workdir.resolve()}:/work:rw", "-w", "/work", "-e", "HOME=/tmp"]
        if self.runtime:
            cmd += ["--runtime", self.runtime]
        if network == "packages" and self.egress_proxy:
            cmd += ["--network", "sastra-egress", "-e", f"HTTPS_PROXY={self.egress_proxy}", "-e", f"HTTP_PROXY={self.egress_proxy}"]
        else:
            cmd += ["--network", "none"]
        return cmd + [LANG_IMAGES[language]] + LANG_CMDS[language] + entry

    def run(self, workdir: Path, language: str, entry: list[str], timeout_s: int, network: str = "none", stdin: bytes = b"") -> RunResult:
        if network == "packages" and not self.egress_proxy:
            raise SandboxUnavailable("Package downloads need the allowlisted egress proxy (deploy/squid). Not configured.")
        return _execute(self.command(workdir, language, entry, network), workdir, timeout_s, self.backend, self.isolation,
                        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")}, stdin=stdin)


class ProcessRunner:
    backend = "process"
    isolation = "weak"

    def __init__(self, memory_mb: int = 512):
        self.memory_mb = memory_mb
        self.netns = self._netns_ok()

    @staticmethod
    def _netns_ok() -> bool:
        if not shutil.which("unshare"):
            return False
        try:
            return subprocess.run(["unshare", "-rn", "true"], capture_output=True, timeout=5).returncode == 0  # noqa: S603,S607
        except Exception:  # noqa: BLE001
            return False

    def run(self, workdir: Path, language: str, entry: list[str], timeout_s: int, network: str = "none", stdin: bytes = b"") -> RunResult:
        if network != "none":
            raise SandboxUnavailable("The weak process sandbox never allows network access")
        interp = shutil.which(LANG_CMDS[language][0])
        if not interp:
            raise SandboxUnavailable(f"{LANG_CMDS[language][0]} not installed on this host")
        cmd = [interp, *LANG_CMDS[language][1:], *entry]
        if self.netns:
            cmd = ["unshare", "-rn", "--", *cmd]
        mem = self.memory_mb * 1024 * 1024

        def limits():  # runs in the child before exec
            import resource
            os.setsid()
            resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
            resource.setrlimit(resource.RLIMIT_CPU, (timeout_s, timeout_s + 1))
            resource.setrlimit(resource.RLIMIT_FSIZE, (50 * 1024 * 1024, 50 * 1024 * 1024))
            resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(workdir), "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
        return _execute(cmd, workdir, timeout_s, self.backend, self.isolation + ("+netns" if self.netns else ""), env=env,
                        preexec=limits, stdin=stdin)


def _execute(cmd: list[str], cwd: Path, timeout_s: int, backend: str, isolation: str, env: dict,
             preexec=None, stdin: bytes = b"") -> RunResult:
    started = time.perf_counter()
    proc = subprocess.Popen(cmd, cwd=str(cwd), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,  # noqa: S603
                            stderr=subprocess.PIPE, preexec_fn=preexec, start_new_session=preexec is None)
    timed_out = False
    try:
        out, err = proc.communicate(input=stdin, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        out, err = proc.communicate()
    return RunResult(exit_code=proc.returncode if not timed_out else -9, stdout=_clip(out), stderr=_clip(err),
                     duration_ms=int((time.perf_counter() - started) * 1000), timed_out=timed_out,
                     backend=backend, isolation=isolation)


def build_runner(settings):
    choice = settings.sandbox_backend
    if choice in ("auto", "docker") and DockerRunner.available():
        return DockerRunner(memory_mb=settings.sandbox_memory_mb, runtime=os.environ.get("SASTRA_SANDBOX_RUNTIME"),
                            egress_proxy=os.environ.get("SASTRA_SANDBOX_EGRESS_PROXY"))
    if choice == "docker":
        return None
    if choice in ("auto", "process") and settings.allow_weak_sandbox:
        return ProcessRunner(memory_mb=settings.sandbox_memory_mb)
    return None
