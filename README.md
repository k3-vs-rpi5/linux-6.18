[中文](README.zh-CN.md)

# SpacemiT 6.18 kernel project

## This project optimises the Linux kernel; it does not tune around it

The workspace's AI work depends on the kernel in ways that no user-space parameter can reach, and this
project is where that dependency is owned:

* **the AI cluster is a kernel device class**: `/dev/tcm` hands out the eight blocks that every compute
  thread needs, `/proc/set_ai_thread` (mode 0222, handler `hmp_set_ai_thread`) is the only way a thread
  is bound to cpu8-15 - the kernel does it with `set_cpus_allowed_ptr`, which is why user-space
  `sched_setaffinity` to those cores returns `EINVAL` - and `cpu_device_up` powers an AI core up on
  demand, which is why `nproc` reports 8 while the runtime's banner reports 16;
* **the barrier path is a missing device**: the runtime logs
  `open(/dev/tcm_sync_mem) failed, errno=2` and falls back to heap barriers, a cost the llama.cpp
  campaign accounts at ~5 ms per token (~7 % of a 67 ms token across ~253 operator boundaries);
* **observability is a build option**: this image was built without `CONFIG_FUNCTION_TRACER`
  (measured: `available_tracers` = `blk wakeup_dl wakeup_rt wakeup nop`), so kernel-function tracing of
  the AI path is impossible until an update is installed; `config/update4.config` already carries
  `CONFIG_FUNCTION_TRACER=y`, `CONFIG_DYNAMIC_FTRACE=y` and `CONFIG_DEBUG_INFO_BTF=y`, and the first
  update has never been built and installed on the board;
* **memory policy matters at kernel level**: the runtime declares `mem_backend: HPAGE` and asks for
  huge pages itself; THP `never` costs a measured 3.4 %, and the 1 GiB hugetlb path the runtime probes
  (`/dev/hugetlb_1g`) does not exist on this image.

So the standing position of this project is: **the kernel is a first-class part of the stack to be
measured, patched and optimised** - TCM allocation and barrier semantics, HMP thread registration and
hotplug, DMA mapping attributes, huge-page policy, preemption and IRQ steering - and not a place to
stop at configuration toggles or to route around in user space. Any user-space finding that ends in
"the kernel decides this" lands here as a patch or an update delta.

This project builds the K3's kernel from the vendor BSP and packages it as
Debian packages, so a kernel-side change can be tested as a real update instead
of a hand-copied image. Configuration comes first; patches to the tree come
later, as declared patches.

## Pinned source

| field | value |
|---|---|
| upstream | `https://github.com/spacemit-com/linux-6.18.git` |
| ref | `k3-br-v1.0.7` |
| commit | `4158237f35b8fd62ba198c1627e5a66e5a34c50f` |
| license | GPL-2.0 |
| checkout | `src/linux-6.18` (never tracked) |

The board runs `6.18.3-generic #1.0.7.4`, which is this branch family. The newer
`k3-br-v1.0.8` exists and is deliberately not used, so update1 differs from the
running kernel by its declared configuration and nothing else.

## Version policy

| build | kernel release | package version |
|---|---|---|
| frozen update `N` | `6.18.3-generic-updateN` | `6.18.3-generic-updateN-1` |
| iteration | `6.18.3-generic-dev+<commit12>` | `6.18.3-generic-dev-<commit12>` |

Only a frozen update carries a number. An iteration build says `dev` and the
commit it came from, so a package lying around a machine always identifies
itself, and a number that appears in a report can only come from a build that
was meant to be kept.

## Configuration

The base is the board's own running configuration, `config/base.config`, digest
recorded in `sources.lock.yaml`, so the first update changes the declared
options and nothing else. Each update adds one delta in `config/updateN.config`,
merged by the kernel's own `merge_config.sh` and resolved by `olddefconfig`.

Update 1 exists because two high-concurrency `llama-server` runs starved the RCU
grace-period kthread (`rcu_preempt kthread starved for 175000 jiffies`, 700 s at
`HZ=250`) and wedged the board. Its delta enables `CONFIG_RCU_EXPERT` +
`CONFIG_RCU_BOOST` and the lockup detectors. `RCU_EXPERT` is required: without
it kconfig silently drops `RCU_BOOST`, and a delta that looks applied while
doing nothing is worse than no delta.

Verified on the board on 2026-09-18: update1 is installed and running, its boot
log reports `rcu: RCU priority boosting: priority 1 delay 500 ms.`, and the
multi-agent ladder that wedged the board twice completes with no stall recorded.

Update 2 adds a patch, not a config. The DDR
performance monitor's only readout scales each delta by 16 and clamps it into 32
bits, so it saturates at 4,096 MiB/s in a one-second window and 8,188 MiB/s in
half a second - and a decode on this board sustains about 22 GiB/s, which the old
readout could only report as that constant. The patch adds a raw per-port
readout and leaves the existing ioctl untouched, so the vendor's own client
keeps working.

Verified on the board on 2026-09-18: update 2 is installed and running
(`6.18.3-generic-update2`), the boot log still reports the RCU boosting, and a
decode window read through the aggregate now reports 21,075 to 22,356 MiB/s per
tick with **zero clamped samples** and 93.1 % of the roof - where the old readout
could only report the constant.

Update 3 turns on `CONFIG_DEBUG_INFO_BTF`, so the board publishes
`/sys/kernel/btf/vmlinux` and an eBPF collector can be built against the kernel
it will run on instead of against hand-validated struct offsets. Verified: the
board's BTF renders a 168,399-line `vmlinux.h`, and the memory collector attaches
18 of 18 CO-RE programs.

