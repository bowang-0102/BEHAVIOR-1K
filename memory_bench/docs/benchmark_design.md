# Memory Bench：bench 构造思路与项目实现思路

## 一、bench 构造思路

### 1.1 benchmark 构想、难度等级与任务序列

整个 benchmark 围绕“先获取记忆信息，经过其他任务后，再利用历史信息完成目标任务”构建，规划三个难度等级：

| 等级 | 名称 | 核心设定 |
|---|---|---|
| Level 1 | 单片段记忆依赖 | Q 依赖一个 Key 记忆片段中的信息完成任务 |
| Level 2 | 多片段记忆依赖 | Q 依赖两个或更多 Key 记忆片段中的信息完成任务 |
| Level 3 | 超长程 | 沿用 Level 2 的多 Key 设定，通过增加间隔任务，将整条任务序列延长至接近 1 小时 |

Level 1、Level 2 按 Q 实际依赖的 Key 记忆片段数量划分。每个 Key 是任务设计中预先定义的记忆片段，不通过任意切分同一段操作增加片段数；同一个 Key 内可以包含多条信息或多次操作。任务定义说明各 Key 提供的信息及其作用，等级由实际记忆需求确定。

静态世界记忆、动态记忆更新和多条信息综合等是具体任务类型，可以在相应等级下设计不同任务。当前只保留 Level 1 下层巧克力任务和 Level 2 杯子位置更新任务。前者记住一个柜子位置，后者结合目标身份与后续位置更新；信息综合等其他任务类型留作后续扩展。

Level 3 的时长指完整 K/B/I/Q 序列的仿真执行时长。本阶段仅保留这一构想，具体任务设计和项目实现先规划 Level 1、Level 2；暂不制定 Level 3 的素材数量、时长验收和实现步骤。

**任务片段与排序**

一个任务序列由关键任务片段（Key，K）、衔接导航片段（Bridge，B）、间隔任务片段（Intervening，I）和目标执行任务（Query，Q）组成。

| 片段 | 作用 | 数量与来源 |
|---|---|---|
| K | 提供后续 Q 所依赖的记忆信息，例如物体被放到哪个柜子 | 一个或多个，自行设计并遥操采集 |
| B | 连接相邻任务片段的出口与入口，保证空间及机器人运动连续 | 每两个相邻任务片段之间一段，自动合成 |
| I | 在 K 与 Q 之间形成任务间隔 | 多个，复用已有 2026 任务录制 |
| Q | 利用此前 K 中获得的信息完成目标任务 | 每个序列一个，与关联的 K 一起遥操采集 |

排序由片段列表及依赖关系决定，不能把 K 固定为第一个片段。K 可以位于序列中间，也可以有多个；所有被 Q 依赖的 K 必须先于 Q。K 前后均可插入 I 或其他 K，相邻任务之间统一插入 B。本bench以 Q 作为最终执行任务供模型执行。

例如，以下都是接口需要支持的顺序：

```text
K₁ → B → I₁ → B → I₂ → B → Q
I₁ → B → K₁ → B → I₂ → B → Q
I₁ → B → K₁ → B → K₂ → B → I₂ → B → Q
```

第三例中 Q 可以同时依赖 K₁、K₂。若多个 K 操作同一物体，还须声明它们的状态依赖，不能只检查它们都排在 Q 前面。位于某个 K 之前的 I 不计入该 K 到 Q 的记忆间隔；每个 K 的记忆距离分别记录。

### 1.2 场景与空间约束

整个序列在同一个包含多个房间的场景内完成：

- Q 与它依赖的所有 K 安排在同一个房间，建立记忆信息与执行位置的空间关联。
- 各 I 安排在不同房间。检查实际的房间实例 `room_id`，不能只按 `bedroom` 等房间类型判断。
- B 连接前一片段的结束位置与后一片段的起始位置，在统一场景中规划并执行。

导航根据当前世界的门状态选择可通行路线。实例准备不自动打开房间门，门可能属于任务操作对象。B 本身只负责机器人运动，不承担开门操作；无可行路线时拒绝该衔接。

### 1.3 物体资产隔离与记忆对应

**K/Q 可以复用场景已有资产，也可以插入新资产，不强制新增。** 选择依据是：K/Q 使用的物体没有被任何 I 使用，并且各片段的物体状态能够兼容。

具体检查：

1. 各 I 的任务物体、容器和相关支撑物互不共用，避免前一个 I 改变后一个 I 所依赖的状态。
2. K/Q 的任务物体、容器和相关支撑物不属于任何 I 的使用集合。不能只检查 I 有没有移动它们，还应检查任务绑定和状态依赖。
3. K 与 Q 中承担记忆依赖的物体保持同一身份，使用固定的场景物体 ID，并记录各片段的 BDDL 绑定；Q 不重新采样另一份外观相同的物体。在记忆更新任务中，Key 可以按任务设计改变这些物体的位置或状态，身份对应仍保持不变。
4. 可先检查已有场景资产是否满足隔离、可达和状态要求；不满足时再插入新物体。新增物体使用独立名称，并检查放置区域及导航空间。
5. 即使物体使用集合不相交，也要检查初态、前置条件和空间碰撞，排除状态依赖冲突。

物体出现在 I 的全场景文件或相机背景里，不等于被 I 使用。使用集合由任务绑定、显式声明的容器/支撑物及实际轨迹中的交互共同确定。机器人、地板等公共结构单独做空间与状态检查，不作为跨任务操作资产。

### 1.4 一个 session 的设计与构建流程

session 表示一套冻结的场景、任务片段、资产绑定、采样规则和编排配置，其下可以生成多个场景实例与完整 episode。

1. **选择场景与 I。** 从已有 2026 任务中确定共同场景和多个间隔任务，确认房间不同、物体隔离、录制接口兼容。
2. **设计 K/Q。** 确定 Level 1 或 Level 2，定义各 Key 提供或更新的信息、Q 的依赖和完成条件；Level 2 还要确定 Key 的采集顺序及状态依赖。选择未被 I 使用的已有资产或新增资产，冻结身份对应关系。
3. **编写 BDDL 并准备实例。** 为 K/Q 编写 BDDL，先用少量实例贯通采集、导航与渲染。冻结配置、原始采样和变体分配后，准备并固定每个实例的 I 录制，用其入口物体状态生成 TRO；机器人沿用采样位置和朝向，采集时应用当前 reset。正式数据数量统一见 1.5。
4. **连续遥操、独立保存。** 按 Key 顺序完成所有 K，再完成 Q。每个子任务完成后按左方向键，检查通过后先保存并关闭独立 HDF5，再只恢复机器人到本实例 Key 1 的完整初始状态，保留全部物体状态。停稳后按右方向键，同步控制器并开始下一段；恢复和等待不录制。每段保留任务内检查点及回滚，不能跨子任务回滚。
5. **构建 episode recipe。** `build_episodes.sh` 默认选每实例的完整 `demo_001`，沿用固定 I，指定合法顺序，在累计世界中合成物理导航，保存原始 B HDF5 和唯一 recipe。
6. **拼接与重渲染。** 按指定顺序拼接 K、I、Q、B，在统一完整场景中 replay 并重新渲染，输出完整 VLA 训练数据及片段注释。

