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

静态世界记忆、动态记忆更新和多条信息综合等是具体任务类型，可以在相应等级下设计不同任务。当前 Level 1 的杯柜 demo 属于静态世界记忆；Level 2 先分别设计记忆更新任务和信息综合任务，例如后续 Key 更新物体位置，或 Q 综合不同 Key 提供的多个目标位置，后续也可增加其他需要多个记忆片段的任务。

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

导航优先选择不需要开门的路线。必须经过房间门时，在准备 session 初始场景时预先打开对应的门，并检查前序任务不会将它关闭。通道门与任务中的柜门分别管理；预开通道门不改变 K/Q 对柜门关闭的要求。B 本身只负责机器人运动，不承担开门操作。

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
3. **编写 BDDL 并采样。** 为 K/Q 编写 BDDL，先用少量实例贯通采集、素材匹配、导航与渲染，确认可行后冻结配置并采样正式实例；保存完整初态、变体绑定及多样化的 Q 机器人起点。正式数据数量统一见 1.5。
4. **联合遥操。** 在同一次录制中完成所有 K 和 Q。单 Key 流程完成 Key 后切换到 Q；多 Key 流程按采集顺序完成各 Key，每次完成后按切换键进入下一个 Key，最后进入 Q。切换时自动导航到下一片段入口，到达并准备好后恢复遥操，全程继承世界状态。输出一条 HDF5，分别标注各 Key、转场和 Query 的边界；这些采集转场区间在最终拼接时丢弃。
5. **选择拼接素材与生成 B。** 在拼接阶段挑选 I 源录制并建立素材对应，为每组素材指定一种合法顺序，在对应的统一世界状态下合成衔接导航，分别输出 HDF5。
6. **拼接与重渲染。** 按指定顺序拼接 K、I、Q、B，在统一完整场景中 replay 并重新渲染，输出完整 VLA 训练数据及片段注释。

多 K 时，联合录制与切段使用有序 Key 列表；各 Key 分别保留边界和依赖。Key 的采集顺序保证状态依赖成立，完整序列的 I 插入位置在拼接时指定。联合录制中的转场用于取得下一 Key 或 Q 的入口，不直接代替最终序列经过 I 的衔接路线。

### 1.5 数据与记忆有效性

- 采样 150 条有效场景实例，每个实例录制一条有效的联合 K/Q 轨迹，共 150 条 K/Q HDF5。录制中分别标注各 Key 片段和 Query 片段的边界。
- 拼接时，对每个选定的 I，从其 2026 的 300 条录制中挑选 150 条通过兼容性检查的录制，分别与 150 条 K/Q 录制一一匹配，形成 150 组拼接素材；源录制 ID 和对应关系写入清单。
- 完整任务序列的顺序在拼接阶段指定，提供不多于三种合法顺序。例如可采用 K→I₁→I₂→Q、I₁→K→I₂→Q、I₂→K→I₁→Q；每组素材选择其中一种，检查状态依赖并生成对应 B。
- 场景实例、K/Q 源录制、I 源录制和输出 episode 分别编号。150 组素材各生成一条完整 episode，所有顺序合计 150 条；不同顺序的数量不要求均匀，记录每条 episode 的实际顺序即可。
- 同一 Key 任务必须设计多种记忆情况，并在不同实例中覆盖；每个实例执行其中一种。同一 K/Q 模板的 Q 指令保持一致，正确执行目标随 Key 的实际情况变化，要求模型利用 Key 的历史观测确定目标。Key 情况与拼接顺序独立分配，Q 的起始观测不应直接泄露记忆答案。
- 按 Q 实际需要的 Key 信息检查记忆依赖：Level 1 需要一个记忆片段，Level 2 需要两个或更多记忆片段。各 Key 提供的信息、必要的更新关系和最终有效信息按任务定义保存；任务设计应避免最后一个 Key 直接复述 Q 所需的全部信息，使前面的 Key 失去作用。I/B 不得改变承担记忆依赖的物体；静态记忆任务另外检查相关记忆状态保持不变。
- Q 起点的位置和朝向应在可达范围内保持多样性，并与记忆答案独立采样，避免起点本身成为答案线索。
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
| 2026 原始录制 | `<intervent_rawdata_root>/task-XXXX/episode_XXXXXXXX.hdf5` | 每个选定 I 的 300 条候选录制，拼接时选取 150 条 |
| K/Q 的 BDDL | `<仓库根目录>/bddl3/bddl/activity_definitions/<activity>/problem0.bddl` | 定义初态、目标和任务物体 |
| session 配置 | `<仓库根目录>/memory_bench/configs/<session_id>.yaml` | 指定场景、任务、资产规则、150 条实例及不多于三种顺序 |

官方任务实例与 2026 原始录制只读。选取 I 时保存源路径、demo 编号、帧区间及哈希，通过清单引用源录制。

**输出目录**

