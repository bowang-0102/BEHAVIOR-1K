# Memory Bench

实现范围为 Level 1 杯柜 demo，以及 Level 2 的位置更新和杯子/盘子信息综合任务。共同使用 session 配置、有序 Key 列表、联合遥操、物理 Bridge、受控状态拼接和全场景 LeRobot 导出。Level 3 和策略评测不在本次范围内。任务模块保留现有 `tasks/` 布局，以 Query 活动名注册。

## 环境与配置

从仓库根目录运行，使用预装的 `behavior` 环境，不安装依赖。正式配置各采样 150 个实例；小规模联调请复制配置，修改 `instance_sampling.count` 并使用新的 `paths.output_root`，不能覆盖正式 session。

| 配置 | 记忆需求 |
| --- | --- |
| `configs/mb_retrieve_mug_from_top_cabinet.yaml` | 一个 Key，三个候选吊柜 |
| `configs/mb_retrieve_target_mug_after_relocation.yaml` | Key 1 选择目标身份，Key 2 独立更新两只杯子的位置 |
| `configs/mb_retrieve_mug_and_plate.yaml` | 分别记住杯子和盘子的位置 |

本机资产根目录是 `/data2/bwang/code/BEHAVIOR-1K/datasets`，原始录制根目录是 `/data2/bwang/datasets/2026-challenge-rawdata`。检查配置中的三个绝对路径；环境变量 `OMNIGIBSON_DATA_PATH`、`MB_ROOT` 如已设置，必须与配置相同。

```bash
conda activate behavior
export OMNIGIBSON_HEADLESS=1
export OMNIGIBSON_DATA_PATH=/data2/bwang/code/BEHAVIOR-1K/datasets
export OMNIGIBSON_TASK_INSTANCES_DATASET=memory-bench-task-instances
export MB_ROOT=/data2/bwang/datasets/memory-bench
export PYTHONBREAKPOINT=0
CONFIG=memory_bench/configs/mb_retrieve_mug_from_top_cabinet.yaml
SESSION=mb_retrieve_mug_from_top_cabinet
```

## 准备与采集

1. 只读检查 I 素材，冻结 session 注册，并为全部 Key/Query 生成模板。现有采样器输出到独立的 `memory-bench-task-instances`，不修改官方任务实例。

```bash
python -m memory_bench.processing.sources "$CONFIG"
python -m memory_bench.collection.prepare_task "$CONFIG" init
# 对配置中的每个 Key 和 Query 分别执行：
python OmniGibson/scripts/sampling/sample_b1k_tasks.py --activity mb_mug_into_top_cabinet
python OmniGibson/scripts/sampling/sample_b1k_tasks.py --activity mb_retrieve_mug_from_top_cabinet
# 只有第一个 Key 需要乘采样到实例总数；正式配置为 150。
python OmniGibson/scripts/sampling/multiply_b1k_tasks.py --activity mb_mug_into_top_cabinet \
  --start_idx 1 --end_idx 150 --partial_save --headless
# 为每个 Key/Query 模板采样机器人入口，以下展示 demo 的两个活动。
# 原有 pose 工具要求 instances 目录存在；Query 只需要模板入口，不生成额外 TRO。
mkdir -p "$OMNIGIBSON_DATA_PATH/memory-bench-task-instances/scenes/house_single_floor/json/house_single_floor_task_mb_retrieve_mug_from_top_cabinet_instances"
python OmniGibson/scripts/sampling/sample_robot_pose.py --activity mb_mug_into_top_cabinet
python OmniGibson/scripts/sampling/sample_robot_pose.py --activity mb_retrieve_mug_from_top_cabinet
python -m memory_bench.collection.prepare_task "$CONFIG" freeze-cases
python -m memory_bench.collection.sample_instances "$CONFIG"
python -m memory_bench.collection.prepare_task "$CONFIG" finalize \
  --pose-manifest "$MB_ROOT/sessions/$SESSION/sampled/poses.json" \
  --scene-root "$MB_ROOT/sessions/$SESSION/sampled/scene_snapshots" \
  --inventory "$MB_ROOT/reports/$SESSION/source_inventory.json"
```

