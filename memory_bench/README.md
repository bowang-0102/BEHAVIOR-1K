# Memory Bench

无需 K/Q 录制的单条 I/Bridge 拼接重渲染调试，见 [分开调试采集与拼接](docs/compose_debug.md)。

实现范围为 Level 1 杯柜 demo，以及 Level 2 的位置更新和杯子/盘子信息综合任务。共同使用 session 配置、有序 Key 列表、联合遥操、物理 Bridge、受控状态拼接和全场景 LeRobot 导出。Level 3 和策略评测不在本次范围内。任务模块保留现有 `tasks/` 布局，以 Query 活动名注册。

## 代码组织

| 术语 | 含义 |
| --- | --- |
| recording | 原始或规范化的录制 |
| segment | 从录制中提取的任务片段 |
| episode | 多个片段连接后的完整序列 |
| key / intervent / query / bridge | 片段承担的角色 |

```text
memory_bench/
├── collection/                  # 实例准备、场景采样与 K/Q 联合采集
│   ├── prepare_session.py
│   ├── sample_instances.py
│   └── collect_session.py
├── processing/                  # 录制规范化与片段提取
│   ├── scan_recordings.py
│   ├── normalize_recording.py
│   ├── make_segments.py
│   ├── split_recordings.py
│   └── prepare_intervent.py
├── episodes/                    # 素材匹配、Bridge 合成、序列组装与导出
│   ├── pair_segments.py
│   ├── synthesize_bridges.py
│   ├── compose_episodes.py
│   └── render_episodes.py
├── tasks/                       # 任务声明、变体和条件检查
├── utils/                       # 跨流程复用的读取与校验能力
│   ├── recordings.py
│   ├── segment_manifest.py
│   ├── scene_state.py
│   ├── paths.py
│   └── navigation_map.py
├── tools/                       # 独立调试与物理审计
│   ├── debug_intervents.py
│   └── audit_world.py
├── scripts/                     # 环境设置与流程启动
├── configs/
├── tests/
├── docs/
├── session.py                   # Session 配置加载与依赖校验
└── navigation.py                # 机器人导航与关节运动控制
```

`processing/` 将原始录制整理为可复用片段，`episodes/` 将片段连接为完整序列。`utils/recordings.py` 提供录制解析和逐帧读取，`utils/segment_manifest.py` 提供片段加载、边界对齐及接口与哈希校验。各流程继续通过 `scripts/` 启动；代码调用关系见 [代码流程图](docs/code_flow.md)。

## 环境与配置

第一次阅读配置，先看 [带注释的 YAML 模板](docs/session_template.yaml) 和 [字段用途、默认值及代码使用位置](docs/configuration.md)。

所有 session YAML 均通过 `Session.load(config_path)` 加载和校验。正式采集与 `intervent_debug` 调试使用同一入口，加载后通过配置分组属性访问参数，例如 `session.scene_model`、`session.instance_sampling["seed"]`、`session.collection["key_segment_ids"]` 和 `session.export["obs_modalities"]`。采集与 Bridge 共用 `bridge.action_frequency`，由 `session.action_frequency` 读取，默认 30 Hz。调试专用字段通过 `session.task_order`、`session.recordings`、`session.recording_path(segment_id)`、`session.full_scene` 和 `session.open_doorways` 访问。

`session.config` 保留完整的原始配置，用于冻结快照；后续处理无需重新读取配置 YAML。状态比较、机器人到点和停稳阈值由相应校验代码维护，不属于 Session 配置。实例清单、资产清单和采集标记 `marks.yaml` 是独立的运行数据，仍由对应流程读取。实际实例数继续由已冻结的 `case_assignments.json` 决定。

当前固定使用 R1Pro、N 键推进采集和完整场景。同一实例的全部 Key/Query 共用第一 Key 的初始机器人状态，不另采样 Query 起点。K/Q 指令由任务定义提供，I 指令由录制活动名生成。标定进度保存在运行报告中，配置注释仅提醒阈值仍需物理标定。

`case_sampling.seed` 默认沿用 `instance_sampling.seed`，需要独立分配任务变体时再显式配置。调试配置的 `recordings.*.hdf5` 相对于 `paths.intervent_rawdata_root` 解析，也支持绝对路径；它与 `task_order` 中的任务一一对应。清理字段后配置快照会变化，已有冻结输出不会被自动改写，重新准备 session 应使用独立的输出目录。

运行前先激活预装的 `behavior` 环境，脚本直接使用当前终端的 `python`：

```bash
conda activate behavior
```