采样器使用的模板、TRO 和任务元数据写入 `$OMNIGIBSON_DATA_PATH/memory-bench-task-instances/`，并设置 `OMNIGIBSON_TASK_INSTANCES_DATASET=memory-bench-task-instances`。该目录遵循 OmniGibson 的任务实例组织；下面的 `$MB_ROOT/instances/` 保存完整场景快照、Q 起点及对 TRO 的引用。

```text
$MB_ROOT/
├── sessions/<session_id>/
│   ├── session.json                         # 本次构建的配置快照与解析后的路径
│   ├── assets.json                          # 资产来源、统一物体 ID 及保护集合
│   ├── instances.json                       # 150 条实例及各 Key 的情况、绑定
│   └── source_pairings.json                 # 实例、episode 与 recipe 的索引
├── instances/<session_id>/instance_XXXX/     # 完整初态、绑定、Q 起点、TRO 引用
├── recordings/<session_id>/instance_XXXX_recording_0001.hdf5  # 每个实例一条有效 K/Q 录制
├── recordings/<session_id>/instance_XXXX_recording_0001/
│   ├── marks.yaml                           # 各 Key、转场、Query 的切分标注
│   ├── key_<n>_entry.json                    # 各 Key 入口的完整场景及机器人状态
│   ├── key_<n>_exit.json                     # 各 Key 出口的完整场景及机器人状态
│   ├── query_entry.json                     # Q 入口完整状态，包含实际机器人位姿
│   └── query_exit.json                      # Q 出口完整状态
├── segments/<session_id>/...                 # K/I/Q 段清单，引用已保存的边界状态
├── bridges/<session_id>/<episode_id>/bridge_XX.hdf5
├── recipes/<session_id>/<episode_id>.json    # 片段顺序、来源及衔接的完整配方
├── episodes/<session_id>/<episode_id>.hdf5   # 拼接后的统一状态与动作
├── episodes/<session_id>/<episode_id>_scene.json  # 合并 I 初态后的完整回放起点
├── reports/<session_id>/...                  # 采样、兼容性、导航及导出检查报告
└── lerobot/<session_id>/                     # 所有顺序合计 150 条完整 episode
    ├── meta/
    ├── data/
    ├── videos/
    ├── annotations/<episode_id>.json
    └── segment_states/<episode_id>/seg_XX.json
```

**命名、引用与阶段边界**

- `session_id` 暂直接使用唯一 Query task 的 BDDL 活动名，与 Query 片段的 `activity` 字段一致；配置文件名和各产物目录统一使用该名称。实例文件夹按 `instance_0001` 至 `instance_0150` 命名，每个实例保留一条有效录制。录制文件名和标注目录名使用 `instance_XXXX_recording_0001`，例如 `instance_0003_recording_0001.hdf5` 及同名标注目录。
- 同一 `output_root` 下，一个 Query 活动名对应一份 session 配置。需要保留同一 Query 的不同配置时，使用不同输出根目录；已有产物不直接覆盖。
- 输出 episode 按 `episode_00000000` 至 `episode_00000149` 编号，通过 recipe 和匹配索引关联原实例、各源录制及分配的顺序。
- 片段 ID 使用完整类型名称及 session 内部序号，如 `key_1`、`intervent_1`、`intervent_2`、`query`、`bridge_00`；I 的原任务编号由 `source.task_id` 指定。`role` 统一为 `key_task`、`intervent_task`、`query_task`、`bridge_task`。每个 Key 使用独立片段 ID，记忆情况另存 `case_id` 或变体字段；片段角色、记忆情况与排序分别记录。
- 配置及清单中，本项目产物目录和源文件路径相对 `$MB_ROOT`；目录内的文件名相对明确声明的目录，例如 `marks.yaml` 中的 `recording.state_dir`。外部输入使用绝对路径；运行时按这些约定统一解析，禁止依赖某个进程的当前目录。
- 源录制、切分标注、配方和最终训练数据分别保留。构建失败写入 `reports/`，成功产物可沿来源清单追溯。

| 阶段 | 主要输入 | 主要输出 |
|---|---|---|
| 场景与实例准备 | session 配置、BDDL、场景资产、官方任务元数据 | 任务实例数据集、150 条完整初态与 Q 起点、资产和实例清单 |
| 联合遥操 | 实例完整初态、K/Q 定义、Key 采集顺序、Q 起点 | 150 条 K/Q HDF5、marks、各 Key 入口/出口及 Q 入口状态与位姿 |
| 切段 | K/Q HDF5、marks、I 候选录制 | 统一段清单、任务相关读写集合、边界状态引用及检查结果 |
| 选取与拼接 | K/I/Q 段、候选顺序、资产及状态约束 | 150 组素材对应、每组一种顺序、B HDF5、recipe、拼接 HDF5 和回放起点 |
| 全场景 replay | 拼接 HDF5、回放起点、recipe、传感器配置 | 合计 150 条 LeRobot episode、片段注释、关键快照与读回报告 |

### 2.2 范围与模块组织

先提供覆盖 Level 1、Level 2 的公共接口，再用 Level 1 的“吊柜里的杯子”实现第一个 demo。采样、采集、编排和渲染模块不能写死杯子、厨房、任务 18/19、一个 Key 或 Key 位于序列开头。