多 K 时，采集与后处理使用有序 Key 列表；各 Key 分别保留文件、边界和依赖。Key 的采集顺序保证状态依赖成立，完整序列的 I 插入位置在拼接时指定。采集转场用于取得下一 Key 或 Q 的入口，不直接代替最终序列经过 I 的衔接路线。

### 1.5 数据与记忆有效性

- 采样 150 条有效场景实例，每个实例完成一轮有效 K/Q 尝试，每轮保存全部 Key 与 Query 的独立 HDF5 及入口/出口快照。
- 采集前，对每个选定的 I 准备足量不同且通过兼容性及物理边界检查的录制，按实例编号一一分配；源录制路径、文件内 demo 和对应关系由 `intervent_assignments.json` 保存。每实例按对应 I 入口生成 TRO，采集后不再换源。
- 完整任务序列的顺序在拼接阶段指定，提供不多于三种合法顺序。例如可采用 K→I₁→I₂→Q、I₁→K→I₂→Q、I₂→K→I₁→Q；每组素材选择其中一种，检查状态依赖并生成对应 B。
- 场景实例、K/Q 源录制、I 源录制和输出 episode 分别编号。150 组素材各生成一条完整 episode，所有顺序合计 150 条；不同顺序的数量不要求均匀，记录每条 episode 的实际顺序即可。
- 同一 Key 任务必须设计多种记忆情况，并在不同实例中覆盖；每个实例执行其中一种。同一 K/Q 模板的 Q 指令保持一致，正确执行目标随 Key 的实际情况变化，要求模型利用 Key 的历史观测确定目标。Key 情况与拼接顺序独立分配，Q 的起始观测不应直接泄露记忆答案。
- 按 Q 实际需要的 Key 信息检查记忆依赖：Level 1 需要一个记忆片段，Level 2 需要两个或更多记忆片段。各 Key 提供的信息、必要的更新关系和最终有效信息按任务定义保存；任务设计应避免最后一个 Key 直接复述 Q 所需的全部信息，使前面的 Key 失去作用。I/B 不得改变承担记忆依赖的物体；静态记忆任务另外检查相关记忆状态保持不变。
- 同一实例的所有 Key 与 Query 共用 Key 1 的初始机器人状态。起点多样性来自不同实例的原始采样，并与变体答案分配独立；最终任务顺序另行分配，不能由起点推断答案。
- Q 指令不直接给出需要回忆的答案。答案、物体绑定和变体信息保存在内部注释中，不进入逐帧指令或策略观测。
- 每条输出 episode 保留完整 K、I、B、Q 的观测、动作、指令与片段边界；少量联调数据不替代正式的 150 组素材。
- 本阶段只负责数据构建，评测框架和数据划分先不涉及。

## 二、项目实现思路

### 2.1 输入输出目录与运行约定

先统一路径、文件命名及各阶段的输入输出，再实现后续模块。以下是公共接口的目录约定；路径由 session 配置传入，各任务共用，不写死在任务模块中。

**运行环境与根目录**

- 仓库根目录为 `/data2/bwang/code/BEHAVIOR-1K`，代码、BDDL 和配置保存在仓库中。
- Python 和仿真使用 `behavior` conda 环境；自动化检查与测试设置 `OMNIGIBSON_HEADLESS=1`，遥操使用硬件与 GUI 所需的启动配置。
- `paths.data_root` 与 `OMNIGIBSON_DATA_PATH` 保持一致，用于定位资产、官方场景和任务实例数据集。
- `paths.output_root` 是 Memory Bench 数据产物根目录，本文简称 `$MB_ROOT`；启动脚本中的 `MB_ROOT` 与其保持一致。
- 2026 原始录制通过 `paths.intervent_rawdata_root` 定位，当前路径为 `/data2/bwang/datasets/2026-challenge-rawdata`。

当前机器的路径配置示例，输出根目录可按存储位置调整：

```yaml
paths:
  data_root: /data2/bwang/datasets
  intervent_rawdata_root: /data2/bwang/datasets/2026-challenge-rawdata
  output_root: /data2/bwang/datasets/memory-bench
```

**输入目录**

| 输入 | 路径约定 | 用途 |
|---|---|---|
| 场景与物体资产 | `$OMNIGIBSON_DATA_PATH/` 下现有 OmniGibson 数据 | 加载完整场景及已有/新增物体 |
| 官方任务实例 | `$OMNIGIBSON_DATA_PATH/2026-challenge-task-instances/` | 读取场景模板、任务元数据和 I 所需绑定 |
| 2026 原始录制 | `<intervent_rawdata_root>/task-XXXX/episode_XXXXXXXX.hdf5` | 每个选定 I 的候选录制，采集前选取、检查并按实例固定 |
| K/Q 的 BDDL | `<仓库根目录>/bddl3/bddl/activity_definitions/<activity>/problem0.bddl` | 定义初态、目标和任务物体 |
| session 配置 | `<仓库根目录>/memory_bench/configs/<session_id>.yaml` | 指定场景、任务、资产规则、150 条实例及不多于三种顺序 |

官方任务实例与 2026 原始录制只读。选取 I 时保存源路径、demo 编号和帧区间，通过清单引用源录制。

**输出目录**

采样器使用的模板、TRO 和任务元数据写入 `$MB_ROOT/memory-bench-task-instances/`，并设置 `OMNIGIBSON_TASK_INSTANCES_DATASET` 为该目录的绝对路径。该目录遵循 OmniGibson 的任务实例组织，与 `sessions/`、`recordings/` 同处输出根目录，方便一起迁移。分配清单中的采样引用相对于 `MB_ROOT` 保存；下面的 session 目录保存共享场景模板、分配清单及最终 TRO。