从仓库根目录运行脚本。直接修改 `scripts/common.sh` 顶部的路径、配置、session 名、场景、实例数和活动列表；路径、session、场景和活动需要与 YAML 配置一致。各脚本自己的参数也写在顶部，不通过命令行传参。默认使用 Level 1 杯柜 demo。`INSTANCE_COUNT` 控制前面请求采样多少份 TRO；`assign-variants` 使用目录中实际已有的全部匹配 TRO 文件，后续步骤以保存的分配记录数量为准，不强制达到 YAML 中的计划数量。小规模联调请使用新的 `paths.output_root`，并同步修改 `common.sh` 中的 `MB_ROOT`。

| 配置 | 记忆需求 |
| --- | --- |
| `configs/mb_retrieve_mug_from_top_cabinet.yaml` | 一个 Key，三个候选吊柜 |
| `configs/mb_retrieve_target_mug_after_relocation.yaml` | Key 1 选择目标身份，Key 2 独立更新两只杯子的位置 |
| `configs/mb_retrieve_mug_and_plate.yaml` | 分别记住杯子和盘子的位置 |

本机资产根目录是 `/data2/bwang/code/BEHAVIOR-1K/datasets`，原始录制根目录是 `/data2/bwang/datasets/2026-challenge-rawdata`。检查配置中的三个绝对路径；环境变量 `OMNIGIBSON_DATA_PATH`、`MB_ROOT` 如已设置，必须与配置相同。

换任务时，在 `common.sh` 中修改 `MB_CONFIG`、`MB_SESSION_ID` 和 `ACTIVITIES`。活动列表按 Key 的采集顺序排列，最后一项是 Query。

`assets.requirements.*.instances` 填写 **Query 活动中的 BDDL 角色**，例如 `mug.n.04_1`，不填写采样生成的 `mug_231`。`assign-variants` 通过各实例的 Query 绑定解析实际物体，把结果冻结在 `case_assignments.json` 的 `asset_requirements` 中；后续组装与保护检查读取这些实际名字，生成过程中不需要修改配置。

当前三个任务通过类别和模型共享各活动新插入的杯子、盘子，沿用首次采样的名字和初始化来源（`asset_sources`）；已有场景物体按原名保留，不按 BDDL 编号合并。每个活动的新增物体必须具有不同的类别/模型组合，否则不能确定物理身份，会在分配阶段报错。双杯任务用 `tall_mug`、`short_mug` 和明确的模型对应关系选择变体；OG 的模型白名单不保证采到两种不同杯子，缺失或重复模型需要重新采样，不能靠改配置里的物体名解决。

## 准备与采集

1. 一键生成全部 Key/Query 模板、第一 Key 的 TRO，通过 `assign-variants` 分配任务变体，检查 I 素材，生成共享场景模板与每实例最终 TRO。现有采样器输出到独立的 `memory-bench-task-instances`，不修改官方任务实例。

```bash
bash memory_bench/scripts/prepare_session.sh
```

准备分为两个独立阶段：`assign-variants` 只写 `case_assignments.json`，记录 case、对象绑定、原始 TRO 路径、配置与输入校验值；`sample_instances` 读取这份清单，合入干扰任务资产后启动仿真，检查第一个 Key 的初态、空手、碰撞和稳定性，并保存该实例全部 Key/Query 共用的完整机器人初态。每个实例通过检查后只写一份最终 TRO，共用一个场景模板：

```text
sessions/<session_id>/
├── session.json                 # 配置快照
├── case_assignments.json        # 分配结果与原始采样输入引用
├── scene_template.json          # 全部任务共用的场景和物体初始化信息
├── assets.json                  # 供采集、切段与拼接使用的共享资产清单
└── tro/
    ├── instance_0001.json       # 最终状态、绑定、共用机器人初态及检查结果
    └── ...
```

TRO 保存任务物体、机器人及其他发生变化的物体状态，不重复保存物体创建信息。采集和拼接时在内存中用公共模板与 TRO 恢复完整场景；不再生成每实例的完整场景、`poses.json`、`instances.json` 或独立 `instance.json`。实例仍标记为待物理标定，准备阶段的检查不能替代实际遥操与后续 Bridge 的物理验收。

准备阶段不主动改变门的开关状态。公共模板保留输入场景的门状态，实例按其原始 TRO 恢复任务物体状态，路线检查使用当前场景的实际状态。