任务模块按前两个等级组织；公共采集入口按有序 Key 列表推进，单 Key 是列表长度为一的情况，切段、导航、拼接和渲染共用：

| 等级 | 任务模块提供的信息 | 采集及处理差异 |
|---|---|---|
| Level 1：单片段记忆依赖 | Q 所需的一个 Key 记忆片段、Q 指令、记忆情况和任务条件检查 | 按 Key 列表采集；单 Key 流程完成 Key 后切换至 Q，检查实际记忆依赖及任务状态 |
| Level 2：多片段记忆依赖 | Q 所需的多个 Key 记忆片段、各片段的信息、必要的更新关系和依赖 | 遥操按 Key 列表逐段推进；检查每段前置/后置状态，记录各自入口、出口及转场，再进入 Q |

```text
memory_bench/
├── docs/benchmark_design.md
├── configs/                    # 等级、session、拼接顺序、源录制对应、采集与导出配置
├── scripts/                    # 环境与硬件启动脚本
├── collection/
│   ├── prepare_task.py         # 已有，待同步目录调整并扩展 session 准备
│   └── collect_session.py      # 待实现：单 Key / 多 Key 联合录制、逐段切换及边界事件
├── processing/                 # 待实现
│   ├── make_segments.py        # 切段、任务相关状态提取、段接口检查
│   ├── compose_episodes.py     # 排序校验、世界状态继承、拼接
│   ├── synthesize_bridges.py   # 通用衔接导航及 HDF5 录制
│   └── render_episodes.py      # 全场景 replay、VLA 导出与读回
├── tasks/
│   ├── base_task.py            # 已有公共任务定义，待扩展并统一字段
│   ├── level_1/                # Q 依赖一个 Key 记忆片段的任务
│   │   └── retrieve_mug_from_top_cabinet.py  # 已有任务，待移入
│   └── level_2/                # Q 依赖多个 Key 记忆片段的任务
└── utils/paths.py              # 已有数据集路径
```

现有 `MemoryTask` 已表达 Key 列表和单个 Query 的方向，但具体任务仍传入单个 Key 字符串，准备代码的导入和任务发现仍引用旧目录，尚不能作为通用入口直接运行。实现时先同步这些接口，再加入 session 编排；旧 `distractor_activity` 字段统一到间隔任务语义。

公共模块处理场景、片段和依赖；任务模块以 Query 的 BDDL 活动名注册，提供该 session 的 BDDL 活动、资产声明、记忆绑定、采样约束、指令和段条件检查。各片段通过 `activity` 指定自己的 BDDL 活动。等级目录按 Q 依赖的记忆片段数量组织，各目录内可以增加不同记忆任务类型。当前计划的 Level 2 记忆更新、信息综合需求分别实现为不同任务，共用多 Key 采集流程。首个 demo 只负责注册一份具体配置；上述等级目录是规划，当前代码文件暂不迁移。

### 2.3 通用配置与任务接口

session 用片段 ID 表示任务，每份拼接配方的 `task_order` 只列 K/I/Q；编排器在相邻片段间生成 B。采集只记录 K/Q，完整顺序在拼接时指定。下面是首个 demo 的规划配置，房间 ID 和资产绑定在准备阶段冻结，I 源录制在拼接阶段选定。I 选取数量及完整 episode 数量由 `instance_sampling.count` 得到，其余固定规则沿用 1.5。

```yaml
session_id: mb_retrieve_mug_from_top_cabinet
benchmark:
  level: 1
scene_model: house_single_floor
instance_sampling:
  count: 150
  seed: 0
intervent_selection:
  candidate_count_per_task: 300
memory_room: kitchen_0
assets:
  manifest: sessions/mb_retrieve_mug_from_top_cabinet/assets.json  # 每项注明 source: scene 或 insert
segments:
  key_1:
    role: key_task
    activity: mb_mug_into_top_cabinet
    room_id: kitchen_0
    depends_on: []
  intervent_1:
    role: intervent_task
    activity: tidying_bedroom
    room_id: bedroom_0
    source: {task_id: 18}
    depends_on: []
  intervent_2:
    role: intervent_task
    activity: outfit_a_basic_toolbox
    room_id: utility_room_0
    source: {task_id: 19}
    depends_on: []
  query:
    role: query_task
    activity: mb_retrieve_mug_from_top_cabinet
    room_id: kitchen_0
    depends_on: [key_1]
composition:
  task_orders:
    - [key_1, intervent_1, intervent_2, query]
    - [intervent_1, key_1, intervent_2, query]
    - [intervent_2, key_1, intervent_1, query]
query_start:
  sampler: room_free_space
collection:
  key_segment_ids: [key_1]
  advance_segment_key: N          # 键盘字母 N：完成 Key 或确认下一片段入口已准备好
```

