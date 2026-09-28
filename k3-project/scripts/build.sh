#!/bin/sh
# The kernel door: configure, build and package the SpacemiT 6.18 kernel.
#
#   scripts/build.sh                      iteration build, version 6.18.3-generic-dev+<sha>
#   scripts/build.sh --release 1          frozen update 1, version 6.18.3-generic-update1
#   scripts/build.sh --fetch              clone the pinned branch first
#   scripts/build.sh --config-only [--release N]
#   scripts/build.sh --info
#
# Only a frozen release carries an update number; an iteration build never does,
# so a package lying around a machine always says what it is.
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname "$0")" && pwd)
PROJECT_ROOT=$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd)

PYTHON=${LINUX618_PYTHON:-python3}
unset PYTHONHOME
PYTHONNOUSERSITE=1
PYTHONPATH=$PROJECT_ROOT/src
export PYTHONNOUSERSITE PYTHONPATH

run_cli() {
    exec "$PYTHON" -P -m linux_6_18_project.cli "$@"
}

case "${1:-}" in
    fetch|config|package|verify|info)
        run_cli "$@"
        ;;
    --fetch)
        shift
        run_cli fetch "$@"
        ;;
    --config-only)
        shift
        run_cli config "$@"
        ;;
    --info)
        shift
        run_cli info "$@"
        ;;
    --release)
        shift
        number=$1
        shift
        run_cli release "$number" "$@"
        ;;
    *)
        run_cli build "$@"
        ;;
esac
