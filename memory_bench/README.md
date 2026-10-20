# Memory Bench

无需 K/Q 录制的单条 I/Bridge 拼接重渲染调试，见 [分开调试采集与拼接](docs/compose_debug.md)。

当前保留两个任务：Level 1 下层巧克力任务，以及 Level 2 杯子位置更新任务。共同使用 session 配置、有序 Key 列表、连续遥操与子任务独立保存、物理 Bridge、受控状态拼接和全场景 LeRobot 导出。Level 3 和策略评测不在本次范围内。任务模块保留现有 `tasks/` 布局，以 Query 活动名注册。

## 代码组织

| 术语 | 含义 |
| --- | --- |
| recording | 原始或规范化的录制 |
| segment | 从录制中提取的任务片段 |
| episode | 多个片段连接后的完整序列 |
| key / intervent / query / bridge | 片段承担的角色 |

```text
memory_bench/
├── collection/                  # 实例准备、场景采样与 K/Q 逐子任务采集
│   ├── prepare_session.py
│   ├── sample_instances.py
│   ├── collect_session.py
│   └── recording_wrapper.py     # 每子任务独立写盘，转场不录制
├── processing/                  # 录制规范化与片段提取
│   ├── scan_recordings.py
│   ├── process_recordings.py     # 自采 K/Q 和复用 I 的统一入口
│   ├── normalize_recording.py
│   ├── make_segments.py
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

`session.config` 保留完整的原始配置，用于冻结快照；后续处理无需重新读取配置 YAML。状态比较、机器人到点和停稳阈值由相应校验代码维护，不属于 Session 配置。实例清单、资产清单和整轮采集索引 `manifest.json` 是独立的运行数据，仍由对应流程读取。实际实例数继续由已冻结的 `case_assignments.json` 决定。

当前固定使用 R1Pro、独立的结束/开始按键和完整场景。同一实例的全部 Key/Query 共用第一 Key 的初始机器人状态，不另采样 Query 起点。K/Q 指令由任务定义提供，I 指令由录制活动名生成。标定进度保存在运行报告中，配置注释仅提醒阈值仍需物理标定。

`case_sampling.seed` 默认沿用 `instance_sampling.seed`，需要独立分配任务变体时再显式配置。调试配置的 `recordings.*.hdf5` 相对于 `paths.intervent_rawdata_root` 解析，也支持绝对路径；它与 `task_order` 中的任务一一对应。清理字段后配置快照会变化，已有冻结输出不会被自动改写，重新准备 session 应使用独立的输出目录。

运行前先激活预装的 `behavior` 环境，脚本直接使用当前终端的 `python`：

```bash
conda activate behavior
```

从仓库根目录运行脚本。直接修改 `scripts/common.sh` 顶部的路径、配置、session 名、场景、实例数和活动列表；路径、session、场景和活动需要与 YAML 配置一致。各脚本自己的参数也写在顶部，不通过命令行传参。默认使用 Level 1 下层巧克力任务。`INSTANCE_COUNT` 控制前面请求采样多少份 TRO；`assign-variants` 使用目录中实际已有的全部匹配 TRO 文件，后续步骤以保存的分配记录数量为准，不强制达到 YAML 中的计划数量。小规模联调请使用新的 `paths.output_root`，并同步修改 `common.sh` 中的 `MB_ROOT`。

| 配置 | 记忆需求 |
| --- | --- |
| `configs/mb_retrieve_chocolate_from_top_cabinet.yaml` | 下层厨房，一个 Key，两个候选吊柜；干扰任务为客厅开收音机、走廊整理鞋子 |
| `configs/mb_retrieve_target_mug_after_relocation.yaml` | Key 1 选择目标身份，Key 2 独立更新两只杯子的位置 |

本机资产根目录是 `/data2/bwang/code/BEHAVIOR-1K/datasets`，原始录制根目录是 `/data2/bwang/datasets/2026-challenge-rawdata`。检查配置中的三个绝对路径；环境变量 `OMNIGIBSON_DATA_PATH`、`MB_ROOT` 如已设置，必须与配置相同。

换任务时，在 `common.sh` 中修改 `MB_CONFIG`、`MB_SESSION_ID` 和 `ACTIVITIES`。活动列表按 Key 的采集顺序排列，最后一项是 Query。

`assets.requirements.*.instances` 填写 **Query 活动中的 BDDL 角色**，例如 `mug.n.04_1`，不填写采样生成的 `mug_231`。`assign-variants` 通过各实例的 Query 绑定解析实际物体，把结果冻结在 `case_assignments.json` 的 `asset_requirements` 中；后续组装与保护检查读取这些实际名字，生成过程中不需要修改配置。

各任务通过类别和模型共享各活动新插入的物体，沿用首次采样的名字和初始化来源（`asset_sources`）；已有场景物体按原名保留，不按 BDDL 编号合并。每个活动的新增物体必须具有不同的类别/模型组合，否则不能确定物理身份，会在分配阶段报错。双杯任务用 `tall_mug`、`short_mug` 和明确的模型对应关系选择变体；OG 的模型白名单不保证采到两种不同杯子，缺失或重复模型需要重新采样，不能靠改配置里的物体名解决。

下层任务使用 `house_double_floor_lower` 的厨房。Key 将餐桌上的巧克力收到 A/B 随机选定的吊柜并关好两个柜门；A 对应 `top_cabinet_tynnnw_0`（远离客厅），B 对应 `top_cabinet_tynnnw_1`（靠近客厅）。Query 的指令固定为取回此前收好的巧克力并放到餐桌，不包含位置答案；打开另一柜门会使录制无效。两个干扰任务是 `turning_on_radio`（task-0000，`living_room_0`）和 `putting_shoes_on_rack`（task-0022，`corridor_0`），记忆资产与两段干扰任务的使用集合分离。

启动脚本已选择该任务，独立输出根目录为 `/data5/bwang/datasets/memory-bench-lower`，当前仍只请求 5 个联调实例。原始干扰录制需放在配置的 `intervent_rawdata_root` 下对应任务目录。任务定义与离线条件检查不能替代巧克力抓取、关门后的视觉遮挡、真实录制边界及完整 Bridge 的仿真验收。

## 下载干扰任务录制

从 Hugging Face 的 `behavior-1k/2026-challenge-rawdata` 仅下载配置中 `intervent_task` 的
`source.task_id` 对应录制，保存到 `paths.intervent_rawdata_root`。巧克力配置选中 `task-0000`
和 `task-0022`；不会下载 Key/Query 或其他任务。

```bash
# 先查看任务、文件数和体积，不下载文件。
python -m memory_bench.tools.download_rawdata \
  --config memory_bench/configs/mb_retrieve_chocolate_from_top_cabinet.yaml --dry-run