```text
$MB_ROOT/
├── sessions/<session_id>/
│   ├── session.json                         # 本次构建的配置快照与解析后的路径
│   ├── assets.json                          # 资产来源、统一物体 ID 及保护集合
│   ├── variant_assignments.json             # 变体、绑定及原始采样输入引用
│   ├── scene_template.json                  # 共享场景与全部任务物体初始化信息
│   ├── tro/instance_XXXX.json               # 每实例最终状态、起点与检查结果
│   └── intervent_assignments.json           # 每实例固定的原始 I 路径和文件内 demo
├── recordings/<session_id>/instance_XXXX/demo_XXX/
│   ├── manifest.json                         # 整轮状态、TRO/文件身份和转场检查
│   ├── key_<nn>.hdf5                         # 每个子任务独立，data/demo_0
│   ├── query.hdf5
│   ├── key_<nn>_entry.json                    # 各 Key 入口的完整场景及机器人状态
│   ├── key_<nn>_exit.json                     # 各 Key 出口的完整场景及机器人状态
│   ├── query_entry.json                     # Q 入口完整状态，包含实际机器人位姿
│   └── query_exit.json                      # Q 出口完整状态
├── bridges/<session_id>/<episode_id>/bridge_XX.hdf5
├── recipes/<session_id>/<episode_id>.json    # 片段顺序、来源及衔接的完整配方
├── episodes/<session_id>/<episode_id>.hdf5   # 拼接后的统一状态与动作
├── episodes/<session_id>/<episode_id>_scene.json  # 整条序列的完整回放起点
├── episodes/<session_id>/<episode_id>_query_start.json  # Query 首个动作前的完整评测状态
├── reports/<session_id>/...                  # 采样、兼容性、导航及导出检查报告
└── lerobot/<session_id>/                     # 所有顺序合计 150 条完整 episode
    ├── meta/
    ├── data/
    ├── videos/
    ├── annotations/<episode_id>.json
    └── segment_states/<episode_id>/seg_XX.json
```

**命名、引用与阶段边界**

- `session_id` 暂直接使用唯一 Query task 的 BDDL 活动名，与 Query 片段的 `activity` 字段一致；配置文件名和各产物目录统一使用该名称。实例 TRO 按 `instance_0001.json` 至 `instance_0150.json` 命名，每个实例保留完整有效的采集 run，例如 `recordings/<session_id>/instance_0003/demo_002/`，其中每个子任务对应独立文件；每次进入实例建立递增 run 目录，Home 在同一服务端、客户端和窗口内推进到下一已准备实例。
- 同一 `output_root` 下，一个 Query 活动名对应一份 session 配置。需要保留同一 Query 的不同配置时，使用不同输出根目录；已有产物不直接覆盖。
- 输出 episode 按 `episode_00000000` 至 `episode_00000149` 编号，通过 recipe 和匹配索引关联原实例、各源录制及分配的顺序。
- 片段 ID 使用完整类型名称及 session 内部序号，如 `key_1`、`intervent_1`、`intervent_2`、`query`、`bridge_00`；I 的原任务编号由 `source.task_id` 指定。`role` 统一为 `key_task`、`intervent_task`、`query_task`、`bridge_task`。每个 Key 使用独立片段 ID，任务变体统一通过 `variant_id` 记录；片段角色、记忆情况与排序分别记录。
- 配置及清单中，本项目产物目录和源文件路径相对 `$MB_ROOT`；目录内的文件名相对明确声明的目录，例如 `manifest.json` 中片段的 `states.directory`。外部输入使用绝对路径；运行时按这些约定统一解析，禁止依赖某个进程的当前目录。
- 源录制、尝试索引、片段清单、配方和最终训练数据分别保留。构建失败写入 `reports/`，成功产物可沿来源清单追溯。

| 阶段 | 主要输入 | 主要输出 |
|---|---|---|
| 场景与实例准备 | session 配置、BDDL、场景资产、官方任务元数据 | 任务实例数据集、150 条完整初态与共用机器人初态、资产和实例清单 |
| 连续遥操 | 实例完整初态、K/Q 定义、Key 采集顺序、共用机器人初态 | 每个实例一轮有效尝试、各子任务独立 HDF5 和入口/出口状态 |
| 独立合成 Bridge | 固定 I 原始录制、完整 K/Q demo、边界、候选顺序、资产约束 | 原始 B HDF5、入口/出口和唯一 recipe |
| 拼接、回放与导出 | recipe、原始 K/I/Q/B、实例世界和传感器配置 | 完整 episode、Query 评测起点、LeRobot 数据、片段注释及读回报告 |

### 2.2 范围与模块组织

先提供覆盖 Level 1、Level 2 的公共接口，使用 Level 1 的“吊柜里的巧克力”作为默认 demo。采样、采集、编排和渲染模块不能写死杯子、厨房、任务 18/19、一个 Key 或 Key 位于序列开头。

任务覆盖前两个等级；公共采集入口按有序 Key 列表推进，单 Key 是列表长度为一的情况，切段、导航、拼接和渲染共用：

| 等级 | 任务模块提供的信息 | 采集及处理差异 |
|---|---|---|
| Level 1：单片段记忆依赖 | Q 所需的一个 Key 记忆片段、Q 指令、记忆情况和任务条件检查 | 按 Key 列表采集；单 Key 流程完成 Key 后切换至 Q，检查实际记忆依赖及任务状态 |
| Level 2：多片段记忆依赖 | Q 所需的多个 Key 记忆片段、各片段的信息、必要的更新关系和依赖 | 遥操按 Key 列表逐段推进；检查每段前置/后置状态，记录各自入口、出口及转场，再进入 Q |

```text
memory_bench/
├── collection/                 # 实例准备、场景采样与 K/Q 逐子任务采集
│   ├── prepare_session.py
│   ├── prepare_intervent.py      # 检查元数据、资产兼容并固定原始 I 分配
│   ├── sample_instances.py
│   └── collect_session.py
├── episodes/                   # 片段匹配、物理 Bridge、状态拼接与导出
│   ├── build_episodes.py         # Step 3：选择素材、生成 Bridge 和 episode recipe
│   ├── synthesize_bridges.py
│   ├── compose_episodes.py
│   └── render_episodes.py
├── tasks/                      # Level 1 / Level 2 任务定义，按 Query 活动注册
├── utils/                      # 录制读取、片段描述、场景状态、路径和导航地图
│   └── task_segments.py         # 片段描述和逐帧检查的公共函数
├── tools/                      # 素材下载与录制回放
├── scripts/                    # 环境与流程启动脚本
├── configs/                    # Session、任务顺序、采集与导出配置
├── tests/
├── docs/
├── session.py                  # Session 配置加载与依赖校验
└── navigation.py               # 机器人导航与关节运动控制
```

