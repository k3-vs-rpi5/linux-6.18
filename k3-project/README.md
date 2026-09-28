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
It is listed in the workspace manifest as of 2026-09-26, in the `ai,notdefault`
group, so a default `repo sync` is unaffected and an explicit `-g ai` sync
places it. The manifest entry is:

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

## Observability inventory and what still has to be enabled (assessed 2026-09-23)

Checked against `config/base.config` plus the update deltas, with the question each item serves:

| capability | state | question it answers |
|---|---|---|
| `FUNCTION_TRACER`, `DYNAMIC_FTRACE`, `DEBUG_INFO_BTF` | **present** (update4, installed) | which kernel functions the AI path calls |
| `KPROBE_EVENTS`, `FTRACE_SYSCALLS` | present | per-call timing, syscall-level view |
| `SCHED_TRACER`, `SCHEDSTATS` | present | who runs, wakeups, runqueue behaviour |
| `BLK_DEV_IO_TRACE` | present | storage path when the page cache misses |
| `KALLSYMS_ALL`, `PERF_EVENTS`, `BPF_SYSCALL`, `BPF_JIT`, `TRACER_SNAPSHOT` | present | symbol resolution, perf, eBPF, snapshots |
| `FUNCTION_PROFILER`, `FUNCTION_GRAPH_RETVAL` | **added in update5** (building) | kernel time ranking per function without parsing graph blocks |
| `UPROBES`, `UPROBE_EVENTS` | **missing -> update6** | the 20-30 ms session-creation cost, traced in *user* space (llama.cpp functions, vendor entry points) without modifying them |
| `OSNOISE_TRACER`, `HWLAT_TRACER` | missing -> update6 | is the board quiet, and are there hardware stalls - the monitor's question, at kernel resolution |
| `IRQSOFF_TRACER`, `PREEMPT_TRACER`, `TIMERLAT_TRACER` | missing -> update6 | worst-case interrupt-off, preemption-off and timer latency = the jitter a host-side decode step can suffer |
| `STACK_TRACER` | missing -> update6 | turns a latency finding into a call path |
| hardware counters | **present in the base config** (`PERF_EVENTS` + `RISCV_PMU_SBI`); `HW_PERF_EVENTS` is not a RISC-V symbol and the delta line carrying it was inert | cycles, instructions, branches, L1 and dTLB counts; measured 2026-09-24 to count the **AI cluster too**, not only X100 - see `/root/k3-evidence/linux-6.18/perf-counters-20260924/` |
| 1 GiB hugetlb reservation and `/dev/hugetlb_1g` | config present (`HUGETLBFS`, `HUGETLB_PAGE`), pool and device absent | whether the runtime's `mem_backend: HPAGE` can be served properly |
| a barrier device replacing the heap fallback | needs a BSP patch | ~5 ms/token of barrier cost across ~253 operator boundaries |

Two deltas therefore follow: **update5** (profiler + graph return values, already building) and
**update6** (uprobes, latency/noise tracers, stack tracer, hardware perf events). The barrier device and
the hugetlb pool are not options but work items.


## First kernel profile with update6 (2026-09-24)

`function_profile_enabled` during one `llama-bench` run, all sixteen `trace_stat/functionN` files
aggregated by `tools/kernel-profile.py` (host-side, off-band per Rule 2 of the ONNX project's tooling
rules), with sleep-dominated paths removed for a "working" view:

| view | result |
|---|---|
| all kernel time | 6,710 s over 62 functions - **sleep-inclusive**, dominated by `schedule` (2,559 s), `do_idle` (662 s), timeouts, futex and epoll waits |
| **working view** (sleep paths removed) | **452.7 s = 6.7 %** of all kernel time |
| top working entries | `__riscv_sys_read` / `ksys_read` / `vfs_read` **6,751 calls, 40.9 s, mean 6.1 ms per call**; `anon_pipe_read` 679 calls / 35.3 s; `rcu_gp_fqs_loop` 1374 / 24.5 s; `hrtimer_nanosleep`, `do_nanosleep`, `core_sys_select` |

Two readings. First, the kernel is not where a decode run spends its time - 93 % of kernel time is
waiting, which is expected and now measured. Second, the dominant *working* path is the read path with a
**6.1 ms mean per read**, which is not page-cache speed: that is the storage path, and it is the first
kernel-side candidate for the 20-30 ms session-creation cost this project has been chasing. `rcu_gp_fqs_loop`
(24.5 s) is the second candidate, and it exists because update4 enabled RCU boost.

Tool and evidence: `tools/kernel-profile.py` in the ONNX project, raw 16-CPU dump in
`k3-evidence/linux-6.18/update6-profile-20260923/profile-full.txt`.


### Cold versus warm: the read cost is storage, and residency is the lever (2026-09-24)

Same benchmark, one variable - `drop_caches` between runs: **cold** 52.2 s wall with **3.15 GB read from
storage** and `filemap_add_folio` 27,967 calls / 985 ms; **warm** 47.6 s with 512 sectors and
`filemap_get_entry`/`filemap_fault` at 1.7-2.6 us per call. The 6.1 ms per `vfs_read` seen earlier is
therefore cold-cache I/O, not a constant, and a cold start costs 4.6 s of wall time plus ~1 s of kernel
CPU. The lever is residency of the weights before the first request (preload, readahead, mlock or a
warm-up pass) - a base-software change with a measured prize, and the first such change this project
has quantified on the kernel side.


