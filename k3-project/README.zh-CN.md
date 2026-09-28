[English](README.md)

# SpacemiT 6.18 内核项目

## 本项目优化的是内核本身，而不是绕着内核调参

工作区的 AI 工作依赖内核，这些依赖没有任何用户态参数能够触达，而本项目就是承担这份依赖的地方：

* **智算簇是一类内核设备**：`/dev/tcm` 发放每个计算线程都需要的八个块，`/proc/set_ai_thread`（mode 0222，处理函数 `hmp_set_ai_thread`）是把线程绑到 cpu8-15 的唯一途径——内核用 `set_cpus_allowed_ptr` 完成，这正是用户态 `sched_setaffinity` 对这些核返回 `EINVAL` 的原因——而 `cpu_device_up` 按需给 AI 核上电，这也是 `nproc` 报 8、运行时横幅却报 16 的原因；
* **barrier 路径是一个缺失的设备**：运行时记录 `open(/dev/tcm_sync_mem) failed, errno=2` 并回退到堆 barrier，llama.cpp 战役把这项代价记为每 token 约 5 ms（约 253 个算子边界、67 ms token 的约 7 %）；
* **可观测性是一个构建选项**：本镜像是未开 `CONFIG_FUNCTION_TRACER` 构建的（实测 `available_tracers` = `blk wakeup_dl wakeup_rt wakeup nop`），所以 AI 路径的内核函数级跟踪在装上新内核之前不可能；`config/update4.config` 已带 `CONFIG_FUNCTION_TRACER=y`、`CONFIG_DYNAMIC_FTRACE=y` 与 `CONFIG_DEBUG_INFO_BTF=y`，而第一次更新从未在板上构建安装；
* **内存策略在内核层面就已生效**：运行时声明 `mem_backend: HPAGE` 并自行申请大页；THP `never` 的实测代价是 3.4 %，而运行时探测的 1 GiB hugetlb 路径（`/dev/hugetlb_1g`）在本镜像上不存在。

因此本项目的立场是：**内核是被测量、被打补丁、被优化的技术栈一等组成部分**——TCM 分配与 barrier 语义、HMP 线程注册与热插拔、DMA 映射属性、大页策略、抢占与 IRQ 引导——而不是一个停在配置开关、或者靠用户态绕过的地方。任何以「这是内核决定的」结尾的用户态发现，都以补丁或更新增量的形式落到这里。

本项目从厂商 BSP 构建 K3 内核并打包为 Debian 包，使内核侧改动能以"真正的更新包"
形式被验证，而不是手工拷贝镜像。先做配置，之后才是对源码树的声明式补丁。

## 固定源码

| 字段 | 值 |
|---|---|
| upstream | `https://github.com/spacemit-com/linux-6.18.git` |
| ref | `k3-br-v1.0.7` |
| commit | `4158237f35b8fd62ba198c1627e5a66e5a34c50f` |
| license | GPL-2.0 |
| checkout | `src/linux-6.18`（不纳入 Git） |

板卡运行的是 `6.18.3-generic #1.0.7.4`，属于该分支家族。更新的
`k3-br-v1.0.8` 存在但刻意不用，因此 update1 与在跑内核的差别只有其声明的配置。

## 版本策略

| 构建 | 内核 release | 包版本 |
|---|---|---|
| 定版更新 `N` | `6.18.3-generic-updateN` | `6.18.3-generic-updateN-1` |
| 迭代构建 | `6.18.3-generic-dev+<commit12>` | `6.18.3-generic-dev-<commit12>` |

只有定版才带编号。迭代构建标着 `dev` 与来源提交，因此机器上遗留的包一定能自证身份，
而报告里出现的编号只能来自一个被打算保留的构建。

## 配置

基线是板卡自己正在运行的配置 `config/base.config`，其摘要记录在
`sources.lock.yaml`，因此首个更新只改动声明的选项。每个更新在
`config/updateN.config` 里加一份增量，由内核自带的 `merge_config.sh` 合并、再由
`olddefconfig` 解析。