`recording` 表示原始录制，`segment` 表示从录制中提取的任务片段，`episode` 表示多个片段连接后的完整序列；`key`、`intervent`、`query`、`bridge` 表示片段角色。`MemoryTask` 使用有序 Key 列表和单个 Query，准备、采集及后处理共用同一份 session 配置。

公共模块处理场景、片段和依赖；任务模块以 Query 的 BDDL 活动名注册，提供该 session 的 BDDL 活动、资产声明、记忆绑定、采样约束、指令和段条件检查。各片段通过 `activity` 指定自己的 BDDL 活动。Level 1 的单 Key 巧克力任务和 Level 2 的杯子位置更新任务保留在 `tasks/` 下，共用多 Key 采集流程。

### 2.3 通用配置与任务接口

session 用片段 ID 表示任务，每份拼接配方的 `task_order` 只列 K/I/Q；编排器在相邻片段间生成 B。采集只记录 K/Q，完整顺序在拼接时指定。房间 ID、资产绑定和每实例 I 源录制在准备阶段冻结。I 选取数量及完整 episode 数量以已保存的变体分配数量为准，其余固定规则沿用 1.5。

```yaml
session_id: mb_retrieve_chocolate_from_top_cabinet
benchmark:
  level: 1
scene_model: house_double_floor_lower
instance_sampling:
  count: 150
  seed: 0
intervent_selection:
  candidate_count_per_task: 200
memory_room: kitchen_0
assets:
  manifest: sessions/mb_retrieve_chocolate_from_top_cabinet/assets.json  # 每项注明 source: scene 或 insert
segments:
  key_1:
    role: key_task
    activity: mb_chocolate_into_top_cabinet
    room_id: kitchen_0
    depends_on: []
  intervent_1:
    role: intervent_task
    activity: turning_on_radio
    room_id: living_room_0
    source: {task_id: 0}
    depends_on: []
  intervent_2:
    role: intervent_task
    activity: putting_shoes_on_rack
    room_id: corridor_0
    source: {task_id: 22}
    depends_on: []
  query:
    role: query_task
    activity: mb_retrieve_chocolate_from_top_cabinet
    room_id: kitchen_0
    depends_on: [key_1]
composition:
  task_orders:
    - [key_1, intervent_1, intervent_2, query]
    - [intervent_1, key_1, intervent_2, query]
    - [intervent_2, key_1, intervent_1, query]
collection:
  key_segment_ids: [key_1]
```

`segments` 以片段 ID 为键，每个键对应一份片段定义，无需在定义内部重复写 `id`。片段的执行顺序由 `task_orders` 或具体配方的 `task_order` 指定，映射中的书写顺序不作为执行顺序。`depends_on` 始终是片段 ID 列表，Q 可以依赖多个 K。增加 Key 时，扩展 `segments`、采集 Key 列表及 Q 的依赖，不新增一套专用流程。以下顺序同样应由公共编排器处理：

```yaml
# 单 Key，前后均有 I
task_order: [intervent_1, key_1, intervent_2, query]

---
# 多 Key；segments.query.depends_on 为 [key_1, key_2]
task_order: [intervent_1, key_1, key_2, intervent_2, query]
```

`benchmark.level` 按 Q 实际依赖的 Key 记忆片段数记录难度等级：Level 1 为一个，Level 2 为两个或更多。`segments.query.depends_on` 声明完整的记忆依赖 Key 集合，检查时结合任务定义确认各片段的信息确实被 Q 使用。等级不直接规定拼接顺序。`collection.key_segment_ids` 是实际遥操的有序 Key 列表；Level 2 的配置例如 `[key_1, key_2]`，使用左方向键结束并保存、右方向键准备好后开始，依次推进至 Q。

`depends_on` 按角色表达依赖：Q 列出必须回忆的 Key；Key 列出建立其前置状态的任务。例如 `key_2.depends_on: [key_1]` 要求采集与拼接都保持这一先后关系；互相独立的信息综合任务可不声明 Key 之间的依赖。编排器检查引用和顺序，任务模块检查前置/后置条件及最终答案；各 Key 的信息是否不可缺少由任务设计确认，依赖列表本身不能证明记忆有效。

下面是 Level 2 记忆更新任务的配置片段，活动名、资产和间隔任务由具体任务补充；信息综合任务若两个 Key 无先后依赖，可以将 `key_2.depends_on` 设为空列表：

```yaml
benchmark:
  level: 2
collection:
  key_segment_ids: [key_1, key_2]
segments:
  key_1:
    role: key_task
    depends_on: []
  key_2:
    role: key_task
    depends_on: [key_1]
  query:
    role: query_task
    depends_on: [key_1, key_2]
```

**任务信息放在哪里、由谁使用**

公共采样、录制、切段和拼接程序需要读取各任务的信息。信息分布在 session 配置、任务定义、BDDL 和生成的实例/段清单中，按下表组织；检查函数放在对应的任务模块中。

`tasks/base_task.py` 和各任务文件定义活动名、指令、模型白名单、房间类型、变体绑定与段检查；公共流程读取这些定义。

| 所需信息 | 定义或保存位置 | 哪一步使用、做什么 |
|---|---|---|
| 每个片段的 BDDL 活动与指令 | 配置中的 `segments.<片段 ID>.activity`；任务定义中的片段指令，I 指令从源录制取得 | 实例准备时加载对应 BDDL；遥操和导出时使用该片段指令 |
| 物体模型、房间及任务使用对象 | 任务定义中的 `whitelist`、`room_types` 和 BDDL 物体声明；准备后写入 `assets.json`，切段后保存段清单的 `objects.used` | 准备场景与采样物体；选取 I 时检查任务资产不共用 |
| K/Q 使用的是哪些实际物体、哪些物体需要保护 | 任务定义的 `variants`；实例保存 BDDL 到场景物体的绑定，资产清单保存保护集合；段清单保存 `objects.memory_bindings` | 确保 Q 操作 K 中的同一物体；拼接及 B 合成时检查这些对象未被无关片段改变 |
| 初始状态、多种 Key 情况和共用机器人初态 | BDDL 初始条件、任务变体与第一 Key 原始 TRO；检查后的状态保存到最终 TRO | 生成 150 条实例；遥操切换时恢复该实例的完整机器人初态，保留世界状态 |
| Key 结束及 Query 开始/结束时是否满足任务要求 | `tasks/` 中的任务检查函数；读取 BDDL 条件和录制状态 | 采集时检查任务边界；`utils/task_segments.py` 在拼接时检查实际轨迹是否超出声明的读写范围 |
| 多个 Key 的信息、更新关系及前置状态 | 任务定义中各 Key 提供或更新的信息及 Q 的需求；配置中的 `segments.<片段 ID>.depends_on`；段清单中的读写集合及入口/出口状态 | 采集按依赖推进；`compose_episodes` 检查指定顺序及最终记忆状态，防止更新顺序错误或共享物体的 Key 被错误重排 |

