#!/usr/bin/env python3
"""Door implementation for the linux-6.18 project.

    cli.py fetch                          clone the pinned branch and verify the commit
    cli.py config [--release N]           base config plus update N's delta
    cli.py build  [--release N] [--jobs J] [--targets Image,dtbs,modules]
    cli.py package [--release N] [--evidence DIR]
    cli.py release N [--jobs J] [--evidence DIR]     configure, build, package, verify
    cli.py verify [--release N]
    cli.py info                           lock, identity and artefact status
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import commands, project as P


def _summary() -> None:
    lock = P.load_lock()
    print(f"project      : {P.ROOT}")
    print(f"source       : {lock['source']['upstream']} {lock['source']['ref']}")
    print(f"commit       : {lock['source']['commit']}")
    print(f"base config  : {lock['base_config']['file']} sha256 {lock['base_config']['sha256'][:16]}")
    print(f"updates      : {', '.join(sorted(lock.get('updates', {}))) or 'none declared'}")
    print(f"work tree    : {P.OUTPUT}")
    print(f"packages     : {P.DEBS}")
    if (P.SOURCE / "Makefile").is_file():
        print(f"identity     : {P.identity(None).uts_release} (iteration build)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="linux-6.18")
    parser.add_argument("--release", type=int, help="frozen update number")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch")
    for name in ("config", "build", "package", "verify"):
        command = sub.add_parser(name)
        command.add_argument("--release", type=int, help="frozen update number")
        if name == "build":
            command.add_argument("--jobs", type=int)
            command.add_argument("--targets", default="Image,dtbs,modules")
        if name == "package":
            command.add_argument("--evidence", type=Path)
    release = sub.add_parser("release")
    release.add_argument("number", type=int)
    release.add_argument("--jobs", type=int)
    release.add_argument("--evidence", type=Path)
    sub.add_parser("info")
    args = parser.parse_args(argv)

    try:
        if args.command == "info":
            _summary()
        elif args.command == "fetch":
            commands.fetch()
        elif args.command == "config":
            commands.configure(args.release)
        elif args.command == "build":
            commands.configure(args.release)
            commands.build(args.release, args.jobs, tuple(args.targets.split(",")))
        elif args.command == "package":
            commands.package(args.release, args.evidence)
        elif args.command == "verify":
            commands.verify(args.release)
        elif args.command == "release":
            commands.configure(args.number)
            commands.build(args.number, args.jobs)
            commands.package(args.number, args.evidence)
            commands.verify(args.number)
        return 0
    except (P.ProjectError, FileNotFoundError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