update1 的由来：两次高并发 `llama-server` 压测把 RCU 宽限期内核线程饿死
（`rcu_preempt kthread starved for 175000 jiffies`，`HZ=250` 下即 700 秒）并卡死板卡。
其增量启用 `CONFIG_RCU_EXPERT` + `CONFIG_RCU_BOOST` 与锁检测器。`RCU_EXPERT` 是必需
的：没有它 kconfig 会静默丢弃 `RCU_BOOST`，而"看起来生效、实际什么都没做"的增量比
没有增量更糟。

2026-09-18 已在板卡上验证：update1 已安装并运行，其启动日志报告
`rcu: RCU priority boosting: priority 1 delay 500 ms.`，且当初两次卡死板卡的多 agent
梯度在有记录的情况下无 stall 完成。

update2 加的是补丁而非配置。DDR 性能监控器唯一的读数会把每次
增量乘 16 后按 32 位截断，因此在 1 秒窗口里饱和于 4,096 MiB/s、半秒窗口里饱和于
8,188 MiB/s——而本板解码稳定在约 22 GiB/s，旧读数只能把它报成那个常数。补丁新增一个
原始逐端口读数，且不改动既有 ioctl，厂商自己的客户端照常工作。该补丁已对固定检出做过
编译验证。

2026-09-18 已在板卡上验证：update2 已安装并运行（`6.18.3-generic-update2`），启动日志
仍报告 RCU 优先级提升，且通过总集读取的一个解码窗口现在逐 tick 报出
21,075–22,356 MiB/s、**0 个钳位样本**、占天花板 93.1 %——而旧读数只能报那个常数。

update3 打开 `CONFIG_DEBUG_INFO_BTF`，于是板卡公开
`/sys/kernel/btf/vmlinux`，eBPF 采集器可以针对它将运行的内核构建，而不再依赖手工校验的结构
偏移。已验证：板卡 BTF 渲染出 168,399 行的 `vmlinux.h`，内存采集器 18 个 CO-RE 程序
全部挂载成功。

update4 打开函数跟踪，它存在的原因是缺了它某项测量根本做不了。厂商配置没有开启
`CONFIG_FUNCTION_TRACER`，因此 `DYNAMIC_FTRACE` 也未开启，`/sys/kernel/tracing`
下没有 `available_filter_functions`，BPF fentry 程序无法挂载——在原厂内核上实测：
`bpf_program__attach()` 报 `-EFAULT`。应用该增量后，板卡报告 60,700 个可过滤函数、
`function` tracer 可用、fentry 挂载成功、0 次 stall，以及 6,688,553 字节的
`/sys/kernel/btf/vmlinux`。它解锁了什么写在监控器的 `docs/kernel-enhancements.md`。

## 补丁

固定检出绝不原地修改：源码变更以 `patches/` 中的补丁存在，并在
`patches/manifest.yaml` 登记它所属的更新、原因、触及的文件与验证方式。
`scripts/build.sh` 在配置前按顺序应用它们，能识别"已应用"，并把实际应用的集合写进
封存的构建清单。

## 构建与打包

```sh
scripts/build.sh --fetch                    # 克隆固定分支，仅一次
scripts/build.sh --config-only --release 1  # 组装并解析配置
scripts/build.sh --release 1                # 配置、构建、打包、校验
scripts/build.sh                            # 迭代构建，不带更新编号
scripts/build.sh --info
```

产物落在 `.local/linux-6.18/debs/`，封存记录落在
`/root/k3-evidence/linux-6.18/<kernel release>/`，其中含构建清单、已解析配置、包摘要
与交叉编译器身份。

### 这条流水线产出什么、不产出什么

