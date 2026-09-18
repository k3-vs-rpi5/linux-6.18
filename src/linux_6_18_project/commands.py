"""Fetch, configure, build, package and verify one kernel update."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from . import project as P


def fetch() -> None:
    """Clone the pinned branch when the checkout is absent, then verify the commit."""
    lock = P.load_lock()["source"]

    if (P.SOURCE / "Makefile").is_file():
        commit = P.ensure_source()
        print(f"source present at {commit}")
        return
    P.SOURCE.parent.mkdir(parents=True, exist_ok=True)
    P.run(["git", "clone", "--depth", "1", "--branch", lock["ref"], lock["upstream"],
           str(P.SOURCE)], cwd=P.SOURCE.parent)
    commit = P.ensure_source()
    print(f"fetched {lock['ref']} at {commit}")


def configure(update: int | None) -> Path:
    """Base config plus the update delta, resolved by the kernel's own kconfig."""
    P.check_base_config()
    P.OUTPUT.mkdir(parents=True, exist_ok=True)
    if update is None:
        shutil.copyfile(P.BASE_CONFIG, P.OUTPUT / ".config")
        print(f"iteration build: base config only ({P.sha256(P.BASE_CONFIG)[:12]})")
    else:
        delta = P.update_delta(update)
        merged = P.OUTPUT / "merged.config"
        with merged.open("wb") as handle:
            handle.write(P.BASE_CONFIG.read_bytes())
            handle.write(b"\n")
            handle.write(delta.read_bytes())
        P.run([str(P.SOURCE / "scripts" / "kconfig" / "merge_config.sh"), "-m",
               "-O", str(P.OUTPUT), str(merged)], cwd=P.SOURCE)
        print(f"update{update}: base config + {delta.name}")
    resolved = P.OUTPUT / ".config"
    P.run(["make", "ARCH=riscv", f"CROSS_COMPILE={P.CROSS_COMPILE}",
           f"O={P.OUTPUT}", "olddefconfig"], cwd=P.SOURCE)
    digest = P.sha256(resolved)
    (P.OUTPUT / "resolved-config.sha256").write_text(
        f"{digest}  .config\n", encoding="utf-8")
    print(f"resolved config: {resolved} ({digest[:12]})")
    return resolved


def build(update: int | None, jobs: int | None = None,
          targets: tuple[str, ...] = ("Image", "dtbs", "modules")) -> P.Identity:
    identity = P.identity(update)

    print(f"building {identity.uts_release} with {P.CROSS_COMPILE.name}gcc")
    P.run(P.make(identity, list(targets), jobs))
    (P.OUTPUT / "identity.json").write_text(json.dumps(identity.__dict__, indent=2) + "\n",
                                            encoding="utf-8")
    image = P.OUTPUT / "arch" / "riscv" / "boot" / "Image"
    if not image.is_file():
        raise P.ProjectError(f"build finished without {image}")
    print(f"kernel image: {image} ({image.stat().st_size} bytes, "
          f"sha256 {P.sha256(image)[:12]})")
    return identity


def package(update: int | None, evidence: Path | None = None) -> list[Path]:
    """Produce the Debian packages and seal their identity into evidence."""
    identity = P.identity(update)
    P.DEBS.mkdir(parents=True, exist_ok=True)
    P.run(P.make(identity, ["bindeb-pkg"], None,
                 extra={"KDEB_PKGVERSION": identity.deb_version,
                        "KBUILD_BUILD_VERSION": "1"}))
    produced = []
    for candidate in sorted(P.SOURCE.parent.glob("*.deb")) + sorted(P.OUTPUT.glob("*.deb")):
        target = P.DEBS / candidate.name
        shutil.move(str(candidate), target)
        produced.append(target)
    if not produced:
        raise P.ProjectError("bindeb-pkg produced no packages")

    tree = (evidence or P.DEFAULT_EVIDENCE) / f"{identity.uts_release}"
    tree.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "identity": identity.__dict__,
        "source": P.load_lock()["source"],
        "base_config_sha256": P.base_config_digest(),
        "resolved_config_sha256": (P.OUTPUT / "resolved-config.sha256").read_text().split()[0],
        "update_delta": (P.update_delta(update).name if update is not None else None),
        "cross_compiler": {
            "path": str(P.CROSS_COMPILE) + "gcc",
            "version": subprocess.run([str(P.CROSS_COMPILE) + "gcc", "--version"],
                                      capture_output=True, text=True, check=True).stdout.splitlines()[0],
            "sha256": P.sha256(Path(str(P.CROSS_COMPILE) + "gcc")),
        },
        "packages": [
            {"name": deb.name, "bytes": deb.stat().st_size, "sha256": P.sha256(deb)}
            for deb in produced
        ],
    }
    (tree / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                              encoding="utf-8")
    shutil.copyfile(P.OUTPUT / ".config", tree / "resolved.config")
    (tree / "SHA256SUMS").write_text(
        "".join(f"{P.sha256(path)}  {path.name}\n" for path in sorted(tree.iterdir())
                if path.name != "SHA256SUMS"), encoding="utf-8")
    print(f"packages: {', '.join(deb.name for deb in produced)}")
    print(f"evidence: {tree}")
    for deb in produced:
        print(f"  {deb.name}  {deb.stat().st_size} bytes  {P.sha256(deb)[:16]}")
    return produced


def verify(update: int | None) -> None:
    """Assert the built artefacts agree with the declared identity."""
    identity = P.identity(update)
    image = P.OUTPUT / "arch" / "riscv" / "boot" / "Image"
    issues = []

    if not image.is_file():
        issues.append(f"missing kernel image {image}")
    else:
        strings = subprocess.run(["strings", str(image)], capture_output=True, text=True).stdout
        if identity.uts_release not in strings:
            issues.append(f"{image} does not carry {identity.uts_release}")
    expected_prefix = f"linux-image-{identity.uts_release}"
    if not any(deb.name.startswith(expected_prefix) for deb in P.DEBS.glob("*.deb")):
        issues.append(f"no package named {expected_prefix}* in {P.DEBS}")
    if issues:
        raise P.ProjectError("; ".join(issues))
    print(f"verify: {identity.uts_release} image and packages agree")