例如，杯柜任务定义 A/B/C 三种目标绑定，采样后某实例记录目标为 B 柜；切段时检查 Key 已将杯子放入 B 柜、Query 起点仍保持这一状态，拼接时检查 I 没有使用或改变相关物体。这些信息分别服务于采样、录制检查和拼接检查。

配置与生成信息通过片段 ID 对应，例如 `key_1`、`key_2`、`query`；多个 Key 分别读取各自的定义和状态。共享物体的 Key 只有在依赖顺序与前置状态都满足时才能重排。

### 2.4 统一场景与 150 条实例准备

任务实例路径与环境变量沿用 2.1。先做少量端到端联调，再按 1.5 的数量构建正式实例。

准备流程：

1. 确定 I 的候选任务及其 2026 录制，检查共同场景、房间、机器人、控制器、频率、动作格式和任务资产。`prepare_intervent` 只检查元数据、物体隔离和资产兼容性，选择足量不同源录制，并在 `intervent_assignments.json` 中按实例编号固定分配，不启动仿真或解码轨迹。后续 Bridge 合成保留实际边界的衔接检查。
2. 构造全场景模板，固定房间布局、物体身份、模型和通道门状态。各 I 独立资产可保留源录制的初始位姿或状态，允许调整的对象明确写入资产清单。可以复用 `merge_scene_files`，调用前深拷贝；同名物体模型不同或共享前置状态冲突时报错。
3. 选择未被 I 使用的 K/Q 资产，或插入所需资产；保存源物体到统一场景物体 ID 的映射、BDDL 绑定和保护集合。
4. 保留任务场景的门状态，不在准备阶段统一开门；检查新增资产及所有任务操作区域的空间兼容性，后续 Bridge 按实际世界状态检查通路。
5. 编写或复用各 K/Q 的 BDDL，准备有效实例。沿用已有原始采样和变体分配，按固定 I 录制入口生成实例世界状态；机器人只保留已采样位置和朝向，关节姿态由遥操 reset 决定。变体、绑定及种子不在 TRO 中重复记录。Level 2 按前序 Key 的后置状态检查下一 Key 的前置条件，不能将多个 Key 的独立初态直接合并。
6. 验证实例的任务初态、绑定和机器人边界，保存模板、实例清单及输入路径。联调阶段正常加载代表性实例，检查稳定性、任务操作与完整导航路线；确认候选 I 有足够的兼容素材，再开始正式批量采集。

准备阶段生成一个公共 `scene_template.json`，每实例最终 TRO 保存相对模板变化的世界状态及机器人的已采样位置、朝向，不重复保存物体创建信息、变体或 I 分配，也不保存准备机器人的关节状态。采集在内存中补入当前遥操机器人并应用当前 reset 配置；拼接补入首段录制的完整机器人定义和状态。

不同 I 源录制可能有不同的物体初态。采集前根据每个实例已固定的 I 录制入口生成对应 TRO，并检查物理停稳后仍匹配。后处理和拼接沿用分配；拼接只检查初始布局，不再覆盖 I 物体初态。分配或布局不一致时停止，不能静默换源或改变 K/Q 的物体身份及记忆状态。

Q 继承所有 Key 按采集顺序执行后的世界状态。该实例所有 Key 与 Q 的机器人入口共用第一 Key 的初始位置、朝向、关节和控制器状态，不另采样后续入口。切换时只加载机器人的状态，不能加载整个场景或调用会重置物体的任务初始化；仍需检查当前世界中的入口条件及机器人碰撞。实际录制的入口状态作为后续 B 的目标。

各任务的记忆情况覆盖由任务采样规则决定，巧克力任务 的具体分配见 2.10。

### 2.5 连续世界中的独立子任务采集

完整加载实例场景，关闭 partial load，按 `collection.key_segment_ids` 完成所有 Key，再执行 Query。所有子任务在同一个仿真世界内进行，但各自写独立 HDF5：

1. 入口检查空手、稳定、无碰撞、公共机器人起点及任务前置条件。按右方向键或键盘 X，完成 GELLO 同步后为当前文件记录初态并开始。
2. 任务完成后按左方向键。检查后置条件、空手、稳定和无碰撞；失败时留在当前任务继续修正。
3. 检查通过后暂停，保存出口快照，写完当前子任务文件、刷新到磁盘并关闭，再原子更新尝试索引。保存失败保留内存轨迹与当前世界，操作员可按左方向键重试。
4. 若还有下一任务，只恢复机器人到第一 Key 的公共初态，检查恢复过程中非机器人世界未变、机器人停稳且无碰撞。此过程不写录制；然后等待右方向键单独开始。
5. 最后一个 Q 保存后，整轮尝试标为 `complete`，暂停等待 Home 加载下一已准备实例，或按 R 从头重录当前实例并新建下一编号的 demo，旧文件保留；最后一个实例完成后也保持暂停，按 Esc 退出。只有完整尝试能进入统一后处理。

每个子任务开始时建立入口检查点。JoyCon X 手动检查点、Y 选择后 X 执行回滚，以及配置启用的自动检查点均保留；回滚恢复当前任务内的世界，删去正式轨迹中的失败动作，并保存为该文件的 `rollbacks` 分支。跨任务时清空检查点，已关闭文件不参与后续回滚。暂停和回滚后允许握持物体继续，入口/出口仍要求空手。

`INSTANCE_ID` 只指定起始实例，无需逐实例重新启动进程。JoyCon Home 加载下一已准备实例，在同一父目录的 `instance_NNNN/demo_NNN/` 中建立新 run 和 `manifest.json`，随后暂停等待右方向键或键盘 X。若旧 run 未完成，丢弃当前段缓冲并标为 `aborted`，已保存文件保留；完成的 run 保持 `complete`。没有下一实例时只提示并保留当前状态。Home 每次按下只推进一次，并沿用冷却限制。`--session-recording-dir` 指定起始实例目录，后续实例使用其父目录下的标准实例目录。

Esc / Capture 丢弃未完成段并退出，将未完成 run 标为 `aborted`；中止时不生成未完成段的 HDF5 或出口快照，已完成并等待 Home 的 run 保持 `complete`。键盘 P 暂停。完整按键表见[中文遥操说明](memory_bench_teleoperation_zh.md)。

