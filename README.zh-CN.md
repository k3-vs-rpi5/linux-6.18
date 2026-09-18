[English](README.md)

# SpacemiT 6.18 内核项目

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

update2 已备好但尚未构建：它加的是补丁而非配置。DDR 性能监控器唯一的读数会把每次
增量乘 16 后按 32 位截断，因此在 1 秒窗口里饱和于 4,096 MiB/s、半秒窗口里饱和于
8,188 MiB/s——而本板解码稳定在约 22 GiB/s，旧读数只能把它报成那个常数。补丁新增一个
原始逐端口读数，且不改动既有 ioctl，厂商自己的客户端照常工作。该补丁已对固定检出做过
编译验证；构建、打包与安装是下一步，且与任何内核变更一样需要操作者批准。

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

本仓库位于 `projects/linux-6.18`，自带 Git 历史。它目前还没有登记到工作区 manifest
中，因为 manifest 会从共享远端解析 `linux-6.18.git`，而首次 push 仍需人工批准。远端
仓库就绪后，把下面这行加入 `default.xml`：

```xml
<project name="linux-6.18.git" path="projects/linux-6.18" />
```