`segments` 以片段 ID 为键，每个键对应一份片段定义，无需在定义内部重复写 `id`。片段的执行顺序由 `task_orders` 或具体配方的 `task_order` 指定，映射中的书写顺序不作为执行顺序。`depends_on` 始终是片段 ID 列表，Q 可以依赖多个 K。增加 Key 时，扩展 `segments`、采集 Key 列表及 Q 的依赖，不新增一套专用流程。以下顺序同样应由公共编排器处理：

```yaml
# 单 Key，前后均有 I
task_order: [intervent_1, key_1, intervent_2, query]

---
# 多 Key；segments.query.depends_on 为 [key_1, key_2]
task_order: [intervent_1, key_1, key_2, intervent_2, query]
```

`benchmark.level` 按 Q 实际依赖的 Key 记忆片段数记录难度等级：Level 1 为一个，Level 2 为两个或更多。`segments.query.depends_on` 声明完整的记忆依赖 Key 集合，检查时结合任务定义确认各片段的信息确实被 Q 使用。等级不直接规定拼接顺序。`collection.key_segment_ids` 是实际遥操的有序 Key 列表；Level 2 的配置例如 `[key_1, key_2]`，切换键逐次推进，最后进入 Q。

`depends_on` 按角色表达依赖：Q 列出必须回忆的 Key；Key 列出建立其前置状态的任务。例如 `key_2.depends_on: [key_1]` 要求采集与拼接都保持这一先后关系；互相独立的信息综合任务可不声明 Key 之间的依赖。编排器检查引用和顺序，任务模块检查前置/后置条件及最终答案；各 Key 的信息是否不可缺少由任务设计确认，依赖列表本身不能证明记忆有效。

下面是 Level 2 记忆更新任务的配置片段，活动名、资产和间隔任务由具体任务补充；信息综合任务若两个 Key 无先后依赖，可以将 `key_2.depends_on` 设为空列表：

