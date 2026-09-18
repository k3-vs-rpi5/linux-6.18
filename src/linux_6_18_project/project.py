"""Paths, lock data and the version identity of a build."""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parents[1]
SOURCE = ROOT / "src" / "linux-6.18"
LOCK = ROOT / "sources.lock.yaml"
BASE_CONFIG = ROOT / "config" / "base.config"
WORK = WORKSPACE / ".local" / "linux-6.18"
OUTPUT = WORK / "out"
DEBS = WORK / "debs"
DEFAULT_EVIDENCE = Path("/root/k3-evidence/linux-6.18")
TOOLCHAIN = WORKSPACE / "spacemit-toolchain-linux-glibc-x86_64-v1.2.4"
CROSS_COMPILE = TOOLCHAIN / "bin" / "riscv64-unknown-linux-gnu-"


class ProjectError(RuntimeError):
    """A contract the project declares could not be honoured."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_lock() -> dict:
    return yaml.safe_load(LOCK.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Identity:
    """What this build calls itself, on the board and in its packages."""

    release: str          # 6.18.3
    localversion: str     # -generic-update1 or -generic-dev+<short>
    uts_release: str      # 6.18.3-generic-update1
    deb_version: str      # 6.18.3-generic-update1-1
    update: int | None    # None for an iteration build


def ensure_source() -> str:
    """Verify the checkout exists at the locked commit and return that commit."""
    lock = load_lock()["source"]

    if not (SOURCE / "Makefile").is_file():
        raise ProjectError(f"pinned source is missing: {SOURCE} (run the fetch verb)")
    head = subprocess.run(["git", "-C", str(SOURCE), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    if head != lock["commit"]:
        raise ProjectError(f"checkout is {head}, lock says {lock['commit']}")
    return head


def kernel_release() -> str:
    """The upstream version from the tree itself, never a hard-coded constant."""
    if not (SOURCE / "Makefile").is_file():
        raise ProjectError(f"pinned source is missing: {SOURCE} (run the fetch verb)")
    result = subprocess.run(["make", "-s", "-C", str(SOURCE), "kernelversion"],
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


def identity(update: int | None = None) -> Identity:
    """Frozen updates carry their number; iteration builds never do."""
    release = kernel_release()
    commit = ensure_source()

    if update is None:
        localversion = f"-generic-dev+{commit[:12]}"
        uts = f"{release}{localversion}"
        return Identity(release, localversion, uts, f"{uts}-1", None)
    localversion = f"-generic-update{update}"
    uts = f"{release}{localversion}"
    return Identity(release, localversion, uts, f"{uts}-1", update)


def update_delta(update: int) -> Path:
    path = ROOT / "config" / f"update{update}.config"

    if not path.is_file():
        raise ProjectError(f"update{update} has no declared config delta: {path}")
    return path


def base_config_digest() -> str:
    lock = load_lock()

    return str(lock["base_config"]["sha256"])


def check_base_config() -> None:
    actual = sha256(BASE_CONFIG)
    expected = base_config_digest()

    if actual != expected:
        raise ProjectError(f"base config digest {actual} != lock {expected}")


def make(identity_: Identity, targets: list[str], jobs: int | None, extra: dict[str, str] | None = None,
         dry_run: bool = False) -> list[str]:
    """The one place a kernel make invocation is assembled."""
    if jobs is None:
        jobs = len(os.sched_getaffinity(0)) or 4
    command = [
        "make", f"-j{jobs}", "ARCH=riscv", f"CROSS_COMPILE={CROSS_COMPILE}",
        f"O={OUTPUT}", f"LOCALVERSION={identity_.localversion}",
    ]
    command += targets
    for key, value in (extra or {}).items():
        command.append(f"{key}={value}")
    return command


def run(command: list[str], cwd: Path | None = None) -> None:
    printable = " ".join(command)

    print(f"+ {printable}", flush=True)
    subprocess.run(command, cwd=str(cwd or SOURCE), check=True)


def numeric_suffix(value: str) -> int:
    match = re.search(r"(\d+)$", value)

    if not match:
        raise ProjectError(f"no numeric suffix in {value!r}")
    return int(match.group(1))