新采集不再生成 marks 文件。每轮保留 `manifest.json`：

```yaml
format: memory_bench.collection_run.v1
run_id: demo_001
session_id: <session_id>
instance_id: 3
variant_id: A
instance: sessions/<session_id>/tro/instance_0003.json
status: complete
segment_ids: [key_1, query]
segments:
  key_1:
    hdf5: recordings/<session_id>/instance_0003/demo_001/key_01.hdf5
    demo: 0
    frames: [0, 2570]
    states:
      directory: recordings/<session_id>/instance_0003/demo_001
      entry: key_01_entry.json
      exit: key_01_exit.json
    validation: {entry: true, exit: true, empty_hands: true, stable: true, collision_free: true}
  query: <同结构的 Query 文件记录>
transfers:
  - from_segment: key_1
    to_segment: query
    validation: {nonrobot_world_preserved: true, entry_pose: true}
```

各文件都是本地动作区间 `[0,N)`，原始 HDF5 保存 N 个动作及 N 个动作前状态，最后一个动作后的状态由出口 JSON 提供。恢复和等待不增加录制帧；无需从一个长 HDF5 或源视频中再切出子任务。`manifest.json` 保存完成状态和文件关联，防止不同轮次的 Key/Query 被误用，作用与旧切段标记不同。

恢复失败时整轮中止，已关闭的文件仍保留。采集不插入 I，最终 B 仍在统一世界中独立生成可执行的物理导航。

### 2.6 原始录制的公共读取接口

四步为准备实例、遥操采集、独立合成 Bridge、拼接回放导出。没有独立的规范化步骤，也不生成 normalized HDF5、`segments/`、`segment_index.json`、`material_groups.json` 或 `source_pairings.json`。

`utils/recordings.py` 的 `FrameReader` 读取原始 HDF5 的动作和状态。序列化状态按原录制场景结构即时解码，已有 `state_json` 直接读取。N 个动作需要 N 个动作前状态及真实动作后终态；源文件只有 N 个状态时使用采集或 Bridge 出口快照，不能复制最后一个动作前状态冒充终态。旧机器人/控制器字段只在内存中适配，不修改源文件。

第三步默认只读取每实例的完整 `demo_001`，缺失或无效时停止。需要其他轮时明确指定；不得跨轮选择子任务，也不遍历所有 demo 生成多套 B。每个子任务文件只包含 `data/demo_0`，Key 文件使用 `key_01.hdf5`、`key_02.hdf5`；逻辑 ID 保持配置里的 `key_1`、`key_2`。

第三步读取首尾边界，按任务声明的读写范围合成导航；第四步对真实逐帧变化做完整检查。越界修改或记忆破坏使该 episode 无效，不发布正式拼接结果。

**机器人接口：**

- 入口和出口要求机器人空手、姿态稳定；具体速度容差由机器人配置在联调后确定，不强制张开夹爪或追加固定时长的静止帧。
- 本流程采集的 K/Q 入口统一恢复该实例第一 Key 的完整机器人初态；出口只要求空手、稳定。I 保留其源录制的实际入口和出口。
- 最终相邻任务的实际入口/出口由 B 连接，B 负责收臂、导航、展臂并检查可执行性。
- 首个 demo 使用 R1Pro 的复位关节值作为停靠姿态，与 JoyLo 和 `omnigibson/eval/r1pro.yaml` 对齐，不把该姿态写入所有任务的通用接口。

**物体接口：**

- `U(s)`：任务使用集合，来自 BDDL 绑定、声明的容器/支撑物及实际交互。
- `R(s)`：前置状态依赖集合，记录任务读取或依赖的物体状态。
- `W(s)`：任务写集合，由任务声明并结合录制中的相关状态变化确认，包括位姿、关节及任务涉及的开关、温度等状态。
- 首版针对任务使用对象及保护对象提取状态差异，不建设通用的全场景交互推断系统；联调发现额外交互时补充声明或更换素材。
- 保存入口、出口状态及统一物体 ID 映射。仅靠位姿变化不能发现加热、开关等状态冲突。

`manifest.json` 关联独立采集文件和边界。公共片段描述函数补齐任务、来源、物体依赖和接口，结果内嵌到唯一 recipe，不单独写段清单。下面用 YAML 展示一个内嵌描述：

```yaml
identity:
  id: key_1
  instance_id: 3

task:
  role: key_task
  activity: mb_chocolate_into_top_cabinet
  room_id: kitchen_0
  instruction: "Put the chocolate bar from the breakfast table into a top cabinet and close both cabinets."
  depends_on: []

source:
  hdf5: recordings/mb_retrieve_chocolate_from_top_cabinet/instance_0003_recording_0001.hdf5
  demo: 0
  frames: [40, 2610]

states:
  directory: recordings/mb_retrieve_chocolate_from_top_cabinet/instance_0003_recording_0001
  entry: key_01_entry.json
  exit: key_01_exit.json

objects:
  used: [chocolate_bar_73, top_cabinet_tynnnw_0, top_cabinet_tynnnw_1, breakfast_table_xftrki_0]
  read: [chocolate_bar_73, top_cabinet_tynnnw_0, top_cabinet_tynnnw_1, breakfast_table_xftrki_0]
  write: [chocolate_bar_73, top_cabinet_tynnnw_1]
  memory_bindings:
    chocolate_bar.n.01_1: chocolate_bar_73
    cabinet.n.01_1: top_cabinet_tynnnw_1
```

| 分组 | 内容与用途 |
|---|---|
| `identity` | session 内的逻辑片段 ID 及实例编号，具体素材由清单路径定位 |
| `task` | 角色、活动、房间、指令及依赖，由 session 配置和任务定义补齐 |
| `source` | 原始 HDF5、文件内部的轨迹编号及动作帧区间，用于读取源数据 |
| `states` | 入口/出口完整状态文件，用于前置条件、状态继承和衔接检查 |
| `objects` | `used`、`read`、`write` 分别对应 U(s)、R(s)、W(s)；`memory_bindings` 保存记忆物体的身份对应 |

字段约定：

- `identity.id` 与 `task.depends_on` 始终使用 session 的逻辑片段 ID，不引入另一套全局 ID。recipe 按这些 ID 保存本组的片段描述。
- 项目内的 `source.hdf5` 与 `states.directory` 相对 `$MB_ROOT`，2026 外部源 HDF5 使用绝对路径；`states.entry`、`states.exit` 相对声明的状态目录。
- K/Q 与 B 描述直接引用录制阶段保存的边界状态；I 首尾通过公共读取器取得，不重复保存完整场景。
- K、I、Q 使用相同字段分组。I 保留其 2026 HDF5 来源和原指令；无 Key 依赖时 `task.depends_on` 为 `[]`，不承担记忆绑定时 `objects.memory_bindings` 为 `{}`。各组字段由公共片段描述函数生成。
- `objects` 中的对象均使用统一场景物体 ID；使用集合、读取依赖和实际写入范围分别保存。示例物体名是巧克力任务 的绑定候选，最终以冻结清单为准。