采样阶段检查第一个 Key 的真实 BDDL 初态、空手、稳定性和路线可达性。完整场景、各活动绑定、独立于答案采样的多样 Q 起点、TRO/场景哈希写入实例清单。配置中的速度和状态阈值目前标为待物理标定，应先在代表实例上完成导航、柜门和机器人边界检查，再冻结正式数据条件。`finalize` 可通过 `--representative-calibration`、`--physical-calibration` 引用真实检查报告；未提供时实例保留待标定状态。

2. 启动 JoyLo 硬件客户端与 session 服务端。服务端完整加载实例场景，同一次 HDF5 中采集全部 Key 与 Q。按 `N` 请求开始或结束当前片段；边界检查通过后才推进。完成 Key 后自动收臂、导航、展开，等待控制器与实际关节同步后继续遥操。Q 打开错误柜门会拒绝该录制。

```bash
bash memory_bench/scripts/run_joylo.sh
# 另一个终端，每个实例一次：
bash memory_bench/scripts/collect_session.sh "$CONFIG" 1
```

入口/出口快照和 `marks.yaml` 保存在 HDF5 同名目录。采集转场仅用于联合录制，最终序列重新合成 Bridge。不要通过 reset 或重新加载 Query 场景切换任务。

## 切段、匹配、拼接与导出

对每条联合录制执行规范化和切段。状态约定为动作区间 `[start,end)`，出口为最后一个动作后的 `state[end]`；缺少终态时必须提供采集器的 Query 出口快照。

```bash
RECORDING=recordings/$SESSION/instance_0001_recording_0001
NORMALIZED=segments/$SESSION/instance_0001/joint_normalized.hdf5
python -m memory_bench.processing.normalize_recording "$CONFIG" \
  "$RECORDING.hdf5" "$NORMALIZED" --terminal "$RECORDING/query_exit.json"
python -m memory_bench.processing.make_segments "$CONFIG" \
  --marks "$RECORDING/marks.yaml" --normalized "$NORMALIZED" \
  --instance "instances/$SESSION/instance_0001/instance.json"
```

所有联合录制切段完成后，选择每个 I 的有效源录制，不放回地一一匹配，独立分配序列顺序，生成物理 Bridge 并导出：

```bash
python -m memory_bench.processing.prepare_intervents "$CONFIG" \
  "reports/$SESSION/source_inventory.json"
python -m memory_bench.processing.pair_sources "$CONFIG" --order-seed 1
python -m memory_bench.processing.compose_episodes "$CONFIG" \
  "sessions/$SESSION/material_groups.json"
python -m memory_bench.processing.render_episodes "$CONFIG"
```

拼接只继承片段声明的写集合和机器人状态，保留其他任务的结果；I/B 全程不得改变保护物体。Bridge 在当前统一场景中执行动作并检查非机器人状态、接触和实际底盘终点，不传送机器人。完整序列重新渲染；Bridge 指令沿用下一个任务指令。答案、变体及绑定只进入内部注释，不进入逐帧指令。导出后读回动作、指令、时间戳、帧数、episode 数及视频。

已有产物拒绝覆盖；失败报告保存在 `reports/<session_id>/`。失败后检查原因，使用新的输出根目录重建，保留原始录制不变。

## 验收

```bash
OMNIGIBSON_HEADLESS=1 conda run --no-capture-output -n behavior \
  python -m pytest memory_bench/tests -q
```

离线测试覆盖 Key 在序列中间的状态继承、多 Key 状态依赖、缺失终态、非法物体变更、机器人实际底盘边界、旧录制元数据迁移，以及 Level 2 两个 Key 的独立信息。旧录制只在内存中适配当前 Robot/控制器状态结构，原始 HDF5 保持不变。它们不能替代 GPU 场景、物理导航、遥操或 LeRobot 视频的完整验收。

正式交付数量为 150 个有效实例、150 条联合录制、每个 I 150 条不同录制和总计 150 条完整 episode。现有素材是否足量、实际物理检查及导出结果应以运行报告为准；少量 smoke 数据不代表正式数据完成。

本次实现的实测范围与未完成的数据验收见 [validation.md](docs/validation.md)。
