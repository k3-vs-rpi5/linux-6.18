#!/bin/sh
# The board door for the kernel project.
#
#   scripts/run.sh --status                      kernel, packages and devices on the board
#   scripts/run.sh --install <deb-dir> [--reboot] push and install kernel packages
#   scripts/run.sh --devices off|on|status       disable or restore devices a test does not need
#
# Board credentials come from the workspace record .ssh-k3, are read inside this
# process only, and are never printed. Installing a kernel is a remote change:
# this door performs it only when asked, and says what it will do first.
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname "$0")" && pwd)
ROOT=$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd)
WORKSPACE=$(CDPATH='' cd -- "$ROOT/../.." && pwd)
CREDENTIALS=${LINUX618_CREDENTIALS:-"$WORKSPACE/.ssh-k3"}
STAGE=/tmp/linux-6.18-install
STATE=/var/lib/linux-6.18/devices.state

ACTION=status
ACTION_ARG=
REBOOT=no
WITH_DBG=no
while [ $# -gt 0 ]; do
    case "$1" in
        --status) ACTION=status; shift ;;
        --install) ACTION=install; ACTION_ARG=$2; shift 2 ;;
        --devices) ACTION=devices; ACTION_ARG=$2; shift 2 ;;
        --reboot) REBOOT=yes; shift ;;
        --with-dbg) WITH_DBG=yes; shift ;;
        *) echo "usage: $0 [--status] [--install DIR [--reboot] [--with-dbg]]" >&2
           echo "          [--devices off|on|status]" >&2
           exit 2 ;;
    esac
done

[ -r "$CREDENTIALS" ] || { echo "cannot read credentials: $CREDENTIALS" >&2; exit 1; }
mode=$(stat -c '%a' "$CREDENTIALS")
[ "$mode" = "600" ] || { echo "credentials must be mode 0600 (found $mode)" >&2; exit 1; }
command -v sshpass >/dev/null 2>&1 || { echo "sshpass is required" >&2; exit 1; }

host=; user=; secret=
while read -r host_field user_field secret_field _rest; do
    host=$host_field; user=$user_field; secret=$secret_field; break
done < "$CREDENTIALS"
[ -n "$host" ] && [ -n "$user" ] && [ -n "$secret" ] || {
    echo "credentials record must contain: host account secret" >&2; exit 1; }

SSHPASS=$secret
export SSHPASS
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$WORKSPACE/.local/known_hosts \
-o ConnectTimeout=20 -o NumberOfPasswordPrompts=1 -o PreferredAuthentications=password,publickey"
DEST="$user@$host"

# shellcheck disable=SC2086
remote() { sshpass -e ssh $SSH_OPTS "$DEST" "$@"; }

devices_status() {
    remote 'echo "kernel: $(uname -r)"; \
        echo "wifi: $(ip -br addr show wlP4p1s0 2>/dev/null || echo absent)"; \
        echo "wired: $(ip -br addr show end0 2>/dev/null || echo absent)"; \
        echo "rfkill: $(rfkill list 2>/dev/null | tr "\n" " " | head -c 120)"; \
        echo "rtw89 loaded: $(lsmod | grep -c "^rtw89" || true)"; \
        echo "state file: $(cat '"$STATE"' 2>/dev/null || echo none)"; \
        echo "stalls since boot: $(dmesg 2>/dev/null | grep -ci "rcu.*stall" || true)"; \
        echo "installed: $(dpkg -l "linux-image-6.18*" 2>/dev/null | awk "/^ii/{print \$2\" \"\$3}" | tr "\n" " ")"'
}

devices_off() {
    remote 'set -e; mkdir -p /var/lib/linux-6.18
        if [ ! -f '"$STATE"' ]; then
            {
                echo "wifi_link=$(cat /sys/class/net/wlP4p1s0/operstate 2>/dev/null || echo absent)"
                echo "second_nic=$(cat /sys/class/net/enP2p1s0/operstate 2>/dev/null || echo absent)"
                echo "rtw89_loaded=$(lsmod | grep -c "^rtw89" || true)"
            } > '"$STATE"'
        fi
        echo "-- disabling devices a benchmark does not need"
        rfkill block all 2>/dev/null || true
        ip link set wlP4p1s0 down 2>/dev/null || true
        ip link set enP2p1s0 down 2>/dev/null || true
        # Removing the WiFi modules takes their FIFO-50 IRQ threads off cpu0-7
        # entirely, which is the point of this verb for stress tests.
        for module in rtw89_8852be rtw89_pci rtw89_core; do
            modprobe -r "$module" 2>/dev/null && echo "unloaded $module" || true
        done
        echo "-- after"
        ip -br addr | grep -E "^(end0|wlP4p1s0|enP2p1s0)" || true
        echo "rtw89 loaded: $(lsmod | grep -c "^rtw89" || true)"
        echo "reachability check: the wired end0 address must still answer"'
}

