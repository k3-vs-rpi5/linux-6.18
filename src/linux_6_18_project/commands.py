"""Fetch, configure, build, package and verify one kernel update."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import yaml

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
    apply_patches()
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


def apply_patches() -> list[str]:
    """Apply the declared patches to the pinned checkout, in order.

    The checkout keeps its commit; a patch is the only way source changes reach
    a build, and the list is checked against the tree so a half-applied set
    cannot be built by accident.
    """
    manifest_path = P.ROOT / "patches" / "manifest.yaml"
    if not manifest_path.is_file():
        return []
    entries = (yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}).get("patches") or []
    applied = []
    for entry in entries:
        patch = P.ROOT / "patches" / str(entry["file"])
        if not patch.is_file():
            raise P.ProjectError(f"patch listed but missing: {patch}")
        check = subprocess.run(["git", "-C", str(P.SOURCE), "apply", "--check",
                                "--reverse", str(patch)], capture_output=True, text=True)
        if check.returncode == 0:
            print(f"patch already applied: {patch.name}")
            applied.append(patch.name)
            continue
        P.run(["git", "-C", str(P.SOURCE), "apply", str(patch)])
        print(f"applied patch: {patch.name}")
        applied.append(patch.name)
    return applied


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
    applied_patch_names = apply_patches()
    P.DEBS.mkdir(parents=True, exist_ok=True)
    # A stale package in this directory is not harmless: the install door globs
    # it, and a first version of that door re-pointed the board's boot
    # environment at the *older* release it found there. Only the set produced
    # by this run stays.
    for stale in list(P.DEBS.glob("linux-*.deb")):
        stale.unlink()
    # mkdebian runs `git rev-parse` in the *output* directory, which is outside
    # the pinned checkout, so the packaging step is given the repository
    # explicitly instead of having the tree built in place.
    env = dict(os.environ,
               GIT_DIR=str(P.SOURCE / ".git"),
               GIT_WORK_TREE=str(P.SOURCE))
    # The headers package re-runs parts of the build inside a copied tree, where
    # the cross prefix is not always carried through and the bare
    # `riscv64-unknown-linux-gnu-gcc` is looked up on PATH.
    env["PATH"] = f"{P.CROSS_COMPILE.parent}:{env.get('PATH', '')}"
    # The vendored debian/rules builds tools/perf with a path relative to the
    # object tree, which an out-of-tree build does not have, and it leaves perf
    # output where it is built. Both are handled without touching the pinned
    # checkout: the object tree gets a `tools` symlink, and perf is told where
    # to put its output.
    tools_link = P.OUTPUT / "tools"
    if not tools_link.exists():
        tools_link.symlink_to(P.SOURCE / "tools")
    # perf has to be built where debian/rules then installs it from
    # (`$(srctree)/tools/perf/perf`), so its output is not redirected. The
    # checkout keeps its commit and its tracked files; the artefacts perf
    # leaves are cleaned afterwards and the manifest records that check.
    perf_opts = (f"prefix=/usr HAVE_NO_LIBBFD=1 HAVE_CPLUS_DEMANGLE_SUPPORT=1 "
                 f"CROSS_COMPILE={P.CROSS_COMPILE} NO_LIBPYTHON=1 NO_LIBPERL=1 "
                 f"NO_JEVENTS=1 WERROR=0")
    # The vendor's debian/rules also builds and packages perf, and it installs
    # perf from $(srctree)/tools/perf while an out-of-tree build puts it in the
    # object tree - so that ancillary package cannot be produced here. The
    # declared artefacts of this project are the kernel image, its headers and
    # libc-dev; the exit status and the missing ancillary packages are recorded
    # in the manifest instead of being smoothed over.
    command = P.make(identity, ["bindeb-pkg"], None,
                     extra={"KDEB_PKGVERSION": identity.deb_version,
                            "KBUILD_BUILD_VERSION": "1",
                            "PERF_BUILD_OPTS": perf_opts})
    printable = " ".join(command)
    print(f"+ {printable}", flush=True)
    result = subprocess.run(command, cwd=str(P.SOURCE), env=env)
    if result.returncode != 0:
        print(f"note: bindeb-pkg exited {result.returncode}; "
              "checking the declared artefacts before deciding", flush=True)
    produced = []
    candidates = (sorted(P.SOURCE.parent.glob("*.deb")) + sorted(P.OUTPUT.parent.glob("*.deb"))
                  + sorted(P.OUTPUT.glob("*.deb")))
    for candidate in candidates:
        if not candidate.is_file():
            continue
        target = P.DEBS / candidate.name
        shutil.move(str(candidate), target)
        produced.append(target)
    if not produced:
        raise P.ProjectError("bindeb-pkg produced no packages")
    names = [deb.name for deb in produced]
    required = [f"linux-image-{identity.uts_release}_", f"linux-headers-{identity.uts_release}_"]
    missing = [prefix for prefix in required
               if not any(name.startswith(prefix) for name in names)]
    if missing:
        raise P.ProjectError(
            f"bindeb-pkg exited {result.returncode} and the declared artefacts are missing: "
            f"{', '.join(missing)}")
    ancillary_missing = [] if result.returncode == 0 else ["linux-tools", "linux-tools-common"]

    # perf builds in place because debian/rules installs it from there; clean it
    # and record the proof that the pinned checkout's tracked files are intact.
    subprocess.run(["make", "-C", str(P.SOURCE / "tools" / "perf"), "clean"],
                   capture_output=True, text=True)
    status = subprocess.run(["git", "-C", str(P.SOURCE), "status", "--porcelain",
                             "--untracked-files=no"],
                            capture_output=True, text=True, check=True).stdout.strip()
    patched_files = {str(path) for entry in (yaml.safe_load(
        (P.ROOT / "patches" / "manifest.yaml").read_text(encoding="utf-8")) or {}
        ).get("patches", []) for path in entry.get("touches", [])}
    changed_files = {line.split()[-1] for line in status.splitlines() if line.strip()}
    undeclared = sorted(changed_files - patched_files)
    if undeclared:
        raise P.ProjectError(
            "the checkout differs from the lock outside the declared patches: "
            + ", ".join(undeclared))

    tree = (evidence or P.DEFAULT_EVIDENCE) / f"{identity.uts_release}"
    tree.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "identity": identity.__dict__,
        "source": P.load_lock()["source"],
        "base_config_sha256": P.base_config_digest(),
        "resolved_config_sha256": (P.OUTPUT / "resolved-config.sha256").read_text().split()[0],
        "update_delta": (P.update_delta(update).name if update is not None else None),
        "patches": applied_patch_names,
        "checkout_tracked_changes": status or "none",
        "bindeb_pkg_exit": result.returncode,
        "ancillary_packages_not_built": ancillary_missing,
        "declared_artefacts": required,
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