### Residency tested as an optimisation: mechanism confirmed, prize measured honestly (2026-09-24)

One variable, two controlled runs of the same benchmark (`-p 8 -n 128 -r 3`): **A** with a cold page
cache, **B** with the model pre-read into the cache (`dd if=model of=/dev/null`) before the run.

| run | wall | storage read during the benchmark | note |
|---|---|---|---|
| A cold | **60,089 ms** | **2 GB** | the benchmark pays the cache fill |
| B preloaded | **58,970 ms** | **0 GB** | the preload (2 GB) happened before the run |

So the mechanism is exactly as expected - preloading removes the storage traffic from the measured
window - and the **wall-time prize in this controlled pair is 1.1 s (1.8 %)**. An earlier, less
controlled pair (shorter runs, n=8, profiler enabled) showed 4.6 s (9.8 %); the difference between the
two pairs is itself recorded: the smaller the run, the larger the share of one-off cold-start cost, so
the honest summary is "residency removes the I/O from the window, worth somewhere between 1.8 % and
9.8 % of wall time depending on how much one-off cost the run carries".

For a deployment that starts frequently this is the difference between paying the cache fill per start
and paying it once; for a long benchmark it is a small constant. Both runs and the sector counts are in
`residency-runs.txt`.


### uprobe on the user-space path: registration is cheap, the graph work is where time goes (2026-09-24)

With update6's `UPROBES` enabled, entry/exit probes were registered by offset (`nm -D` -> `file:0xaddr`)
on three library functions and one bench run was traced (`uprobe-summary.txt`):

| probe | target | what it measures |
|---|---|---|
| `cpuinit` | `libggml-cpu.so: ggml_backend_cpu_init` | backend setup |
| `sres` | `libllama.so: llama_context::sched_reserve()` | the scheduler/graph reservation inside context creation |
| `gcomp` | `libllama.so: llama_context::graph_compute(ggml_cgraph*, bool)` | every graph execution (prefill and each decode step) |

Two instrument lessons recorded: ftrace wants the **offset** form (`file:0xaddr`) rather than a mangled
symbol name, and its trace lines join pid and comm (`llama-bench-690036`), which a naive parser reads as
a missing field.

The measurement itself confirms the kernel-side picture from the user side: the backend init and the
scheduler reservation are **sub-millisecond to low-millisecond** operations, while `graph_compute` is
where the millisecond-scale work lives - the same conclusion the kernel function profile reached from
the other direction (93-96 % of kernel time is waiting; the run's time is inside the graph work).


### Interference and hardware-stall checks, and the perf gap (2026-09-24)

**`osnoise` works** (tracer present after update6) and reports per-CPU noise; in a 6 s window during a
bench, cores 1/3/5/7 show 37-64 % "noise". Interpretation matters: the osnoise thread loses its runtime
to *everything else on that core*, which includes our own workload's host-side threads - so this is not
yet an OS-noise number but a "the X100 side is busy while the AI side runs" number. The correct
measurement is a two-point one: a **quiet-board baseline** (osnoise with no workload) against the same
window during a run, and the difference is the workload's own CPU share.

**`hwlat` finds no hardware stalls**: a 5 s window produced no hardware-latency entries, so there is no
SMI-like interference on this board at that resolution.

**`perf` is unblocked, and the tool is worth more than the gap suggested** (2026-09-24). The failure
was only a path: `/usr/bin/perf` is Debian's wrapper and resolves
`/usr/lib/linux-tools/$(uname -r)/perf`, which the out-of-tree package build cannot produce - but the
*stock* `/usr/lib/linux-tools/6.18.3-generic/perf` is on the board, is `6.18.3.g4158237f35b8` (the same
commit as this project's pinned source), and runs against update6 unmodified. Counters measured
available: `cycles`, `instructions`, `branches`, `branch-misses`, `L1-dcache-loads/-misses`,
`dTLB-loads`; not available: `dTLB-load-misses`, `cache-references`, `cache-misses`. There is also an
unused `rvtrace` PMU (type 10) with a per-CPU sink group.

The instrument then corrected a standing premise rather than merely unblocking a plan: with idle
controls on both masks, **`perf` counts the AI cluster's cycles and instructions on this kernel** -
4.7e6 cycles per 10 s on cpu8-15 while idle against 290e9 cycles in a 51.5 s decode window, 1.13e9
cycles per decoded token across eight harts, 5.0 cycles per instruction, and 8.07e9 L1 misses whose
64-byte lines match the 371 GiB of weights the same 256 tokens stream. The 2026-09-17 record that the
cluster is invisible to Linux-side instruments is therefore **not reproduced on this board**, and the
`CONFIG_HW_PERF_EVENTS=y` line in update6 is *inert*: the symbol does not exist for RISC-V, and the
counters work through the base config's `PERF_EVENTS` + `RISCV_PMU_SBI`. What remains true is the
contract's evidence rule for A100 work (vendor-side engagement, TCM occupancy and the throughput
arithmetic); what changes is that a counter now exists alongside it. Full record, methods and raw logs:
`/root/k3-evidence/linux-6.18/perf-counters-20260924/`. A version-specific `linux-tools` package
remains the honest packaging fix for a delivered artefact and is not claimed here.
