# Memory Bench

实现范围为 Level 1 杯柜 demo，以及 Level 2 的位置更新和杯子/盘子信息综合任务。共同使用 session 配置、有序 Key 列表、联合遥操、物理 Bridge、受控状态拼接和全场景 LeRobot 导出。Level 3 和策略评测不在本次范围内。任务模块保留现有 `tasks/` 布局，以 Query 活动名注册。

## 环境与配置

脚本自动进入仓库、使用预装的 `behavior` 环境并读取配置中的路径，无需手动激活环境或设置环境变量。默认使用 Level 1 杯柜 demo。正式配置各采样 150 个实例；小规模联调请复制配置，修改 `instance_sampling.count` 并使用新的 `paths.output_root`，不能覆盖正式 session。

| 配置 | 记忆需求 |
| --- | --- |
| `configs/mb_retrieve_mug_from_top_cabinet.yaml` | 一个 Key，三个候选吊柜 |
| `configs/mb_retrieve_target_mug_after_relocation.yaml` | Key 1 选择目标身份，Key 2 独立更新两只杯子的位置 |
| `configs/mb_retrieve_mug_and_plate.yaml` | 分别记住杯子和盘子的位置 |

本机资产根目录是 `/data2/bwang/code/BEHAVIOR-1K/datasets`，原始录制根目录是 `/data2/bwang/datasets/2026-challenge-rawdata`。检查配置中的三个绝对路径；环境变量 `OMNIGIBSON_DATA_PATH`、`MB_ROOT` 如已设置，必须与配置相同。

除 `collect.sh` 外，流程脚本的第一个可选参数都是配置路径；例如使用 Level 2：

```bash
bash memory_bench/scripts/prepare_session.sh memory_bench/configs/mb_retrieve_mug_and_plate.yaml
```

## 准备与采集

1. 一键生成全部 Key/Query 模板、第一 Key 的 TRO、完整场景和入口姿态，检查 I 素材并写入实例清单。现有采样器输出到独立的 `memory-bench-task-instances`，不修改官方任务实例。

```bash
bash memory_bench/scripts/prepare_session.sh
```

采样阶段检查第一个 Key 的真实 BDDL 初态、空手、稳定性和路线可达性。完整场景、各活动绑定、独立于答案采样的多样 Q 起点、TRO/场景哈希写入实例清单。一键准备连续执行采样和 finalize，未做物理标定的实例保留待标定状态。正式数据仍需在代表实例上完成导航、柜门和机器人边界检查；需要分开执行时，使用 `prepare_session.sh CONFIG sample` 和 `prepare_session.sh CONFIG finalize [POSE_MANIFEST]`。标定报告参数仍由 `collection.prepare_task` 的 CLI 提供。

2. 启动 JoyLo 硬件客户端与 session 服务端。服务端完整加载实例场景，同一次 HDF5 中采集全部 Key 与 Q。按 `N` 请求开始或结束当前片段；边界检查通过后才推进。完成 Key 后自动收臂、导航、展开，等待控制器与实际关节同步后继续遥操。Q 打开错误柜门会拒绝该录制。

```bash
bash memory_bench/scripts/run_joylo.sh
# 另一个终端，每个实例一次：
bash memory_bench/scripts/collect.sh 1
```

逐实例修改数字即可；`collect.sh` 的第二个参数可指定其他 session 配置，第三个参数可指定录制路径。硬件客户端默认使用 `/dev/ttyUSB1`，可通过 `GELLO_PORT` 修改。入口/出口快照和 `marks.yaml` 保存在 HDF5 同名目录。采集转场仅用于联合录制，最终序列重新合成 Bridge。不要通过 reset 或重新加载 Query 场景切换任务。

## 切段、匹配、拼接与导出

一键规范化和切段全部实例的默认联合录制（`instance_NNNN_recording_0001.hdf5`）。脚本从 `marks.yaml` 读取 Query 出口快照补充终态，拒绝无效录制；录制缺失或处理失败时停止，不跳过实例。

```bash
bash memory_bench/scripts/split_recordings.sh
```

只处理一个实例时，传入配置路径和实例数字：`split_recordings.sh CONFIG 1`。自定义录制文件名仍使用 `processing.normalize_recording`、`processing.make_segments` 的 CLI。状态约定为动作区间 `[start,end)`，出口为最后一个动作后的 `state[end]`。

所有联合录制切段完成后，一键选择 I 素材、匹配序列、合成物理 Bridge、渲染导出并读回：

```bash
bash memory_bench/scripts/export_session.sh
```

默认顺序种子为 1，可通过 `export_session.sh CONFIG ORDER_SEED` 修改，需与实例种子不同。拼接只继承片段声明的写集合和机器人状态，保留其他任务的结果；I/B 全程不得改变保护物体。Bridge 在当前统一场景中执行动作并检查非机器人状态、接触和实际底盘终点，不传送机器人。完整序列重新渲染；Bridge 指令沿用下一个任务指令。答案、变体及绑定只进入内部注释，不进入逐帧指令。导出后读回动作、指令、时间戳、帧数、episode 数及视频。

已有产物拒绝覆盖；失败报告保存在 `reports/<session_id>/`。失败后检查原因，使用新的输出根目录重建，保留原始录制不变。

## 验收

```bash
bash memory_bench/scripts/check.sh
```

检查脚本运行 Ruff 代码/格式检查和离线测试。离线测试覆盖 Key 在序列中间的状态继承、多 Key 状态依赖、缺失终态、非法物体变更、机器人实际底盘边界、旧录制元数据迁移，以及 Level 2 两个 Key 的独立信息。旧录制只在内存中适配当前 Robot/控制器状态结构，原始 HDF5 保持不变。它们不能替代 GPU 场景、物理导航、遥操或 LeRobot 视频的完整验收。

正式交付数量为 150 个有效实例、150 条联合录制、每个 I 150 条不同录制和总计 150 条完整 episode。现有素材是否足量、实际物理检查及导出结果应以运行报告为准；少量 smoke 数据不代表正式数据完成。

本次实现的实测范围与未完成的数据验收见 [validation.md](docs/validation.md)。