`source.frames` 使用半开动作区间 `[start, end)`，第 i 帧状态是执行动作 i 之前的状态。`states.entry` 对应 `state[start]`，`states.exit` 对应 `state[end]`；出口使用后继录制帧或额外保存的终态，不能把最后一帧动作之前的状态当成动作后的终态。源路径、demo、帧范围及 fps 随 `source` 保存，机器人型号、控制器配置及动作 schema 由源录制元数据提供，切段时检查完整并在匹配时校验兼容性。

### 2.7 指定顺序、状态继承与拼接

`build_episodes` 沿用固定原始素材、确定一个合法顺序并生成 B，recipe 内嵌全部来源和顺序。`compose_episodes` 只读取 recipe，不再生成导航、选择素材或写规范化副本。失败不自动更换顺序或源录制。

1. 检查顺序包含全部任务片段且不重复，以唯一 Q 结束，并满足配置中的依赖关系。
2. 根据 1.2、1.3 检查房间与资产隔离。核对每个 I 的片段引用与采集前分配一致，核对实例内对应物体已符合 I 的入口状态；不覆盖实例布局，不重新选择源录制。
3. 按顺序读取各段的入口/出口状态，对任务声明的读取和写入对象检查前置条件并继承后置状态。段间不加载源录制的完整场景覆盖当前世界；无关对象保持此前状态。
4. 对共享对象的 Key 按声明继承状态，检查 Q 的入口与采集时的任务相关状态兼容。I/B 不得改变保护对象；相关状态容差在联调后按任务和机器人配置确定，不使用一组通用固定数值。
5. 在当前世界为相邻任务生成 B，并检查衔接连续性。前序任务移动物体后的空间兼容性通过实际导航和代表性任务回放确认，首版不实现全轨迹扫掠体分析。
6. 保存 recipe、B HDF5、拼接 HDF5 和完整回放起点。所有有效组完成后导出，数量满足 1.5。

recipe 是每条 episode 唯一的完整配方，保存 session/实例、所选采集 demo、任务顺序和内嵌的 K/I/Q/B 描述。第三步发布 recipe；第四步补充最终帧区间、记忆跨度和输出引用。不再维护平行的素材索引。

第四步额外输出 `episode_XXXXXXXX_query_start.json`。它严格对应拼接后 Query 第一条动作前的完整累计世界，包含物体定义、所有物体状态、机器人关节与控制器、Query 绑定；recipe 保存快照路径和全局帧号，供评测直接恢复。整条序列起点另存为 `*_scene.json`。遥操时的 `query_entry.json` 没有后来执行的 I/B 结果，不能直接作为最终评测世界。

### 2.8 衔接导航合成

每段 B 在前一任务出口与后一任务入口之间包含三个阶段：

1. **收臂**：躯干和手臂从前一段出口插值到停靠姿态，底盘不动。
2. **导航**：按当前统一场景的可通行地图规划路线，考虑机器人尺寸、前序任务移物和门状态；先转向，再沿路径行驶，最后调整到目标朝向。
3. **展臂**：从停靠姿态插值到后一段入口的躯干和手臂姿态。

沿用 1.2 的通道门约束；没有可行路线就报告失败。

使用与遥操相同的机器人、控制器、频率和动作格式，在物理环境中闭环执行并录成 HDF5。首个 demo 对齐 R1Pro 的 23 维动作：底盘为机器人坐标系下的速度指令，躯干/手臂为关节目标，夹爪保持空闲。

速度上限、停靠姿态和终点误差容差在联调后写入机器人配置。B 必须避免碰撞、不改变非机器人物体，并到达下一段实际入口姿态；通过路线和控制调整满足这些要求，不通过冻结物体掩盖接触。

残差写入注释并检查拼接边界连续性；失败时允许调整规划参数重试，仍失败则拒绝配方。不能用直接改机器人位姿的插值代替可执行动作数据。

### 2.9 全场景 replay 与 VLA 输出

`render_episodes` 参照 `OmniGibson/scripts/learning/replay_obs.py`，在同一个完整场景中逐帧回放拼接 HDF5 并重新渲染，不拼接各源录制已有的视频。

1. 从 recipe 引用的完整回放起点启动并加载所有房间，起点包含选定 I 的初始物体状态和首段机器人入口。首段可以是 I；不能默认用 Key 录制的初始场景及机器人起点。
2. 统一源机器人别名、物体 ID 与序列化 schema。可复用 `data_wrapper.py` 中的状态 schema 处理函数，但不得在切段时覆盖前序任务已更新的物体状态。
3. 回放关闭 transition rules 和机器人控制，使用 visual-only 物体。逐帧只更新机器人和当前段的写集合，其他物体继承此前状态；B 仅更新机器人。
4. 每段首帧先写入状态、完成渲染更新，再保存合并后的完整场景快照和当前段绑定。该快照包含前序片段的状态，与源录制边界快照用途不同。
5. 将观测、对应动作与逐帧指令写入同一条 episode。B 使用下一任务的指令；K/Q 使用任务模块指令，I 沿用 2026 任务指令。
6. 输出完整 K/I/B/Q 帧，并用 `LeRobotDataset` 读回，检查总帧数、视频、动作/观测对齐及注释连续覆盖。

demo 对齐现有相机与本体感知配置：头部 720×720、左右手腕 480×480，RGB、深度及 R1Pro 本体感知。通用导出接口允许通过配置选择传感器与输出字段，不固定任务或相机数量。

输出目录遵循 2.1 的统一约定，最终训练数据写入 `$MB_ROOT/lerobot/<session_id>/`。

标准 LeRobot 文件由现有导出组件生成；新增注释包含难度等级、每段的 `role`、ID、来源、指令、`[start, end)` 帧区间、房间、绑定、依赖、写集合及起点快照。各任务保存 Key 提供的信息，记忆更新任务还保存更新关系；通过 recipe 引用追溯素材及构建配置。

记忆区间使用列表，而非只支持单 Key 的字段：

```yaml
memory_spans:
  - {key_id: key_1, query_id: query, key_end_frame: 2570, query_start_frame: 22000}
  - {key_id: key_2, query_id: query, key_end_frame: 5300, query_start_frame: 22000}
```