声明产物是 `linux-image-<release>` 与 `linux-headers-<release>`，另有随行
`linux-libc-dev`。厂商的 `debian/rules` 还会构建并打包 perf，而它从
`$(srctree)/tools/perf` 安装 perf，树外构建却把 perf 放在输出目录里，因此该附带包
在这里无法产出。清单记录 `bindeb_pkg_exit` 与 `ancillary_packages_not_built`，而不是
把一次部分完成的运行打扮成完整运行；只要声明产物缺失，门就直接拒绝继续。

## 在板卡上安装

```sh
scripts/run.sh --status
scripts/run.sh --install .local/linux-6.18/debs --reboot
```

安装内核属于远端变更，只有在被明确要求时该门才会执行；执行前先打印包身份，执行后
报告实际运行的内核。

板卡地址变化时无需修改凭据记录：在调用时导出 `K3_BOARD_HOST=<address>` 即可。

update3 已于 2026-09-18 部署到第二台 K3（16 GB，此前为原厂 `6.18.3-generic`）：
它安装的 17 个设备树与该机原厂集合逐字节一致，启动环境已指向
`6.18.3-generic-update3`，运行中的内核报告 `CONFIG_RCU_BOOST=y`、
`CONFIG_DEBUG_INFO_BTF=y`、RCU 优先级提升启动日志、0 次 stall、`/dev/ddr_perf`
以及 6,664,699 字节的 `/sys/kernel/btf/vmlinux`。记录位于
`/root/k3-evidence/linux-6.18/6.18.3-generic-update3/deployment-192.168.100.215`。

## 基准测试不需要的设备

```sh
scripts/run.sh --devices off      # rfkill、网口 down、卸载 WiFi 模块
scripts/run.sh --devices status
scripts/run.sh --devices on       # 恢复
```

本板上 WiFi 驱动是最忙的中断源，且其线程化 IRQ 以 `SCHED_FIFO` 50 跑在 cpu0-7——
正是负载主机侧所用的核，所以压力测试前把它摘掉属于"把测试做公平"的一部分。板卡有线
`end0` 不受影响。

## 工作区集成

本仓库位于 `projects/linux-6.18`，自带 Git 历史。自 2026-09-26 起它已登记到工作区
manifest 中，位于 `ai,notdefault` 组，因此默认 `repo sync` 不受影响，显式
`-g ai` 同步才会放置它。manifest 条目是：

```xml
<project name="linux-6.18.git" path="projects/linux-6.18" />
```

## 更新增量与各自打开的能力

每次更新在 `config/updateN.config` 里只加一条声明增量，增量本身说明上一个内核做不到什么：

| 增量 | 打开了什么 | 为什么需要 |
|---|---|---|
| update1-3 | （更早的内核工作：跟踪前置条件、RCU boost、lockup 检测器） | AI 分析之前的迭代内核工作 |
| **update4** | `FUNCTION_TRACER`、`DYNAMIC_FTRACE`、`DEBUG_INFO_BTF`（2026-09-23 安装） | 出厂镜像上做不了 AI 路径的内核函数级跟踪；update4 去掉了这个限制 |
| **update5**（本地构建） | `FUNCTION_PROFILER`、`FUNCTION_GRAPH_RETVAL` | *函数剖析器*（`trace_stat/function*` 里每函数的调用次数与总时间）是无需解析交错图块就能给内核时间排序的仪器，而图返回值能区分被跟踪函数的哪条分支跑了 |

看起来"不可用"、但并不是内核配置问题的条目：

* **1 GiB hugetlb**：基础配置里已有 `CONFIG_HUGETLBFS=y` 与 `CONFIG_HUGETLB_PAGE=y`，能力是存在的；缺的是预留池（`vm.nr_hugepages = 0`）与运行时探测的 `/dev/hugetlb_1g` 节点——后者不是标准内核设备，要么是启动期预留加厂商设备，要么是一个要问厂商的问题。两条路都记录在案，而不是假定。
* **十六核上的 graph-trace 解析**：这是用户态解析器的问题，不是内核问题——解法是按 CPU 匹配的解析器，或者每遍只跟一个函数，它属于本项目的工具。
* **barrier 设备**（`/dev/tcm_sync_mem`）：这是给我们自有 BSP 打的内核补丁，不是配置选项——设计是暴露一小块一致性区域供跨线程 barrier 使用，它是 update5 启动之后的下一个内核工作项。

