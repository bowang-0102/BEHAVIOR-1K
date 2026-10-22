# Memory Bench

当前保留两个任务：Level 1 下层巧克力任务，以及 Level 2 杯子位置更新任务。共同使用 session 配置、有序 Key 列表、连续遥操与子任务独立保存、物理 Bridge、受控状态拼接和全场景 LeRobot 导出。Level 3 和策略评测不在本次范围内。任务模块保留现有 `tasks/` 布局，以 Query 活动名注册。

任务文件中的 `init`、`goal`、`variant_init` 和 `variant_goal` 使用 BDDL 嵌套列表表达式；例如 `["not", ["open", "cabinet.n.01_1"]]` 表示柜门关闭。外层条件列表要求全部满足，并支持 `and`、`or`、`not`、`forall` 和 `exists`。量词范围由当前阶段的物体绑定确定。采集加载实例时预编译条件，之后通过 BDDL 的 `evaluate_state()` 和 OmniGibson 的 `evaluate_bddl_predicate()` 查询实时物体状态；编译缓存不进入任务定义快照。采样仍使用已有 `.bddl` 文件，采集仍使用 `DummyTask`；空手、停稳、无碰撞等边界检查由采集器维护。

## 代码组织

| 术语 | 含义 |
| --- | --- |
| recording | 原始 HDF5 录制 |
| segment | 从录制中提取的任务片段 |
| episode | 多个片段连接后的完整序列 |
| key / intervent / query / bridge | 片段承担的角色 |

```text
memory_bench/
├── collection/                  # 实例准备、场景采样与 K/Q 逐子任务采集
│   ├── prepare_session.py
│   ├── prepare_intervent.py       # 检查元数据、资产兼容并固定原始 I 录制
│   ├── sample_instances.py
│   ├── collect_session.py
│   └── recording_wrapper.py     # 每子任务独立写盘，转场不录制
├── episodes/                    # 素材匹配、Bridge 合成、序列组装与导出
│   ├── build_bridges.py          # Step 3：默认 demo_001，单独生成导航和 recipe
│   ├── synthesize_bridges.py
│   ├── compose_episodes.py
│   └── render_episodes.py
├── tasks/                       # 任务声明、变体和条件检查
├── utils/                       # 跨流程复用的读取与校验能力
│   ├── recordings.py
│   ├── task_segments.py         # 公共片段描述和逐帧检查
│   ├── segment_manifest.py
│   ├── scene_state.py
│   ├── paths.py
│   └── navigation_map.py
├── tools/                       # 素材下载与物理审计
│   ├── download_rawdata.py
│   └── audit_world.py
├── scripts/                     # 环境设置与流程启动
├── configs/
├── tests/
├── docs/
├── session.py                   # Session 配置加载与依赖校验
└── navigation.py                # 机器人导航与关节运动控制
```

旧录制省略休眠对象时，读取后续帧会按序累计先前状态，不落盘中间副本。`utils/recordings.py` 直接读取原始 HDF5：按录制场景解码序列化状态，或读取已有 `state_json`，并从真实出口快照补齐终态。`utils/task_segments.py` 描述任务读写范围并检查实际轨迹。导航与拼接分开，不生成 normalized HDF5、`segments/`、`segment_index.json`、`material_groups.json` 或独立段清单。设计见 [benchmark_design.md](docs/benchmark_design.md)。

## 环境与配置

第一次阅读配置，先看 [带注释的 YAML 模板](docs/session_template.yaml) 和 [字段用途、默认值及代码使用位置](docs/configuration.md)。

所有 session YAML 均通过 `Session.load(config_path)` 加载和校验。正式采集与 `intervent_debug` 调试使用同一入口，加载后通过配置分组属性访问参数，例如 `session.scene_model`、`session.instance_sampling["seed"]`、`session.collection["key_segment_ids"]` 和 `session.export["obs_modalities"]`。采集与 Bridge 共用 `bridge.action_frequency`，由 `session.action_frequency` 读取，默认 30 Hz。调试专用字段通过 `session.task_order`、`session.recordings`、`session.recording_path(segment_id)`、`session.full_scene` 和 `session.open_doorways` 访问。

`session.config` 保留完整的原始配置，用于冻结快照；后续处理无需重新读取配置 YAML。状态比较、机器人到点和停稳阈值由相应校验代码维护，不属于 Session 配置。实例清单、资产清单和整轮采集索引 `manifest.json` 是独立的运行数据，仍由对应流程读取。实际实例数继续由已冻结的 `variant_assignments.json` 决定。

当前固定使用 R1Pro、独立的结束/开始按键和完整场景。机器人位置和朝向沿用第一 Key 的采样结果，每轮采集启动时按当前 JoyLo reset 配置建立该轮全部 Key/Query 共用的机器人起点，不另采样 Query 起点。K/Q 指令由任务定义提供，I 指令由录制活动名生成。标定进度保存在运行报告中，配置注释仅提醒阈值仍需物理标定。