# 下载两个任务的全部录制。
python -m memory_bench.tools.download_rawdata \
  --config memory_bench/configs/mb_retrieve_chocolate_from_top_cabinet.yaml

# 也可手动指定任务和保存目录。
python -m memory_bench.tools.download_rawdata \
  --task-ids 0 22 --local-dir /data5/bwang/datasets/2026-challenge-rawdata
```

`--local-dir` 可覆盖配置路径，`--workers` 默认 4；`--limit-per-task N` 可只下载每个任务按文件名排序的
前 N 条。默认下载所选任务的全部 HDF5，不受 `candidate_count_per_task` 限制。目录结构保持为
`task-XXXX/episode_XXXXXXXX.hdf5`。重复执行会复用已下载文件并继续未完成下载；请保留保存目录中的
`.cache/huggingface` 元数据。若数据集需要认证，使用已有 Hugging Face 登录或 `HF_TOKEN`。

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

2. 启动 JoyLo 硬件客户端与 session 服务端。服务端完整加载实例场景，按顺序采集全部 Key 与 Q，每个子任务独立保存 HDF5。左 JoyCon 的左方向键用于结束当前子任务：边界检查通过并保存关闭文件后，只恢复机器人到下一起点，保留所有物体状态；右方向键用于准备好并同步控制器后开始遥操。每个子任务内保留 JoyCon X/Y 的检查点和回滚，切换后清空旧检查点。键盘 X/P 保留开始、恢复和暂停，Home 加载下一已准备实例，Esc 退出；跳过未完成实例或退出时丢弃当前未完成段，保留已保存文件。恢复过程不进入录制，最终 Bridge 仍单独合成物理导航。详见[中文遥操说明](docs/memory_bench_teleoperation_zh.md)。

```bash
bash memory_bench/scripts/run_joylo.sh
# 另一个终端：
bash memory_bench/scripts/collect.sh
```

在 `collect.sh` 顶部设置起始 `INSTANCE_ID`、显示器和认证文件。Home 在同一服务端、客户端和窗口内加载下一已准备实例，无需逐实例重新启动；加载后暂停，等待右方向键或键盘 X。Query 保存后当前 run 标为 `complete` 并等待 Home，最后一个实例完成后正常退出。Home 跳过未完成实例时将旧 run 标为 `aborted`；没有下一实例时只提示并留在当前实例。Home 每次按下只推进一次，并沿用冷却限制。Esc / Capture 丢弃未完成段并退出；已完成 run 保持 `complete`。

硬件客户端默认使用 `/dev/ttyUSB1`，直接修改 `run_joylo.sh` 顶部的 `GELLO_PORT`。默认输出为 `recordings/<session_id>/instance_NNNN/run_NNNN/`，内含 `manifest.json`、`key_1.hdf5`、`query.hdf5` 及各自入口/出口 JSON；每个 HDF5 只有 `data/demo_0`。多 Key 额外保存 `key_2.hdf5` 等。每次进入实例都建立递增编号的新 run，保留旧文件。`--session-recording-dir` 指定起始实例目录，后续实例使用同一父目录下的标准 `instance_NNNN/` 目录；统一后处理默认从标准目录查找。新采集不生成 marks 文件。

## 统一处理、匹配、拼接与导出

自采 K/Q 和复用的 I 统一经由以下入口处理，生成相同结构的片段清单。每实例默认选择编号最新的完整 run，不跨 run 混用 Key/Query。K/Q 校验 TRO、文件哈希、任务边界及转场世界状态；I 从准备阶段扫描的候选清单中选择不同源录制，并检查场景、接口、任务、边界和保护物体。缺少完整录制或素材不足时停止。

```bash
bash memory_bench/scripts/process_recordings.sh
```

只处理一个实例或指定 run 时，在 `process_recordings.sh` 顶部设置 `PROCESS_OPTIONS=(--instance-id 1 --run-id 2)`；默认处理全部实例。中断后加 `--resume` 复用并核对已完成产物。每个源文件生成 N 个动作、N+1 个状态的规范化 HDF5，以及同一结构的片段清单；自采出口 JSON 补齐最后一个动作后的终态。统一索引为 `sessions/<session_id>/segment_index.json`，冻结原始文件、边界、规范化文件和清单的哈希。部分实例处理后的索引不可用于全量导出。

统一索引完成后，一键匹配素材、合成物理 Bridge、渲染导出并读回。此步骤只消费统一索引，不再隐式处理 I 素材：

```bash
bash memory_bench/scripts/export_session.sh
```

默认顺序种子为 1，直接修改 `export_session.sh` 顶部的 `ORDER_SEED`，需与实例种子不同。拼接只继承片段声明的写集合和机器人状态，保留其他任务的结果；I/B 全程不得改变保护物体。Bridge 在当前统一场景中执行动作并检查非机器人状态、接触和实际底盘终点，不传送机器人。完整序列重新渲染；Bridge 指令沿用下一个任务指令。答案、变体及绑定只进入内部注释，不进入逐帧指令。导出后读回动作、指令、时间戳、帧数、episode 数及视频。

已有产物拒绝覆盖；显式 `--resume` 只复用来源及哈希一致的已完成产物。失败报告保存在 `reports/<session_id>/`。输入变化或不完整规范化文件需要保留原始产物并换新的输出目录处理。新自采数据不再需要切割长录制或源视频，最终完整序列仍需重新渲染。

## 验收

```bash
bash memory_bench/scripts/check.sh
```

检查脚本运行 Ruff 代码/格式检查和离线测试。离线测试覆盖 Key 在序列中间的状态继承、多 Key 状态依赖、缺失终态、非法物体变更、机器人实际底盘边界、旧录制元数据迁移，以及 Level 2 两个 Key 的独立信息。旧录制只在内存中适配当前 Robot/控制器状态结构，原始 HDF5 保持不变。它们不能替代 GPU 场景、物理导航、遥操或 LeRobot 视频的完整验收。

正式交付数量为 150 个有效实例、150 轮完整 K/Q 尝试（每轮包含全部子任务的独立文件）、每个 I 150 条不同录制和总计 150 条完整 episode。现有素材是否足量、实际物理检查及导出结果应以运行报告为准；少量 smoke 数据不代表正式数据完成。

本次实现的实测范围与未完成的数据验收见 [validation.md](docs/validation.md)。