Update 4 turns on function tracing, and it exists because a measurement could not
be made without it. The vendor configuration leaves `CONFIG_FUNCTION_TRACER`
off, which also leaves `DYNAMIC_FTRACE` off, so `/sys/kernel/tracing` has no
`available_filter_functions` and a BPF fentry program cannot be attached -
measured on the stock kernel: `bpf_program__attach()` fails with `-EFAULT`. With
the delta applied the board reports 60,700 filterable functions, a `function`
tracer, successful fentry attach, zero stalls and a 6,688,553-byte
`/sys/kernel/btf/vmlinux`. What that unlocked is in the monitor's
`docs/kernel-enhancements.md`.

## Patches

The pinned checkout is never edited in place: a source change is a patch in
`patches/`, listed in `patches/manifest.yaml` with the update it belongs to,
the reason, the files it touches and how it is verified. `scripts/build.sh`
applies them in order before configuring, recognises an already-applied patch,
and records the applied set in the sealed build manifest.

## Build and package

```sh
scripts/build.sh --fetch                    # clone the pinned branch, once
scripts/build.sh --config-only --release 1  # assemble and resolve the config
scripts/build.sh --release 1                # configure, build, package, verify
scripts/build.sh                            # iteration build, no update number
scripts/build.sh --info
```

Products land in `.local/linux-6.18/debs/` and the sealed record in
`/root/k3-evidence/linux-6.18/<kernel release>/`, which carries the build
manifest, the resolved config, the package digests and the cross compiler
identity.

### What this pipeline produces, and what it does not

The declared artefacts are `linux-image-<release>` and
`linux-headers-<release>`; `linux-libc-dev` comes with them. The vendor's
`debian/rules` additionally builds and packages perf, and it installs perf from
`$(srctree)/tools/perf` while an out-of-tree build puts it in the object tree,
so that ancillary package cannot be produced here. The manifest records
`bindeb_pkg_exit` and `ancillary_packages_not_built` instead of presenting a
partial run as a complete one, and the door refuses to continue when a declared
artefact is missing.

## Installing on the board

```sh
scripts/run.sh --status
scripts/run.sh --install .local/linux-6.18/debs --reboot
```

Installing a kernel is a remote change and the door performs it only when
asked; it prints the package identity first and reports the running kernel
afterwards.

A board whose address changed is reached without editing the credential record:
export `K3_BOARD_HOST=<address>` for the invocation.

Update 3 was deployed to a second K3 (16 GB, previously stock
`6.18.3-generic`) on 2026-09-18: all 17 device trees it installs are
byte-identical to that board's stock set, the boot environment was re-pointed to
`6.18.3-generic-update3`, and the running kernel reports `CONFIG_RCU_BOOST=y`,
`CONFIG_DEBUG_INFO_BTF=y`, the RCU priority-boosting boot line, zero stalls,
`/dev/ddr_perf`, and a 6,664,699-byte `/sys/kernel/btf/vmlinux`. Record:
`/root/k3-evidence/linux-6.18/6.18.3-generic-update3/deployment-192.168.100.215`.

## Devices a benchmark does not need

```sh
scripts/run.sh --devices off      # rfkill, links down, WiFi modules removed
scripts/run.sh --devices status
scripts/run.sh --devices on       # restore
```

The WiFi driver is the busiest interrupt source on this board and its threaded
IRQ runs at `SCHED_FIFO` 50 on cpu0-7, the same cores the workload's host side
uses, so taking it off is part of making a stress test fair. The board's wired
`end0` is untouched.

## Workspace integration

This repository lives at `projects/linux-6.18` and carries its own Git history.
It is not listed in the workspace manifest yet, because the manifest resolves
`linux-6.18.git` from the shared remote and the first push still needs human
approval. Once the remote repository exists, add this entry to `default.xml`:

```xml
<project name="linux-6.18.git" path="projects/linux-6.18" />
```

## Update deltas and what each one makes possible

Each update adds exactly one declared delta in `config/updateN.config`, and the delta states what the
previous kernel could not do:

| delta | what it enables | why it was needed |
|---|---|---|
| update1-3 | (earlier kernel work: tracing prerequisites, RCU boost, lockup detectors) | iterative kernel work before the AI analysis |
| **update4** | `FUNCTION_TRACER`, `DYNAMIC_FTRACE`, `DEBUG_INFO_BTF` (installed 2026-09-23) | kernel-function tracing of the AI path was impossible on the image that shipped; update4 removed that limit |
| **update5** (built here) | `FUNCTION_PROFILER`, `FUNCTION_GRAPH_RETVAL` | the *function profiler* (per-function call counts and total time in `trace_stat/function*`) is the instrument that ranks kernel time without parsing interleaved graph blocks, and the graph return value distinguishes which branch of a traced function ran |

Items that look "unavailable" but are not kernel-config problems:

* **1 GiB hugetlb**: `CONFIG_HUGETLBFS=y` and `CONFIG_HUGETLB_PAGE=y` are already in the base config, so
  the capability exists; what is missing is a reserved pool (`vm.nr_hugepages = 0`) and the
  `/dev/hugetlb_1g` node the runtime probes, which is not a standard kernel device - it is either a
  boot-time reservation plus a vendor device, or a vendor question. Both paths are recorded rather than
  assumed.
* **graph-trace parsing across sixteen CPUs**: a user-space parser problem, not a kernel one - the fix
  is a per-CPU matched parser or one function per pass, and it belongs in this project's tools.
* **barrier device** (`/dev/tcm_sync_mem`): a kernel patch to the BSP we own, not a configuration
  option - the design is a small coherent region exposed for cross-thread barriers, and it is the next
  kernel work item after update5 boots.
