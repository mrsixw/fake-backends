"""Which container runtime is on this machine, and how to drive it.

CI runs docker. A macOS developer may instead have Apple's ``container``,
which runs Linux containers in lightweight VMs. The two CLIs agree on the few
verbs needed here (``run -d --name -p``, ``exec``, ``logs``, ``rm -f``), so
one thin seam supports both. Ported from jeeves' ``tests/e2e/runtime.py``.

Deliberately not a general abstraction: it covers exactly what the launcher
needs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from enum import StrEnum

__all__ = [
    "CANDIDATES",
    "ContainerRuntime",
    "RuntimeKind",
    "RuntimeUnavailable",
    "detect_runtime",
]

# Docker first: it is what CI uses, so when both are installed a local run
# matches CI.
CANDIDATES = ("docker", "container")

# Named limits for each kind of CLI call. A pull on a cold cache is the slow one.
_PROBE_TIMEOUT_SECONDS = 20
_RUN_TIMEOUT_SECONDS = 300
_QUICK_TIMEOUT_SECONDS = 60
# The in-container health probe's own HTTP timeout.
_INTERNAL_PROBE_TIMEOUT_SECONDS = 5


class RuntimeKind(StrEnum):
    DOCKER = "docker"
    APPLE = "apple"


class RuntimeUnavailable(RuntimeError):
    """No usable container runtime, or a container command failed."""


class ContainerRuntime:
    def __init__(self, name: str, path: str) -> None:
        self.name = name
        self._path = path
        self.kind = RuntimeKind.DOCKER if name == "docker" else RuntimeKind.APPLE

    def __repr__(self) -> str:
        return f"<ContainerRuntime {self.name}>"

    def run(
        self, *args: str, timeout: float, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        try:
            completed = subprocess.run(
                [self._path, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeUnavailable(
                f"`{self.name} {' '.join(args)}` did not finish within {timeout}s"
            ) from exc
        if check and completed.returncode != 0:
            raise RuntimeUnavailable(
                f"`{self.name} {' '.join(args)}` failed with {completed.returncode}\n"
                f"--- stdout ---\n{completed.stdout}\n"
                f"--- stderr ---\n{completed.stderr}"
            )
        return completed

    def healthy(self) -> tuple[bool, str]:
        """Does the runtime's daemon answer?

        A present binary is not a working runtime: Docker Desktop may be
        stopped, and Apple's ``container`` needs ``container system start``.
        Listing containers is the cheapest call that proves it answers.
        """
        verb = "ps" if self.kind is RuntimeKind.DOCKER else "ls"
        try:
            completed = self.run(verb, timeout=_PROBE_TIMEOUT_SECONDS, check=False)
        except (OSError, RuntimeUnavailable) as exc:
            return False, f"{self.name} could not be invoked: {exc}"
        if completed.returncode != 0:
            hint = (
                "is Docker running?"
                if self.kind is RuntimeKind.DOCKER
                else "run `container system start`"
            )
            return False, f"{self.name} is installed but not answering: {hint}"
        return True, ""

    def start(
        self,
        *,
        image: str,
        name: str,
        host_port: int,
        container_port: int,
        env: dict[str, str],
    ) -> None:
        """Start a detached container publishing one port on loopback only."""
        env_args = [
            arg for key, value in env.items() for arg in ("-e", f"{key}={value}")
        ]
        self.run(
            "run",
            "-d",
            "--name",
            name,
            "-p",
            f"127.0.0.1:{host_port}:{container_port}",
            *env_args,
            image,
            timeout=_RUN_TIMEOUT_SECONDS,
        )

    def remove(self, name: str) -> None:
        """Stop and remove a container, tolerating one that is already gone."""
        self.run("rm", "-f", name, timeout=_QUICK_TIMEOUT_SECONDS, check=False)

    def running(self, name: str) -> bool:
        verb = "ps" if self.kind is RuntimeKind.DOCKER else "ls"
        completed = self.run(verb, timeout=_QUICK_TIMEOUT_SECONDS, check=False)
        return name in (completed.stdout or "")

    def serves_internally(self, name: str, port: int, path: str) -> bool:
        """Does the fake answer ``path`` from inside its own container?

        If it does but the published port does not, the fault is the host's
        routing, not the fake. The images have Python and no curl, so the
        probe uses the interpreter.
        """
        probe = (
            "import urllib.request; "
            f"urllib.request.urlopen('http://127.0.0.1:{port}{path}', "
            f"timeout={_INTERNAL_PROBE_TIMEOUT_SECONDS})"
        )
        completed = self.run(
            "exec",
            name,
            "python",
            "-c",
            probe,
            timeout=_QUICK_TIMEOUT_SECONDS,
            check=False,
        )
        return completed.returncode == 0

    def container_ip(self, name: str) -> str | None:
        """The container's own IPv4 address, on Apple's runtime only.

        Apple gives each container a routable address. On some Macs that
        address answers while published ports do not, so it is the fallback.
        """
        if self.kind is not RuntimeKind.APPLE:
            return None
        completed = self.run(
            "inspect", name, timeout=_QUICK_TIMEOUT_SECONDS, check=False
        )
        try:
            info = json.loads(completed.stdout)[0]
            address = info["status"]["networks"][0]["ipv4Address"]
        except (ValueError, LookupError, TypeError):
            return None
        return address.split("/", 1)[0]

    def logs(self, name: str) -> str:
        completed = self.run("logs", name, timeout=_QUICK_TIMEOUT_SECONDS, check=False)
        return (completed.stdout or "") + (completed.stderr or "")


def detect_runtime() -> ContainerRuntime:
    """The first healthy runtime, or ``RuntimeUnavailable`` with every reason.

    "docker is installed but not answering" is far more useful than "no
    container runtime", so each candidate's reason is kept.
    """
    reasons = []
    for candidate in CANDIDATES:
        path = shutil.which(candidate)
        if path is None:
            reasons.append(f"{candidate}: not installed")
            continue
        runtime = ContainerRuntime(candidate, path)
        ok, why = runtime.healthy()
        if ok:
            return runtime
        reasons.append(why)
    raise RuntimeUnavailable("no usable container runtime: " + "; ".join(reasons))