`variant_sampling.seed` 默认沿用 `instance_sampling.seed`，需要独立分配任务变体时再显式配置。调试配置的 `recordings.*.hdf5` 相对于 `paths.intervent_rawdata_root` 解析，也支持绝对路径；它与 `task_order` 中的任务一一对应。清理字段后配置快照会变化，已有冻结输出不会被自动改写，重新准备 session 应使用独立的输出目录。

任务变体统一通过 `variant_id` 标识；任务定义使用 `variant_init`、`variant_goal` 和 `variant_weights`，由 `sample_variants()` 分配到各实例。旧版冻结产物复用前，需要同步迁移配置与任务定义快照、分配文件名及路径引用，以及 JSON/HDF5 中的变体字段。

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

`assets.requirements.*.instances` 填写 **Query 活动中的 BDDL 角色**，例如 `mug.n.04_1`，不填写采样生成的 `mug_231`。`assign-variants` 通过各实例的 Query 绑定解析实际物体，把结果冻结在 `variant_assignments.json` 的 `asset_requirements` 中；后续组装与保护检查读取这些实际名字，生成过程中不需要修改配置。

各任务通过类别和模型共享各活动新插入的物体，沿用首次采样的名字和初始化来源（`asset_sources`）；已有场景物体按原名保留，不按 BDDL 编号合并。每个活动的新增物体必须具有不同的类别/模型组合，否则不能确定物理身份，会在分配阶段报错。双杯任务用 `tall_mug`、`short_mug` 和明确的模型对应关系选择变体；OG 的模型白名单不保证采到两种不同杯子，缺失或重复模型需要重新采样，不能靠改配置里的物体名解决。

下层任务使用 `house_double_floor_lower` 的厨房。Key 将顶柜下方厨房柜台（`bar_egwapq_0`）上的巧克力收到 A/B 随机选定的吊柜并关好两个柜门；A 对应 `top_cabinet_tynnnw_0`（远离客厅），B 对应 `top_cabinet_tynnnw_1`（靠近客厅）。Query 的指令固定为取回此前收好的巧克力并放到同一柜台面，不包含位置答案；过程中可以打开任一任务柜子，完成时需关好两个柜门。两个干扰任务是 `turning_on_radio`（task-0000，`living_room_0`）和 `putting_shoes_on_rack`（task-0022，`corridor_0`），记忆资产与两段干扰任务的使用集合分离。

启动脚本已选择该任务，独立输出根目录为 `/data/bwang/datasets/memory-bench-countertop-demo`，当前仍只请求 5 个联调实例。柜台版需要重新运行 `prepare_session.sh`；原有早餐桌版的准备结果和录制保留在旧输出目录，不能直接复用。原始干扰录制需放在配置的 `intervent_rawdata_root` 下对应任务目录。任务定义与离线条件检查不能替代巧克力抓取、关门后的视觉遮挡、真实录制边界及完整 Bridge 的仿真验收。

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

## 四步流程

### Step 1：准备实例

```bash
bash memory_bench/scripts/prepare_session.sh
```

依次运行 `prepare_session assign-variants`、`collection.prepare_intervent` 和 `sample_instances`。沿用原始采样与已有变体；`prepare_intervent` 只检查 I 的元数据和资产兼容性，并固定到 `intervent_assignments.json`，不启动仿真或解码轨迹。原始 I 文件只读。采样读取每实例对应 I 的真实第 0 帧物体状态，组装并检查场景，保存公共模板与最终 TRO。后续 Bridge 合成保留实际边界的衔接检查。

原始任务模板、采样 TRO 和任务元数据统一保存在 `$MB_ROOT/memory-bench-task-instances/`，与下面的 `sessions/` 一起迁移。代码从输出根目录定位它，并通过 `OMNIGIBSON_TASK_INSTANCES_DATASET` 把绝对路径传给 OmniGibson。`source_tro`、`inputs` 和公共模板中的分配文件引用均相对于 `MB_ROOT` 保存。官方资产仍由 `paths.data_root` 定位，原始 I 录制仍由 `paths.intervent_rawdata_root` 指定。

```text
sessions/<session_id>/
├── session.json
├── variant_assignments.json
├── intervent_assignments.json     # 每实例原始 I 路径和文件内 demo
├── assets.json
├── scene_template.json           # 无准备机器人的定义、关节或控制器状态
└── tro/instance_XXXX.json         # 世界差异、采样底座位置和朝向
```

机器人关节起点由采集时当前 `joylo/gello/utils/og_teleop_cfg.py` reset 配置建立。仅修改 reset 后重启采集即可。实例准备不统一开门，保留原始物体关节状态；不能通过覆盖 I 布局解决输入冲突。

已有旧版 `intervent_segments.json` 时，准备入口一次性提取其原始来源，保留已选录制，更新模板和 TRO 的分配引用，不改变世界状态、变体或原始采样。旧产物保留，不自动删除。

### Step 2：遥操采集

```bash
bash memory_bench/scripts/run_joylo.sh
# 另一个终端
bash memory_bench/scripts/collect.sh
```