```yaml
benchmark:
  level: 2
collection:
  key_segment_ids: [key_1, key_2]
  advance_segment_key: N
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

以下是实现规划。当前 `tasks/base_task.py` 和杯柜任务文件已定义活动名、Query 指令、模型白名单、房间类型和 A/B/C 绑定；通用配置加载、保护集合、Q 起点采样及段检查尚未接通。

| 所需信息 | 定义或保存位置 | 哪一步使用、做什么 |
|---|---|---|
| 每个片段的 BDDL 活动与指令 | 配置中的 `segments.<片段 ID>.activity`；任务定义中的片段指令，I 指令从源录制取得 | 实例准备时加载对应 BDDL；遥操和导出时使用该片段指令 |
| 物体模型、房间及任务使用对象 | 任务定义中的 `whitelist`、`room_types` 和 BDDL 物体声明；准备后写入 `assets.json`，切段后保存段清单的 `objects.used` | 准备场景与采样物体；选取 I 时检查任务资产不共用 |
| K/Q 使用的是哪些实际物体、哪些物体需要保护 | 任务定义的 `variants`；实例保存 BDDL 到场景物体的绑定，资产清单保存保护集合；段清单保存 `objects.memory_bindings` | 确保 Q 操作 K 中的同一物体；拼接及 B 合成时检查这些对象未被无关片段改变 |
| 初始状态、多种 Key 情况和 Q 机器人起点 | BDDL 初始条件、任务定义的变体、配置中的 `query_start`；采样结果保存到实例目录 | 生成 150 条实例；遥操切换键读取该实例的 Q 起点并导航过去 |
| Key 结束及 Query 开始/结束时是否满足任务要求 | `tasks/` 中的任务检查函数（待实现）；读取 BDDL 条件和录制状态 | `make_segments` 切段时检查录制有效性，例如物体已放好、入口状态正确、未发生禁止操作 |
| 多个 Key 的信息、更新关系及前置状态 | 任务定义中各 Key 提供或更新的信息及 Q 的需求；配置中的 `segments.<片段 ID>.depends_on`；段清单中的读写集合及入口/出口状态 | 采集按依赖推进；`compose_episodes` 检查指定顺序及最终记忆状态，防止更新顺序错误或共享物体的 Key 被错误重排 |

例如，杯柜任务定义 A/B/C 三种目标绑定，采样后某实例记录目标为 B 柜；切段时检查 Key 已将杯子放入 B 柜、Query 起点仍保持这一状态，拼接时检查 I 没有使用或改变相关物体。这些信息分别服务于采样、录制检查和拼接检查。

配置与生成信息通过片段 ID 对应，例如 `key_1`、`key_2`、`query`；多个 Key 分别读取各自的定义和状态。共享物体的 Key 只有在依赖顺序与前置状态都满足时才能重排。

### 2.4 统一场景与 150 条实例准备

任务实例路径与环境变量沿用 2.1。先做少量端到端联调，再按 1.5 的数量构建正式实例。

准备流程：

1. 确定 I 的候选任务及其 2026 的 300 条录制，检查共同场景、房间、机器人、控制器、频率、动作格式和任务资产；首个 demo 优先选择无粒子系统、无运行时物体增删且出口空手的录制。具体 150 条源录制在拼接阶段选取。
2. 构造全场景模板，固定房间布局、物体身份、模型和通道门状态。各 I 独立资产可保留源录制的初始位姿或状态，允许调整的对象明确写入资产清单。可以复用 `merge_scene_files`，调用前深拷贝；同名物体模型不同或共享前置状态冲突时报错。
3. 选择未被 I 使用的 K/Q 资产，或插入所需资产；保存源物体到统一场景物体 ID 的映射、BDDL 绑定和保护集合。
4. 设置通道门初态，确认预开的门不在任务写集合中；更新可通行区域，检查新增资产及所有任务操作区域的空间兼容性。
5. 编写或复用各 K/Q 的 BDDL，采样 150 条有效实例。每条保存完整初态、各 Key 的情况及绑定、Key 采集顺序、Q 起点和随机种子。Level 2 按前序 Key 的后置状态检查下一 Key 的前置条件，不能将多个 Key 的独立初态直接合并；不合格实例记录原因并补采。
6. 验证实例的任务初态、绑定和入口可达性，冻结模板、实例清单及文件哈希。联调阶段正常加载代表性实例，检查稳定性、任务操作与完整导航路线；确认候选 I 有足够的兼容素材，再开始正式批量采集。

TRO 文件只保存任务相关物体状态，不能单独代表整条序列的世界。每个实例另保留完整初始场景快照或可确定重建它的模板与额外状态清单，供任意首片段启动和统一 replay 使用。

不同 I 源录制可能有不同的物体初态。拼接时按匹配清单补齐各 I 独立资产的初始状态，构造该组素材的统一初始世界，并重新检查与 K/Q 的前置状态和空间兼容性；不能覆盖 K/Q 的物体身份或记忆状态。

Q 不独立采样另一套物体：它继承所有 Key 按采集顺序执行后的世界状态，单独采样的只是机器人起点。起点按位置区域与朝向分层覆盖，并从与答案无关的候选集合中抽样；实际采集后的 Q 入口位姿才是后续 B 的目标。多 Key 的后续入口也必须基于当前世界检查，切换时只移动机器人，不能通过加载该 Key 的独立初始快照来重置物体。

各任务的记忆情况覆盖由任务采样规则决定，杯柜 demo 的具体分配见 2.10。

### 2.5 联合遥操、自动导航与切分标注

加载实例的完整场景，关闭 partial load，在同一次录制中按 `collection.key_segment_ids` 完成所有 Key，再执行 Query。单 Key 与多 Key 共用以下循环：

1. 确认机器人空手、姿态稳定，绑定当前片段的 BDDL 对象，启用对应指令和目标检查。操作员准备好并同步遥操控制目标后，记录入口帧并开始遥操。
2. 当前 Key 完成后按键盘字母 N。目标和机器人出口条件通过时记录出口帧和状态，进入自动转场；检查不通过则留在当前片段。转场期间由导航模块控制机器人。
3. 若还有 Key，导航到下一 Key 的可操作入口；否则导航到该实例预先采样的 Q 起点。下一 Key 的入口候选由任务定义提供，并基于当前世界确认可操作性；具体位置随 Level 2 任务设计确定，不新增通用起点采样器。
4. 到达后停止自动运动并等待准备。用同一字母键 N 确认继续：在等待状态下按键，完成控制目标同步，激活下一片段指令与目标检查，再记录实际入口帧。按键按一次完整按下/释放触发，避免完成 Key 的同一次按键误触发下一段。
5. 重复循环直至进入 Q；Q 完成后记录出口边界及终态，结束并保存录制。

切换只移动机器人，并在当前世界重新绑定任务对象与检查条件，不调用会重置世界的任务初始化。采集不插入 I；最终顺序及 I 的位置留到拼接时确定。

自动导航不能自行打开房间门；失败时记录原因并保持在切换阶段，不开始下一个 Key 或 Query。自动导航模块与 B 合成共用路径规划和机器人接口。

切分标注保存为 `marks.yaml`，统一分为三部分：`recording` 标识源录制及状态文件目录，`segments` 按片段 ID 保存 K/Q 的帧区间和状态引用，`transfers` 保存采集转场。Key 与 Query 使用相同的片段结构；角色、活动名和指令从 session 配置取得。以下是单 Key 示例：

```yaml
# recordings/<session_id>/instance_0003_recording_0001/marks.yaml
recording:
  hdf5: recordings/<session_id>/instance_0003_recording_0001.hdf5
  demo: 0
  instance_id: 3
  state_dir: recordings/<session_id>/instance_0003_recording_0001

segments:
  key_1:
    frames: [40, 2610]
    entry_state: key_1_entry.json
    exit_state: key_1_exit.json
  query:
    frames: [2920, 4440]
    entry_state: query_entry.json
    exit_state: query_exit.json

transfers:
  transfer_1:
    from_segment: key_1
    to_segment: query
    navigation_frames: [2610, 2900]