全部段区间首尾相接，总帧数等于各段长度之和。答案作为内部注释保留，不进入逐帧训练指令；全部输出 episode 均保留完整 Query 数据。

### 2.10 Level 1：吊柜里的巧克力 + 0 + 22

默认任务只有一个 Key，Key 后的巧克力位置由 I/B 保持。场景为 `house_double_floor_lower`，机器人为 R1Pro；K/Q 都在厨房，两个 I 分别在客厅和走廊，不需要跨楼层。

```text
Key → bridge → 0 → bridge → 22 → bridge → Query
0 → bridge → Key → bridge → 22 → bridge → Query
22 → bridge → Key → bridge → 0 → bridge → Query
```

| 片段 | 任务 | 房间与来源 |
|---|---|---|
| Key | 将餐桌上的巧克力放进两个吊柜中的指定一个，关好两个柜门 | 厨房，自采 |
| 0 | `turning_on_radio` | 客厅 `living_room_0`，2026 原始录制 |
| 22 | `putting_shoes_on_rack` | 走廊 `corridor_0`，2026 原始录制 |
| Query | 取回此前收好的巧克力，放到餐桌，关好两个柜门 | 与 Key 相同厨房，同次自采 |
| bridge ×3 | 按分配顺序连接相邻片段的出口与入口 | 在当前统一场景状态中合成 |

任务定义为 `tasks/retrieve_chocolate_from_top_cabinet.py`，配置为 `configs/mb_retrieve_chocolate_from_top_cabinet.yaml`。

| 资产 | 场景名或模型 | 作用 |
|---|---|---|
| 巧克力 | 新增 `chocolate_bar/dxnisi`，实际物体名由采样冻结 | K/Q 共享的记忆物体 |
| 两个吊柜 | `top_cabinet_tynnnw_0/1` | 变体 A/B 对应的位置 |
| 餐桌 | `breakfast_table_xftrki_0` | Key 起点、Query 终点 |

Key 的 BDDL 为 `mb_chocolate_into_top_cabinet`，Query 为 `mb_retrieve_chocolate_from_top_cabinet`。目标角色 `cabinet.n.01_1` 在 A/B 中分别绑定到两个实际柜子，K/Q 保持同一绑定。A 是远离客厅的柜子，B 是靠近客厅的柜子；位置说明只出现在 Key 指令和内部注释中。Query 指令固定为 `Retrieve the chocolate bar you stored earlier and place it on the breakfast table.`，不透露目标柜。

两种变体均衡分配，正式 150 个实例计划各 75 个；实际冻结数量以原始 TRO 为准。每个实例只执行一个变体，变体与拼接顺序独立分配。

任务检查直接评价实时物体状态：

- Key 入口：巧克力在餐桌上，两个柜门关闭。
- Key 出口与 Query 入口：巧克力在选定柜子中，两个柜门关闭。
- Query 出口：巧克力放回餐桌，两个柜门关闭。
- Query 过程：可以打开任一任务柜子，完成时需关好两个柜门。
- 各采集边界要求机器人空手、无碰撞、停稳，入口符合该实例的共用机器人初态。
- 两段 I 的使用集合互不重叠，且不使用或改变巧克力、两个吊柜和餐桌；Bridge 同样保持这些物体状态。

本地模板中的两个 I 起点直线距离约 5.35 米，不能据此宣称实际出口到入口导航已通过。巧克力抓取、关门后的视觉遮挡、录制边界与完整 Bridge 仍需真实仿真验收。

### 2.11 实施步骤与完成标准

| 阶段 | 实现内容 | 完成标准 |
|---|---|---|
| D0 公共接口 | 同步任务/准备入口，定义 session、段清单、依赖和顺序配置 | 单 Key、多 Key、Key 两侧插段均可表达，依赖引用和顺序检查可用 |
| D1 少量贯通与实例准备 | 检查 0/22，合并场景、隔离资产；先联调采集、匹配、B 和渲染，再冻结正式实例 | 路线与入口可执行，候选素材足够兼容，正式实例按 1.5 准备完成 |
| D2 子任务采集 | 实现 Key 列表推进、机器人恢复、准备确认、任务切换和边界记录 | 每个子任务独立保存；多 Key 切换保留世界状态，任务内回滚有效，边界准确 |
| D3 衔接与拼接 | 沿用已固定 I 素材、分配顺序，生成 B、recipe、拼接 HDF5 和回放起点 | 素材索引完整，每组指定顺序通过状态与衔接检查 |
| D4 重渲染与导出 | 全场景 replay、LeRobot 输出、注释及读回 | 正式数量满足 1.5，帧数、视频、指令与动作对齐，人工抽查边界 |
| D5 Level 2 任务 | 保留杯子位置更新任务，沿用 D1—D4 | 明确每个 Key 的信息与入口条件，Q 确实需要多个片段，数据完整可读回 |

D1 与 D2—D4 的最小实现交叉联调，用少量素材验证全流程，再开展批量构建。配置冻结后开展正式采集；模板变化时检查已有素材是否仍兼容。正常物理加载与稳定性检查用于联调及代表性快照抽查，不逐个重复加载所有边界；visual-only replay 用于生成观测，物理可执行性由采集和 B 执行确认。

当前 Level 2 保留杯子位置更新任务；信息综合作为后续扩展方向。两种记忆依赖的区别如下：

- **记忆更新任务**：Key₁ 让机器人获知多只杯子中的目标，Key₂ 移动多只杯子并更新位置，Q 要取回此前指定的目标。Key₂ 不复述目标身份，Q 需要结合 Key₁ 的目标信息与 Key₂ 的位置更新；只看到 Key₂ 不能确定取哪只杯子。
- **信息综合任务**：Key₁ 提供杯子位置，Key₂ 提供盘子位置，Q 要取回两者；两个位置分别变化，Q 的指令保持一致，需用到两段历史信息。

当前注册的 Level 2 任务为 `mb_retrieve_target_mug_after_relocation`，包含目标身份和位置更新两个必要 Key。具体物体、指令与变体由任务定义冻结；信息综合任务未注册，后续需要时再实现。

项目代码优先放在 `memory_bench/`；JoyLo 切换键和录制事件采用小范围改动，复用现有控制与记录机制。

后续扩展通过配置增加 Key、I、记忆任务类型、场景和源录制，沿用同一套准备、段接口、依赖校验及 replay 流程。每个序列仍只有一个 Q；动态物体追踪等任务通过明确的共享物体状态依赖接入。