在完整世界中依次完成全部 Key 和 Query，各自保存独立原始 HDF5 和入口/出口。左方向键结束并保存子任务，检查通过后只恢复机器人到本轮公共起点；暂停等待期间持续检查 GELLO 同步，未对齐显示“Waiting for GELLO to align...”，对齐后显示“Ready. Press Right (JoyCon) or X (keyboard) to start”。按开始键后再次确认同步再进入录制。Home 在同一服务端切换实例，Esc 丢弃未完成段并保留已保存文件。恢复和等待不录制。详见[中文遥操说明](docs/memory_bench_teleoperation_zh.md)。

```text
recordings/<session_id>/instance_0001/demo_001/
├── manifest.json
├── key_01.hdf5                   # 每文件仅 data/demo_0
├── key_01_entry.json
├── key_01_exit.json
├── query.hdf5
├── query_entry.json
└── query_exit.json
```

按键盘 R 从第一个 Key 重录当前实例，恢复完整初始场景并新建下一编号的 demo（如 `demo_002`），保留旧轮已保存文件；未完成旧轮标为 `aborted`，已完成旧轮保持 `complete`。重置后暂停等待开始，最后一个实例完成后也可按 R 重录或 Esc 退出。暂停/回滚/切换子任务不创建新轮。多 Key 使用 `key_02` 等。目录 demo 编号是一整轮 K/Q，文件内部的 `data/demo_0` 是该子任务的一条轨迹，含义不同。逻辑任务 ID 继续使用配置中的 `key_1` 等。

### Step 3：独立合成 Bridge

```bash
bash memory_bench/scripts/build_bridges.sh
```

脚本顶部 `DEMO_ID=1`，默认每实例只用 `demo_001`，缺失或不完整时报错，不自动改用其他轮或混轮。需要其他轮时明确修改该参数。`BUILD_OPTIONS=(--instance-id 1)` 可单独调试某个实例。`ORDER_SEED` 与实例种子独立；每实例只选择一个合法顺序，I 分配不变。

入口检查采集文件、公共起点、转场及 I 布局，读取任务首尾状态与声明的读写范围，在继承世界中执行收臂、导航和展臂，保存原始 B HDF5 及入口/出口。导航碰撞、非机器人世界保持和终点检查保留。第三步不拼接、不渲染。

```text
bridges/<session_id>/episode_00000000/
├── bridge_00.hdf5
├── bridge_00_entry.json
├── bridge_00_exit.json
└── ...
recipes/<session_id>/episode_00000000.json
```

recipe 是唯一交接文件，内嵌 K/I/Q/B 的原始来源、局部动作范围、任务/接口/物体声明和边界引用，记录选中的实例、采集 demo、任务顺序及完整 sequence，不引用独立段清单。失败不会重新选择 I、采集 demo 或顺序。已有成功 B 核对入口和终点后可复用，recipe 发布后拒绝覆盖。

流程通过路径、实例和任务身份、接口及实际状态检查衔接，不计算或校验文件内容哈希。旧产物中已有的哈希字段不参与读取和验证，新产物不再写入这些字段。

### Step 4：拼接、回放与导出

```bash
bash memory_bench/scripts/export_session.sh
```

`compose_episodes` 只消费第三步 recipe，按公共接口逐帧读取并解码原始录制，检查真实动作/状态对齐、任务范围、拓扑和记忆保护，只更新任务写对象及机器人。真实变化超出声明时拒绝发布 episode，不产生 normalized 副本，也不再合成导航。随后完整回放和重新渲染，不拼接源视频。

```text
episodes/<session_id>/
├── episode_00000000.hdf5
├── episode_00000000_scene.json
└── episode_00000000_query_start.json
lerobot/<session_id>/
├── meta/
├── data/
├── videos/
├── annotations/
├── segment_states/
└── export_contracts/
```

`*_query_start.json` 是最终累计世界中 Query 第一条动作之前的完整可恢复场景，包含全部物体、机器人关节/控制器、创建信息和 Query 绑定。recipe 记录其路径和全局帧号。评测从这里恢复；遥操 `query_entry.json` 不包含后来插入 I/B 的累计结果。整条序列起点单独保存在 `*_scene.json`。

拼接成功后 recipe 补充最终帧区间、记忆跨度和输出引用。渲染可单独重跑：`python -m memory_bench.episodes.render_episodes "$MB_CONFIG"`，不重新生成 Bridge 或拼接。已有导出拒绝覆盖；失败诊断和读回结果保存在 `reports/<session_id>/`，正式步骤不依赖报告传递素材。

## 验收

```bash
bash memory_bench/scripts/check.sh
```

检查脚本运行 Ruff 代码/格式检查和离线测试。离线测试覆盖 Key 在序列中间的状态继承、多 Key 状态依赖、缺失终态、非法物体变更、机器人实际底盘边界、旧录制元数据迁移，以及 Level 2 两个 Key 的独立信息。旧录制只在内存中适配当前 Robot/控制器状态结构，原始 HDF5 保持不变。它们不能替代 GPU 场景、物理导航、遥操或 LeRobot 视频的完整验收。

正式交付数量为 150 个有效实例、150 轮完整 K/Q 尝试（每轮包含全部子任务的独立文件）、每个 I 150 条不同录制和总计 150 条完整 episode。现有素材是否足量、实际物理检查及导出结果应以运行报告为准；少量 smoke 数据不代表正式数据完成。