```

字段约定：

- 所有帧编号对应 `recording.hdf5` 中指定的 `demo`，区间均采用 `[start, end)`。状态文件名相对 `recording.state_dir`；该目录及 HDF5 路径相对 `$MB_ROOT`。
- `frames` 的开始帧是该片段已准备好执行的入口，结束帧是任务完成并满足出口条件的边界。Query 入口帧统一读取 `segments.query.frames[0]`，入口场景和实际机器人位姿均从该片段的 `entry_state` 取得。
- 转场的完整区间由前一片段的结束帧和后一片段的开始帧确定；`navigation_frames` 单独记录自动导航区间，其后的准备等待也属于转场。示例中转场为 `[2610, 2920)`，自动导航为 `[2610, 2900)`，准备等待为 `[2900, 2920)`。

入口帧必须对应机器人已到达、操作员准备好并完成控制目标同步的时刻；不能用按键时刻或尚未停车的到达帧代替。采集程序自动写入标注，回放时允许人工确认或修正；修改边界后重新生成对应状态引用并执行段检查。

多 K 时，在 `segments` 中增加 `key_2` 等片段定义，`transfers` 用 `transfer_1`、`transfer_2` 等 ID 分别引用 Key→Key、最后一个 Key→Q。各 Key 的入口/出口快照按片段 ID 保存。凡最终拼接中丢弃的录制区间，其非机器人物体状态必须保持不变；若某个移动改变了记忆物体，应将它纳入对应 Key，而不能作为无效间隔删掉。

### 2.6 统一片段接口与切段

`make_segments` 为所有 K、I、Q 建立相同格式的段清单。原始 HDF5 保留不改，清单记录来源和帧区间，避免先复制视频再拼接。

**机器人接口：**

- 入口和出口要求机器人空手、姿态稳定；具体速度容差由机器人配置在联调后确定，不强制张开夹爪或追加固定时长的静止帧。
- 手臂与躯干不要求复位；需要调整时由 B 收臂/展臂，并检查调整过程可执行。
- 底盘入口/出口位置不统一，由 B 连接。
- 首个 demo 使用 R1Pro 的复位关节值作为停靠姿态，与 JoyLo 和 `omnigibson/eval/r1pro.yaml` 对齐，不把该姿态写入所有任务的通用接口。

**物体接口：**

- `U(s)`：任务使用集合，来自 BDDL 绑定、声明的容器/支撑物及实际交互。
- `R(s)`：前置状态依赖集合，记录任务读取或依赖的物体状态。
- `W(s)`：任务写集合，由任务声明并结合录制中的相关状态变化确认，包括位姿、关节及任务涉及的开关、温度等状态。
- 首版针对任务使用对象及保护对象提取状态差异，不建设通用的全场景交互推断系统；联调发现额外交互时补充声明或更换素材。
- 保存入口、出口状态及统一物体 ID 映射。仅靠位姿变化不能发现加热、开关等状态冲突。

`marks.yaml` 记录采集边界，段清单由 `make_segments` 生成，补齐拼接所需的任务、来源、状态和物体依赖。每份清单描述一个具体片段，以 JSON 保存；下面用 YAML 展示统一结构：

```yaml
identity:
  id: key_1
  instance_id: 3

task:
  role: key_task
  activity: mb_mug_into_top_cabinet
  room_id: kitchen_0
  instruction: "Put the mug from the kitchen island into a top cabinet and close the door."
  depends_on: []

source:
  hdf5: recordings/mb_retrieve_mug_from_top_cabinet/instance_0003_recording_0001.hdf5
  demo: 0
  frames: [40, 2610]

states:
  directory: recordings/mb_retrieve_mug_from_top_cabinet/instance_0003_recording_0001
  entry: key_1_entry.json
  exit: key_1_exit.json

objects:
  used: [mug_ntgftr_0, top_cabinet_lkxmne_0, top_cabinet_lkxmne_1,
         top_cabinet_lkxmne_2, bar_udatjt_0]
  read: [mug_ntgftr_0, top_cabinet_lkxmne_0, top_cabinet_lkxmne_1,
         top_cabinet_lkxmne_2, bar_udatjt_0]
  write: [mug_ntgftr_0, top_cabinet_lkxmne_1]
  memory_bindings:
    mug.n.04_1: mug_ntgftr_0
    cabinet.n.01_1: top_cabinet_lkxmne_1