## 可观测性清单与仍需开启的部分（2026-09-23 评估）

对照 `config/base.config` 与各增量核对，并附上每一项要回答的问题：

| 能力 | 状态 | 它回答什么问题 |
|---|---|---|
| `FUNCTION_TRACER`、`DYNAMIC_FTRACE`、`DEBUG_INFO_BTF` | **已具备**（update4，已安装） | AI 路径调用了哪些内核函数 |
| `KPROBE_EVENTS`、`FTRACE_SYSCALLS` | 已具备 | 逐调用计时、系统调用级视图 |
| `SCHED_TRACER`、`SCHEDSTATS` | 已具备 | 谁在跑、唤醒、runqueue 行为 |
| `BLK_DEV_IO_TRACE` | 已具备 | page cache 未命中时的存储路径 |
| `KALLSYMS_ALL`、`PERF_EVENTS`、`BPF_SYSCALL`、`BPF_JIT`、`TRACER_SNAPSHOT` | 已具备 | 符号解析、perf、eBPF、快照 |
| `FUNCTION_PROFILER`、`FUNCTION_GRAPH_RETVAL` | **update5 加入**（构建中） | 不解析图块也能得到每函数的内核时间排序 |
| `UPROBES`、`UPROBE_EVENTS` | **缺失 -> update6** | 20-30 ms 的会话创建成本，在*用户*空间被跟踪（llama.cpp 函数、厂商入口点）而无需修改它们 |
| `OSNOISE_TRACER`、`HWLAT_TRACER` | 缺失 -> update6 | 板卡是否安静、是否存在硬件停顿——监控器的问题，在内核分辨率上 |
| `IRQSOFF_TRACER`、`PREEMPT_TRACER`、`TIMERLAT_TRACER` | 缺失 -> update6 | 最坏情况下的关中断、关抢占与定时器延迟＝主机侧 decode 步可能遭受的抖动 |
| `STACK_TRACER` | 缺失 -> update6 | 把一条延迟发现变成调用路径 |
| 硬件计数器 | **基础配置已具备**（`PERF_EVENTS` + `RISCV_PMU_SBI`）；`HW_PERF_EVENTS` 不是 RISC-V 符号，带它的那行增量是惰性的 | cycles、instructions、branches、L1 与 dTLB 计数；2026-09-24 实测它们**也数得到智算簇**，不只 X100——见 `/root/k3-evidence/linux-6.18/perf-counters-20260924/` |
| 1 GiB hugetlb 预留与 `/dev/hugetlb_1g` | 配置已具备（`HUGETLBFS`、`HUGETLB_PAGE`），池与设备缺失 | 运行时声明的 `mem_backend: HPAGE` 能否被正确服务 |
| 取代堆回退的 barrier 设备 | 需要一个 BSP 补丁 | 约 253 个算子边界上约 5 ms/token 的 barrier 代价 |

于是随之而来两条增量：**update5**（剖析器 + 图返回值，已在构建）与 **update6**（uprobes、延迟/噪声 tracer、栈跟踪、硬件 perf 事件）。barrier 设备与 hugetlb 池不是选项，是工作项。

## update6 的第一份内核画像（2026-09-24）

在一次 `llama-bench` 运行期间打开 `function_profile_enabled`，用 `tools/kernel-profile.py` 聚合全部十六个 `trace_stat/functionN` 文件（主机侧、按 ONNX 项目工具规则的 Rule 2 离带处理），并去掉睡眠主导路径得到"工作视图"：

