# Memory Bench 单机数据集调度

准备进程在共享输出根目录里构建 bridge、写 recipe 并拼接轨迹。回放进程从已拼好的 episode 中领任务，导出 RGB 和机器人本体观测，每条独立生成一个 LeRobot v3 shard。两个进程池在 episode 边界调整分工，每个进程只服务一个阶段和一份任务配置。

## 盘点和启动

从仓库根目录运行，在装有本项目仿真与 LeRobot v3 依赖的 Python 环境中执行。本机已用 `behavior_memory` 环境验证打包与视频读回。配置文件的 `paths.output_root` 需要指向已准备并采集的 session。也可以直接传入输出目录中的 `sessions/<任务>/config.yaml`，此时以该目录所属的输出根为准。

```bash
cfg=memory_bench/configs/mb001_return_book_to_original_bookcase.yaml

# 只读盘点，无需 GPU
python -m memory_bench.scheduling "$cfg" --instances 1-20 --plan

# 使用所有可见卡，处理已分配的实例；至少 100 条成功才发布清单
python -m memory_bench.scheduling "$cfg" --min-per-task 100

# 指定物理卡、缩小实例范围，成功后直接打包到一个新目录
python -m memory_bench.scheduling "$cfg" --instances 1-20 --gpus 0 4 \
  --pack-to /data/bwang/datasets/MB001-train-1-20
```

多任务运行时把配置文件按优先级依次传入。当前任务的回放进行中，准备进程可以领取下一任务；回放按任务优先级领取，同一任务优先渲染帧数多的轨迹。使用 `--instances` 时，同一区间需要在每个任务的已分配实例范围内。完整录制缺失的实例单列为 `missing`。任一入队实例最终失败会阻止发布清单；`--min-per-task` 设置每个任务的成功条数下限。

## 并发和资源预算

默认每张回放卡运行 1 个进程，共享准备卡最多运行 4 个进程。调度器结合可见 GPU、空闲显存、可用内存、CPU 配额和磁盘余量决定实际并发数。只有一张卡或主机资源只容纳一个进程时，两阶段轮用；有多个执行位置时，为回放保留容量。运行中根据两阶段实测秒数/帧和就绪队列的预计渲染分钟数调整分工，约 30–60 分钟是初始缓冲目标。

| 参数 | 默认值 | 含义 |
| --- | ---: | --- |
| `--prep-per-gpu` | 4 | 单张准备卡的进程数上限 |
| `--worker-cpus` / `--worker-ram` | 4 / 12 | 每个进程预算的 CPU 数和主存 GiB |
| `--prep-vram` / `--replay-vram` | 6 / 8 | 两类进程各自预算的显存 GiB |
| `--disk-reserve` / `--job-disk` | 8 / 2 | 文件系统保留空间和每条在途任务的估算空间，单位 GiB |
| `--retries` / `--timeout-minutes` | 1 / 240 | 每阶段额外重试次数和单次执行时限 |

预算用于决定能否启动工作。场景、卡型、驱动和相机配置影响真实峰值，正式长跑前可用少量实例校准。只渲染 RGB，旧的深度 shard 会跳过。调度器对每个输出根的 `parallel/` 目录加独占锁。切换到手动命令或其他启动脚本前，先结束写入同一输出根的任务。

## 查看、恢复和打包

启动时会打印运行目录，位于第一份配置的输出根下：`scheduling/runs/<run>/`。该目录保存 SQLite 任务状态、尝试记录、worker 日志和成功后的 `manifest.json`。新生成的数据位于各任务自己的 `scheduling/shards/<任务>/<episode>/`。

```bash
python -m memory_bench.scheduling --status "$RUN_DIR"
```

中断后重新执行原启动命令。调度器重新盘点正式产物：已有 recipe 可继续 compose，已拼好的只需 replay，完整 shard 直接进入打包清单。现有 `lerobot_shards/<任务>/` 和 `parallel/<区间>/lerobot_shards/<任务>/` 均可复用。旧 shard 的所有 episode 都在本次选择内时才能整体复用；只选中其中一部分时，为所选 episode 各自生成独立 shard。重新运行会创建新的日志目录，历史尝试保留。

下面的打包命令也可以独立使用。`OUTPUT_ROOT` 为 session 输出根，`DATASET_DIR` 为新的发布目录。清单记录各任务原始 episode ID 对应的 shard 绝对路径；迁移数据后须使用新路径。pack 在目标盘构建完整数据集后发布，复用 MP4 并更新 parquet、标注、统计和全局索引。

```bash
python -m memory_bench.episodes.pack_dataset "$OUTPUT_ROOT" "$DATASET_DIR" \
  --manifest "$RUN_DIR/manifest.json"
```