devices_on() {
    remote 'set -e
        for module in rtw89_core rtw89_pci rtw89_8852be; do
            modprobe "$module" 2>/dev/null && echo "loaded $module" || true
        done
        rfkill unblock all 2>/dev/null || true
        ip link set wlP4p1s0 up 2>/dev/null || true
        ip link set enP2p1s0 up 2>/dev/null || true
        sleep 2
        ip -br addr | grep -E "^(end0|wlP4p1s0|enP2p1s0)" || true'
}

case "$ACTION" in
    status)
        devices_status
        ;;
    devices)
        case "$ACTION_ARG" in
            off) devices_off ;;
            on) devices_on ;;
            status) devices_status ;;
            *) echo "usage: $0 --devices off|on|status" >&2; exit 2 ;;
        esac
        ;;
    install)
        [ -d "$ACTION_ARG" ] || { echo "no such package directory: $ACTION_ARG" >&2; exit 1; }
        # The debug-symbol package is 683 MB and the kernel does not need it, so
        # it stays behind unless it is asked for explicitly.
        set -- "$ACTION_ARG"/linux-image-*.deb "$ACTION_ARG"/linux-headers-*.deb \
               "$ACTION_ARG"/linux-libc-dev*.deb
        packages=
        for candidate in "$@"; do
            [ -f "$candidate" ] || continue
            case "$candidate" in
                *-dbg_*) [ "$WITH_DBG" = yes ] || { echo "skipping $(basename "$candidate") (-dbg)"; continue; } ;;
            esac
            packages="$packages $candidate"
        done
        [ -n "$packages" ] || { echo "no installable .deb files in $ACTION_ARG" >&2; exit 1; }
        echo "-- packages to install"
        for candidate in $packages; do
            dpkg-deb -f "$candidate" Package Version 2>/dev/null || true
        done
        remote "rm -rf $STAGE && mkdir -p $STAGE"
        # shellcheck disable=SC2086
        # shellcheck disable=SC2086
        sshpass -e scp -q $SSH_OPTS $packages "$DEST:$STAGE/"
        remote "dpkg -i $STAGE/*.deb 2>&1 | tail -20"
        # The board boots by file name, not by "the newest kernel": u-boot reads
        # /boot/env_k3.txt, which names one vmlinuz, one initrd and one dtb
        # directory. Installing a kernel therefore has to re-point that file, or
        # the next reboot comes back on the old kernel.
        # The release is the newest one among the packages being installed, not
        # the first the shell happens to glob: a stale package left in the
        # directory once re-pointed the boot environment back to an older
        # kernel after the vendor's postinst had already done it correctly.
        release=
        for candidate in $packages; do
            case "$candidate" in
                */linux-image-*) ;;
                *) continue ;;
            esac
            candidate_release=$(basename "$candidate" | sed 's/^linux-image-//; s/_.*$//')
            if [ -z "$release" ] || dpkg --compare-versions "$candidate_release" gt "$release"; then
                release=$candidate_release
            fi
        done
        echo "-- installing release $release"
        [ -n "$release" ] || { echo "cannot derive the kernel release from the packages" >&2; exit 1; }
        echo "-- re-pointing the boot environment at $release"
        remote "set -e
            [ -f /boot/vmlinuz-$release ] || { echo 'missing /boot/vmlinuz-$release' >&2; exit 1; }
            if [ ! -f /boot/initrd.img-$release ]; then
                echo 'generating initrd for $release'
                update-initramfs -c -k $release >/dev/null 2>&1 || \\
                    mkinitramfs -o /boot/initrd.img-$release $release
            fi
            [ -d /boot/spacemit/$release ] || { echo 'missing dtb directory /boot/spacemit/$release' >&2; exit 1; }
            [ -f /boot/env_k3.txt.before-$release ] || cp /boot/env_k3.txt /boot/env_k3.txt.before-$release
            sed -i -e 's|^knl_name=.*|knl_name=vmlinuz-$release|' \\
                   -e 's|^ramdisk_name=.*|ramdisk_name=initrd.img-$release|' \\
                   -e 's|^dtb_dir=.*|dtb_dir=spacemit/$release|' /boot/env_k3.txt
            echo '-- /boot/env_k3.txt now:'
            grep -E '^(knl_name|ramdisk_name|dtb_dir)=' /boot/env_k3.txt
            ls -l /boot/vmlinuz-$release /boot/initrd.img-$release | sed 's/^/  /'"
        if [ "$REBOOT" = yes ]; then
            echo "-- rebooting into the new kernel"
            remote "sync; nohup sh -c 'sleep 2; reboot' >/dev/null 2>&1 &" || true
            sleep 30
            devices_status
        else
            echo "-- installed; reboot when ready (pass --reboot to do it from here)"
        fi
        ;;
esac