| 视图 | 结果 |
|---|---|
| 全部内核时间 | 62 个函数共 6,710 s——**含睡眠**，由 `schedule`（2,559 s）、`do_idle`（662 s）、超时、futex 与 epoll 等待主导 |
| **工作视图**（去掉睡眠路径） | **452.7 s = 全部内核时间的 6.7 %** |
| 工作视图的头部条目 | `__riscv_sys_read` / `ksys_read` / `vfs_read` **6,751 次调用、40.9 s、平均每次 6.1 ms**；`anon_pipe_read` 679 次 / 35.3 s；`rcu_gp_fqs_loop` 1374 次 / 24.5 s；`hrtimer_nanosleep`、`do_nanosleep`、`core_sys_select` |

两条读法。第一，内核不是 decode 运行花时间的地方——93 % 的内核时间是等待，这既符合预期，现在也被测出来了。第二，占主导的*工作*路径是读路径，**每次读平均 6.1 ms**，这不是 page cache 的速度：那是存储路径，也是本项目一直追的 20-30 ms 会话创建成本在内核侧的第一个候选。`rcu_gp_fqs_loop`（24.5 s）是第二个候选，它之所以存在是因为 update4 开了 RCU boost。

工具与证据：ONNX 项目里的 `tools/kernel-profile.py`，十六 CPU 的原始转储在 `k3-evidence/linux-6.18/update6-profile-20260923/profile-full.txt`。

### 冷热对比：读代价是存储，驻留才是杠杆（2026-09-24）

同一基准、只变一个变量——运行之间 `drop_caches`：**冷** 52.2 s 墙钟、**从存储读 3.15 GB**、`filemap_add_folio` 27,967 次 / 985 ms；**热** 47.6 s、512 个扇区、`filemap_get_entry`/`filemap_fault` 每次 1.7-2.6 us。因此先前看到的每次 `vfs_read` 6.1 ms 是冷缓存 I/O，不是常数；一次冷启动要多付 4.6 s 墙钟加约 1 s 内核 CPU。杠杆是首个请求之前权重的驻留（预读、readahead、mlock 或预热一遍）——一个带实测奖赏的基础软件改动，也是本项目在内核侧量化的第一个此类改动。

### 把驻留当优化来测：机制确认，奖赏如实测量（2026-09-24）

只变一个变量、同一基准两次受控运行（`-p 8 -n 128 -r 3`）：**A** 冷 page cache，**B** 运行前把模型预读进缓存（`dd if=model of=/dev/null`）。

| 运行 | 墙钟 | 基准期间的存储读 | 说明 |
|---|---|---|---|
| A 冷 | **60,089 ms** | **2 GB** | 基准自己付缓存填充 |
| B 预热 | **58,970 ms** | **0 GB** | 2 GB 的预热发生在运行之前 |

机制与预期完全一致——预热把存储流量移出测量窗口——而**这一受控对的墙钟奖赏是 1.1 s（1.8 %）**。更早、控制更弱的一对（更短的运行、n=8、开着剖析器）显示 4.6 s（9.8 %）；这一差别本身也被记录：运行越小，一次性冷启动成本的占比越大，所以诚实的总结是"驻留把 I/O 移出窗口，值墙钟的 1.8 % 到 9.8 %，取决于运行承载多少一次性成本"。

对于频繁启动的部署，这是"每次启动付一次缓存填充"还是"只付一次"的差别；对长基准它是个小常数。两次运行与扇区计数都在 `residency-runs.txt`。

### 用户态路径上的 uprobe：注册很便宜，时间在图工作里（2026-09-24）

在 update6 打开 `UPROBES` 之后，按偏移注册三个库函数的入口/出口探针（`nm -D` -> `file:0xaddr`），并跟踪一次 bench 运行（`uprobe-summary.txt`）：