```

| 分组 | 内容与用途 |
|---|---|
| `identity` | session 内的逻辑片段 ID 及实例编号，具体素材由清单路径定位 |
| `task` | 角色、活动、房间、指令及依赖，由 session 配置和任务定义补齐 |
| `source` | 原始 HDF5、文件内部的轨迹编号及动作帧区间，用于读取源数据 |
| `states` | 入口/出口完整状态文件，用于前置条件、状态继承和衔接检查 |
| `objects` | `used`、`read`、`write` 分别对应 U(s)、R(s)、W(s)；`memory_bindings` 保存记忆物体的身份对应 |

字段约定：

- `identity.id` 与 `task.depends_on` 始终使用 session 的逻辑片段 ID，不引入另一套全局 ID。recipe 按这些 ID 引用本组的段清单路径。
- 项目内的 `source.hdf5` 与 `states.directory` 相对 `$MB_ROOT`，2026 外部源 HDF5 使用绝对路径；`states.entry`、`states.exit` 相对声明的状态目录。
- K/Q 清单直接引用录制阶段保存的边界状态，不向 `segments/` 重复复制；I 的边界状态在切段时生成并保存到其段目录。
- K、I、Q 使用相同字段分组。I 保留其 2026 HDF5 来源和原指令；无 Key 依赖时 `task.depends_on` 为 `[]`，不承担记忆绑定时 `objects.memory_bindings` 为 `{}`。各组字段由切段程序统一生成。
- `objects` 中的对象均使用统一场景物体 ID；使用集合、读取依赖和实际写入范围分别保存。示例物体名是杯柜 demo 的绑定候选，最终以冻结清单为准。

`source.frames` 使用半开动作区间 `[start, end)`，第 i 帧状态是执行动作 i 之前的状态。`states.entry` 对应 `state[start]`，`states.exit` 对应 `state[end]`；出口使用后继录制帧或额外保存的终态，不能把最后一帧动作之前的状态当成动作后的终态。源文件哈希和 fps 随 `source` 保存，机器人型号、控制器配置及动作 schema 由源录制元数据提供，切段时检查完整并在匹配时校验兼容性。

### 2.7 指定顺序、状态继承与拼接

`compose_episodes` 接收 K/Q 段清单、I 候选录制及候选顺序，按 1.5 选取并匹配素材。每组素材指定一个 `task_order`；首版不自动改换顺序，也不复用 B 缓存。失败时记录原因，人工调整素材或指定顺序后重试，不静默丢弃实例。

1. 检查顺序包含全部任务片段且不重复，以唯一 Q 结束，并满足配置中的依赖关系。
2. 根据 1.2、1.3 检查房间与资产隔离。将选定 I 的独立资产初态补入实例，形成该组素材的完整初始世界；只调整清单允许的对象，不覆盖冻结结构和 K/Q 状态。
3. 按顺序读取各段的入口/出口状态，对任务声明的读取和写入对象检查前置条件并继承后置状态。段间不加载源录制的完整场景覆盖当前世界；无关对象保持此前状态。
4. 对共享对象的 Key 按声明继承状态，检查 Q 的入口与采集时的任务相关状态兼容。I/B 不得改变保护对象；相关状态容差在联调后按任务和机器人配置确定，不使用一组通用固定数值。
5. 在当前世界为相邻任务生成 B，并检查衔接连续性。前序任务移动物体后的空间兼容性通过实际导航和代表性任务回放确认，首版不实现全轨迹扫掠体分析。
6. 保存 recipe、B HDF5、拼接 HDF5 和完整回放起点。所有有效组完成后导出，数量满足 1.5。

recipe 是每条 episode 的完整配方，保存 session/实例 ID、分配的顺序、按逻辑片段 ID 引用的素材清单、B 的来源与边界以及回放起点路径。源哈希、物体映射和帧区间读取被引用清单，避免重复维护。`source_pairings.json` 只保存实例到 episode、recipe 的索引，素材选择和顺序以 recipe 为准。

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

全部段区间首尾相接，总帧数等于各段长度之和。答案和 `object_events` 作为内部注释保留，不进入逐帧训练指令；全部输出 episode 均保留完整 Query 数据。

### 2.10 首个 demo：吊柜里的杯子 + 18 + 19

首个 demo 属于 Level 1，只有一个 Key，Key 后的杯子位置由 I/B 保持。具体实现提供以下三种候选顺序，在拼接时为每组素材选择一种：

```text
Key → bridge → 18 → bridge → 19 → bridge → Query
18 → bridge → Key → bridge → 19 → bridge → Query
19 → bridge → Key → bridge → 18 → bridge → Query
```

| 片段 | 任务 | 房间与来源 |
|---|---|---|
| Key | 将杯子从岛台放进三个同款吊柜中的一个并关门 | 厨房，自采 |
| 18 | `tidying_bedroom` | 卧室，2026 原始录制 |
| 19 | `outfit_a_basic_toolbox` | 杂物间，2026 原始录制 |
| Query | 将此前放入柜子的杯子取回岛台并关门 | 与 Key 相同厨房，同次自采 |
| bridge ×3 | 按分配顺序连接相邻片段的出口与入口 | 在当前统一场景状态中合成 |

场景使用 `house_single_floor`，机器人为 R1Pro。现有 BDDL 的 18 和 19 分别使用 `bedroom`、`utility_room`；具体录制仍要核对实际房间实例、物体和状态，不能仅按任务名保证兼容。

杯柜 demo 可保留以下已有资产候选，前提是选定的 I 未使用它们：

| 资产 | 候选场景名 | 作用 |
|---|---|---|
| 杯子 | `mug_ntgftr_0`，按实际场景已有或采样插入 | K/Q 共享的记忆物体 |
| 三个吊柜 | `top_cabinet_lkxmne_0/1/2` | 变体 A/B/C 对应的位置 |
| 岛台 | `bar_udatjt_0` | Key 起点、Query 终点 |

若候选资产与 I 冲突，可以更换未使用的已有资产或新增对应资产，统一更新绑定；不强制替换所有已有柜子和岛台。资产位姿以冻结的场景与采样结果为准。

复用 BDDL：Key 为 `mb_mug_into_top_cabinet`，Query 为 `mb_retrieve_mug_from_top_cabinet`。目标始终通过 `cabinet.n.01_1` 绑定到变体选定的实际柜子，K/Q 保持同一绑定。Q 的物体初态来自 Key 后置状态，机器人入口来自多样化 Q 起点。

Key 本身包含将杯子放入 A、B、C 柜子的三种情况，demo 计划各采样 50 条，每个实例只执行其中一种。三种情况下 Q 都使用相同的取回指令，且开始时所有柜门关闭；模型需要根据 Key 中实际放置杯子的历史观测确定目标柜。放置情况与拼接顺序独立分配。

指令分别为：

- Key：`Put the mug from the kitchen island into a top cabinet and close the door.`
- Query：`Retrieve the mug and place it on the kitchen island.`
- 18/19：沿用对应 2026 指令；B 使用下一段指令。

任务检查直接重新评价谓词和关节状态，不依赖缓存的 `task.success`：

- Key 出口：杯子在目标柜中，机器人空手，所有柜门关严。
- Query 入口：杯子仍在同一目标柜中，所有柜门关严，目标尚未完成；底盘已到达该实例记录的 Q 入口。
- Query 出口：杯子放回岛台并稳定，柜门关闭。
- Query 过程：不得打开错误柜子进行搜索。现有 BDDL 的终态条件不能表达这一要求；切段时根据柜门关节轨迹生成开门事件，用任务配置的开门阈值检查全过程，不能仅看最终柜门是否关闭。
- 18/19 不使用或改变杯子、三个吊柜和岛台，操作物体也不侵入 K/Q 操作区或阻塞所需导航路线。

demo 按 1.5 的数量规则构建，每条完整 episode 含三段 B。多 Key、Key 位于中间及两侧插段的能力纳入公共接口联调。

### 2.11 实施步骤与完成标准

| 阶段 | 实现内容 | 完成标准 |
|---|---|---|
| D0 公共接口 | 同步任务/准备入口，定义 session、段清单、依赖和顺序配置 | 单 Key、多 Key、Key 两侧插段均可表达，依赖引用和顺序检查可用 |
| D1 少量贯通与实例准备 | 检查 18/19，合并场景、隔离资产；先联调采集、匹配、B 和渲染，再冻结正式实例 | 路线与入口可执行，候选素材足够兼容，正式实例按 1.5 准备完成 |
| D2 联合采集 | 实现 Key 列表推进、自动导航、准备确认、任务切换和边界记录 | demo 联合录制可切段；多 Key 切换保留世界状态且入口标注准确 |
| D3 衔接与拼接 | 选取 I 素材、分配顺序，生成 B、recipe、拼接 HDF5 和回放起点 | 素材索引完整，每组指定顺序通过状态与衔接检查 |
| D4 重渲染与导出 | 全场景 replay、LeRobot 输出、注释及读回 | 正式数量满足 1.5，帧数、视频、指令与动作对齐，人工抽查边界 |
| D5 Level 2 任务 | 分别设计记忆更新和信息综合任务，沿用 D1—D4 | 明确每个 Key 的信息与入口条件，Q 确实需要多个片段，数据完整可读回 |

D1 与 D2—D4 的最小实现交叉联调，用少量素材验证全流程，再开展批量构建。配置冻结后开展正式采集；模板变化时检查已有素材是否仍兼容。正常物理加载与稳定性检查用于联调及代表性快照抽查，不逐个重复加载所有边界；visual-only replay 用于生成观测，物理可执行性由采集和 B 执行确认。

Level 2 任务设计还需补充具体 BDDL、记忆情况组合与下一 Key 的可操作入口。例如：

- **记忆更新任务**：Key₁ 让机器人获知多只杯子中的目标，Key₂ 移动多只杯子并更新位置，Q 要取回此前指定的目标。Key₂ 不复述目标身份，Q 需要结合 Key₁ 的目标信息与 Key₂ 的位置更新；只看到 Key₂ 不能确定取哪只杯子。
- **信息综合任务**：Key₁ 提供杯子位置，Key₂ 提供盘子位置，Q 要取回两者；两个位置分别变化，Q 的指令保持一致，需用到两段历史信息。

这些是设计方向，具体物体、指令与变体在对应任务中确定。Level 1 demo 完成后，Level 2 仍需完成任务设计和数据构建。

项目代码优先放在 `memory_bench/`；JoyLo 切换键和录制事件采用小范围改动，复用现有控制与记录机制。

后续扩展通过配置增加 Key、I、记忆任务类型、场景和源录制，沿用同一套准备、段接口、依赖校验及 replay 流程。每个序列仍只有一个 Q；动态物体追踪等任务通过明确的共享物体状态依赖接入。