原始模板和采样 TRO 不修改。分配清单、公共模板和最终 TRO 通过临时文件完成后再发布；中断后复用已完成的实例。重复运行会核对配置、任务定义、源文件和公共模板；输入冲突时报错。旧格式输出请保留，并为新流程选择独立的 `paths.output_root`。分配清单生成前中断，原始采样仍会重新执行。

已有原始采样文件时，也可分阶段运行：

```bash
python -m memory_bench.collection.prepare_session "$MB_CONFIG" assign-variants
python -m memory_bench.processing.scan_recordings "$MB_CONFIG"
python -m memory_bench.collection.sample_instances "$MB_CONFIG"
```

`utils/recordings.py` 负责读取 HDF5 录制的元数据和逐帧状态；准备脚本通过 `processing/scan_recordings.py` 扫描 I 任务的候选录制，检查活动、场景和房间是否匹配，生成 `reports/<session_id>/source_inventory.json`。

2. 启动 JoyLo 硬件客户端与 session 服务端。服务端完整加载实例场景，同一次 HDF5 中采集全部 Key 与 Q。按 `N` 请求开始或结束当前片段；边界检查通过后才推进。完成 Key 后只将机器人恢复到本实例 Key 1 的初始位置、朝向和关节状态，保留所有物体状态；停稳后再次按 N，同步控制器后继续遥操。恢复区间在切段时丢弃，最终 Bridge 仍单独合成物理导航。Q 打开错误柜门会拒绝该录制。

```bash
bash memory_bench/scripts/run_joylo.sh
# 另一个终端，每个实例一次：
bash memory_bench/scripts/collect.sh
```

逐实例修改 `collect.sh` 顶部的 `INSTANCE_ID`。显示器与认证文件也在顶部直接设置；自定义录制路径可在启动命令中添加 `--session-recording-path`。硬件客户端默认使用 `/dev/ttyUSB1`，直接修改 `run_joylo.sh` 顶部的 `GELLO_PORT`。入口/出口快照和 `marks.yaml` 保存在 HDF5 同名目录。采集转场仅用于联合录制，最终序列重新合成 Bridge。不要通过 reset 或重新加载 Query 场景切换任务。

## 切段、匹配、拼接与导出

一键规范化和切段全部实例的默认联合录制（`instance_NNNN_recording_0001.hdf5`）。脚本从 `marks.yaml` 读取 Query 出口快照补充终态，拒绝无效录制；录制缺失或处理失败时停止，不跳过实例。

```bash
bash memory_bench/scripts/split_recordings.sh
```

只处理一个实例时，把 `split_recordings.sh` 顶部的 `INSTANCE_ID` 改为对应数字；留空则处理全部实例。自定义录制文件名仍使用 `memory_bench.processing.normalize_recording`、`memory_bench.processing.make_segments` 的 CLI。状态约定为动作区间 `[start,end)`，出口为最后一个动作后的 `state[end]`。

所有联合录制切段完成后，一键选择 I 素材、匹配序列、合成物理 Bridge、渲染导出并读回：

```bash
bash memory_bench/scripts/export_session.sh
```

默认顺序种子为 1，直接修改 `export_session.sh` 顶部的 `ORDER_SEED`，需与实例种子不同。拼接只继承片段声明的写集合和机器人状态，保留其他任务的结果；I/B 全程不得改变保护物体。Bridge 在当前统一场景中执行动作并检查非机器人状态、接触和实际底盘终点，不传送机器人。完整序列重新渲染；Bridge 指令沿用下一个任务指令。答案、变体及绑定只进入内部注释，不进入逐帧指令。导出后读回动作、指令、时间戳、帧数、episode 数及视频。

已有产物拒绝覆盖；失败报告保存在 `reports/<session_id>/`。失败后检查原因，使用新的输出根目录重建，保留原始录制不变。

## 验收

```bash
bash memory_bench/scripts/check.sh
```

检查脚本运行 Ruff 代码/格式检查和离线测试。离线测试覆盖 Key 在序列中间的状态继承、多 Key 状态依赖、缺失终态、非法物体变更、机器人实际底盘边界、旧录制元数据迁移，以及 Level 2 两个 Key 的独立信息。旧录制只在内存中适配当前 Robot/控制器状态结构，原始 HDF5 保持不变。它们不能替代 GPU 场景、物理导航、遥操或 LeRobot 视频的完整验收。

正式交付数量为 150 个有效实例、150 条联合录制、每个 I 150 条不同录制和总计 150 条完整 episode。现有素材是否足量、实际物理检查及导出结果应以运行报告为准；少量 smoke 数据不代表正式数据完成。

本次实现的实测范围与未完成的数据验收见 [validation.md](docs/validation.md)。