| 探针 | 目标 | 测什么 |
|---|---|---|
| `cpuinit` | `libggml-cpu.so: ggml_backend_cpu_init` | 后端建立 |
| `sres` | `libllama.so: llama_context::sched_reserve()` | 上下文创建内部的调度器/图预留 |
| `gcomp` | `libllama.so: llama_context::graph_compute(ggml_cgraph*, bool)` | 每次图执行（prefill 与每个 decode 步） |

两条仪器教训记录在案：ftrace 要的是**偏移**形式（`file:0xaddr`），而不是被 mangle 的符号名；它的跟踪行把 pid 与 comm 连在一起（`llama-bench-690036`），粗糙的解析器会把它读成缺失字段。

测量本身从用户态确认了内核侧的画面：后端初始化与调度器预留是**亚毫秒到低毫秒**量级的操作，而毫秒级工作住在 `graph_compute` 里——与内核函数画像从另一方向得到的结论相同（93-96 % 的内核时间是等待；运行的时间在图工作内部）。

### 干扰与硬件停顿检查，以及 perf 缺口（2026-09-24）

**`osnoise` 可用**（update6 之后 tracer 存在）并逐 CPU 报告噪声；在一次 bench 期间的 6 s 窗口里，核 1/3/5/7 显示 37-64 % 的"噪声"。解释很重要：osnoise 线程的运行时间被*该核上其它一切*抢走，其中包含我们自己的工作负载的主机侧线程——所以这还不是一个 OS 噪声数字，而是"AI 侧在跑时 X100 侧很忙"的数字。正确的测法是两点式：**静止板基线**（无工作负载时的 osnoise）对同一窗口运行期，差值才是工作负载自身占的 CPU 份额。

**`hwlat` 没找到硬件停顿**：5 s 窗口没有产生任何硬件延迟条目，所以在这块板上、该分辨率下不存在类 SMI 干扰。

**`perf` 解阻，而且这件工具比缺口暗示的更值钱**（2026-09-24）。失败只是路径问题：`/usr/bin/perf` 是 Debian 的包装器，它会解析 `/usr/lib/linux-tools/$(uname -r)/perf`，而树外构建产不出这个文件——但*自带*的 `/usr/lib/linux-tools/6.18.3-generic/perf` 就在板上，版本 `6.18.3.g4158237f35b8`（与本项目固定源码同一提交），未经修改即可在 update6 上运行。实测可用的计数器：`cycles`、`instructions`、`branches`、`branch-misses`、`L1-dcache-loads/-misses`、`dTLB-loads`；不可用：`dTLB-load-misses`、`cache-references`、`cache-misses`。板上还有一个未被使用的 `rvtrace` PMU（type 10），带 per-CPU sink 组。

随后这件仪器不只是解阻了一个计划，而是纠正了一条长期前提：在两种掩码上都做空闲对照之后，**`perf` 在本内核上数得到智算簇的 cycles 与 instructions**——空闲时 cpu8-15 每 10 s 为 4.7e6 cycles，而 51.5 s 的 decode 窗口里是 290e9 cycles，八个 hart 每个解码 token 1.13e9 cycles，每指令 5.0 cycles，8.07e9 次 L1 miss 的 64 字节行与同一批 256 token 流过的 371 GiB 权重相合。因此 2026-09-17 那条"该簇对 Linux 侧仪器不可见"的记录**在这块板上没有复现**，而 update6 里的 `CONFIG_HW_PERF_EVENTS=y` 是*惰性*的：该符号对 RISC-V 不存在，计数器是靠基础配置的 `PERF_EVENTS` + `RISCV_PMU_SBI` 工作的。仍然成立的是契约里针对 A100 工作的证据规则（厂商侧参与、TCM 占用与吞吐算术）；改变的是现在它旁边多了一个计数器。完整记录、方法与原始日志：`/root/k3-evidence/linux-6.18/perf-counters-20260924/`。面向交付物的诚实打包修法仍是版本相关的 `linux-tools` 包，此处不作声称。
