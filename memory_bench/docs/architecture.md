# Memory Bench 项目架构

> 本文定义目录、数据格式、模块接口与扩展边界；[Pipeline](mug_in_top_cabinet_pipeline.md) 定义首个任务的实验流程；[工程任务](mug_in_top_cabinet_tasks.md) 定义实现顺序和验收标准。公共格式以本文为准。
> 通用 pipeline/core 与 `mb` CLI 仍是设计方案；「吊柜里的杯子」的 BDDL、任务插件和 B1–B3 数据准备脚本已实现，运行步骤见任务目录 README。仿真采样与 JoyLo 硬件验收仍需执行。

## 1. 目标与首版范围

先用「吊柜里的杯子」完成一条真实 episode：采集 Key、恢复 Query 起点、执行 Query、渲染历史并计分，再接入一条已有干扰 demo。首版固定 `house_single_floor` 和 R1Pro，使用本地进程、头部 RGB 和手写 spec。

优先稳定四个接口：状态适配、段来源、任务插件、策略输入。批量采样、导航过渡、Slurm、多模态与发布自动化在闭环通过后实现。假 Key 只用于开发，不计入真实数据的验收。

需要从设计上保证：

- 任务逻辑通过插件接入，pipeline 不出现杯子、柜子等特定物体名。
- 遥操和已有 demo 导出成同一 Segment 格式，渲染器不直接读取原始 HDF5。
- 稀疏状态、末动作后的终态、机器人复位都有明确语义。
- 历史末尾与 Query 起点一致；修改世界的后处理必须有记录。
- 模型只接收协议允许的观测；答案、快照和 QC 留在评测内部。
- 中间产物记录输入、配置、代码和运行环境，失败后可定位和重跑。

## 2. 核心概念与状态契约

```text
Suite → EpisodeSpec（内部配方）→ C0
                                 │
             Key → 后处理 → 干扰 / 过渡 → Query 准备
                                 │
                      各段快照 + Segment → Cq
                                 │
                    Render → HistoryView（模型可见）
                                 │
                    Eval 从 Cq 执行 Query → 指标

训练 Query 示范单独导出，不拼入 HistoryView。
```

| 概念 | 职责 | 主要落点 |
|---|---|---|
| WorldSnapshot | 原生场景 JSON 与对应的版本、配置和状态摘要 | `core/world_json.py`、`sim/state_io.py` |
| Segment | 带初态、逐帧补丁、时间信息和终态的经历 | `core/schemas.py` |
| SegmentSource | 导出已有录制；生成来源另按起始世界生成段 | `sources/` |
| MemoryTask | 世界需求、变体绑定、分阶段约束、指令、监控和评分 | `tasks/<name>/` |
| EpisodeSpec | 初始世界引用、变体、历史段及可选 Query 示范 | `core/schemas.py` |
| Suite | 冻结的任务集合、划分、观测与评测协议 | `configs/suites/`、`release/` |

### 2.1 Segment 的统一语义

一个 Segment 包含 `meta.json` 和 `frames.pt`。`frames.pt` 的逻辑格式如下；内部张量存储可以优化，但不能改变语义：

```python
{
    "schema_version": 1,
    "initial_state": {"robot": {...}, "toy_figure_242": {...}},
    "frames": [
        {"timestamp_s": 0.0, "patch": {...}, "action": ..., "action_valid": True},
        # 每条 patch 是该动作执行前的状态更新
    ],
    "terminal_patch": {...},  # 最后一个动作执行后的更新；缺失时为 None
}
```

1. 补丁按物体名映射到完整的单物体状态。**缺失物体表示沿用上一状态**，不表示删除、归零或回到模板。
2. `initial_state` 必须完整覆盖写集合与机器人；原始首帧缺少对象时，从录制场景和初始元数据恢复，并记录来源。无法恢复则拒绝导出。
3. 累计初态、所有补丁及 `terminal_patch` 得到 `effects`。进入睡眠后不再出现的对象保留最后已知状态，不能因后续缺帧而排除。
4. `state_phase = pre_action`：帧状态与当前动作执行前对齐。静态段、状态后处理或 cut 没有真实动作时标记 `action_valid = False`，不作为控制示范。
5. 最后存储的帧不能默认当作末动作后的终态。自行采集在 reset/关闭前额外保存完整终态；已有数据检查时间语义，必要时在原生物理环境恢复末动作并验证。记录 `terminal_status = captured | recovered | missing`；缺失终态的段只用于开发预览，不能正式拼接。
6. 状态位置按 `coordinate_frame` 声明为场景坐标，长度单位为米、角度为弧度、四元数为 xyzw。StateAdapter 负责原生世界坐标与场景坐标转换。
7. 关节向量携带关节名与顺序；机器人携带模型、控制器、抓取模式和配置签名。逻辑键统一为 `robot`，实际名字、约束引用和相机名由适配层映射。
8. `n_frames` 是动作前状态记录数。终态是否渲染为额外观测由 render profile 规定；实际输出帧数以渲染索引为准，不直接等同于 `num_samples`。分别记录模拟时间和视频播放时间；cut、后处理等无动作边界可增加显示帧，但不能增加虚构的记忆距离。

首版只支持固定物体集合，拒绝物体新增/删除、粒子系统及 transitions。以后支持时升级 schema 和适配器，不能用缺失字段隐含表达。

### 2.2 写集合、读取依赖与兼容性

`write_set` 是该段写入的非机器人对象集合，包括运动、关节和相关 non-kinematic 状态变化。`required_objects` 还包括支撑面、容器、固定家具和抓取约束引用。不能仅根据位移阈值推断全部依赖。

前提检查覆盖对象存在、模型/尺寸/关节签名、相关位置和旋转、关节状态及必要非运动状态。阈值按字段配置，不能把所有状态统一解释为“误差小于 1 cm”。碰撞检查独立于写集合检查。导出段只复用到满足前提的世界，不承诺可叠加到任意世界。

### 2.3 世界编辑、校验与端点一致性

纯 Python 负责确定性的 JSON 编辑和补丁累计；仿真负责状态规范化、机器人复位、物理验证及渲染。

```text
candidate_end = accumulate(start, initial_state, frames, terminal_patch)
assert candidate_end 与 Segment.effects 一致
validate(candidate_end) → qc report
render(start, segment) → observations + render index
```

**首版静置校验只检查候选快照的副本，不把静置后的世界隐式写回。** 保存前后差异并检查稳定性。若要采用静置后的状态，导出显式 `settle` 段，重新做端点与渲染检查。

柜门完全关闭、机器人复位产出显式 `postprocess`/`query_prepare` 段。cut 表达明确的边界跳变，不伪装成导航动作。每个 slot 绑定起始和结束快照，独立渲染时验证：

```text
累计段状态 ≈ 保存的结束快照
最后历史状态 ≈ Cq
恢复 Cq 后的首个有效观测 ≈ 历史末尾观测
```

比较位姿、关节、速度、控制器及抓取状态，按适配器的字段容差报告。物理字段与渲染字段分开比较，不要求 GPU 物理结果或像素逐位相同。

## 3. 分层与依赖方向

```text
cli → pipeline → tasks / sources / eval → sim → compat → 上游
所有模块均可依赖 core；core 不 import omnigibson。
tasks 与 sources 由 pipeline 组合，不互相调用具体实现。
```

- `core` 保存数据结构、JSON 编辑、规划约束和指标等纯逻辑。
- `sim/compat.py` 封装上游私有 API、版本差异和官方数据路径。
- `sim/state_io.py` 实现 StateAdapter，屏蔽机器人、坐标与状态字段细节。
- `tasks` 声明需求和约束，通过适配器操作仿真，不自行拼接上游配置。
- 首版仿真作业单独启动进程，固定 `num_envs = 1`；物理与视觉模式分进程运行。

## 4. 仓库目录

在工作分支上实现。目录是代码落点，不要求首版创建全部模块。

```text
BEHAVIOR-1K/
├── memory_bench/
│   ├── pyproject.toml
│   ├── README.md
│   ├── docs/
│   ├── configs/
│   │   ├── tasks/mug_in_top_cabinet.yaml
│   │   ├── suites/mb_v0.yaml
│   │   ├── pools/b26_house_single_floor.yaml
│   │   ├── render/{preview,full}.yaml
│   │   ├── eval/default.yaml
│   │   └── paths.yaml
│   ├── src/memory_bench/
│   │   ├── core/{schemas,world_json,segment_ops,metrics,paths}.py
│   │   ├── core/{registry,planner,geometry,compat_checks}.py
│   │   ├── sim/{compat,session,envs,state_io,validate,render}.py
│   │   ├── sources/{base,teleop,replay2026,bridge}.py
│   │   ├── tasks/base.py
│   │   ├── tasks/mug_in_top_cabinet/{task.py,README.md}
│   │   ├── pipeline/{stages,runner}.py
│   │   ├── pipeline/executors/{local,slurm}.py
│   │   ├── eval/{harness,protocol}.py
│   │   ├── eval/policies/{hold,replay,scripted}.py
│   │   ├── release/
│   │   └── cli.py
│   └── tests/{unit,sim}/
├── bddl3/bddl/activity_definitions/
│   ├── mb_mug_into_top_cabinet/problem0.bddl
│   └── mb_retrieve_mug_from_top_cabinet/problem0.bddl
└── joylo/  # 自定义场景启动、终态捕获、提示和回放参数的可选扩展
```

BDDL 名统一使用 `mb_` 前缀；数据根目录统一为 `$MB_ROOT`；段文件统一为 `frames.pt`。模型与记忆方法另放 baseline 包，只依赖公开历史格式和 Policy 协议。

优先复用公开 API，内部调用收口到兼容层。必要 JoyLo 改动使用可选参数并单独提交，保持默认采集行为。Python 使用预装 `behavior` 环境；缺少环境或依赖时先完成环境准备，不自动安装。

## 5. 关键接口

下述类型具体实现放在 `core/schemas.py`；新增字段同步维护 schema 版本和迁移规则。

### 5.1 配方与段元数据

```python
@dataclass
class SegmentMeta:
    schema_version: int
    segment_id: str
    source: str
    scene_model: str
    coordinate_frame: str
    state_phase: str                 # pre_action
    sample_rate_hz: float
    n_frames: int
    duration_s: float
    robot_signature: dict            # 模型、关节顺序、控制器、抓取模式、配置哈希
    robot_start: dict                 # 实际 base_footprint 位姿，场景坐标、xyzw
    robot_end: dict
    write_set: list[str]              # 不含机器人
    required_objects: dict[str, dict] # 对象签名及读取依赖
    preconditions: dict[str, dict]    # 按字段定义容差
    effects: dict[str, dict]          # 含 robot；累计补丁和终态得到
    terminal_status: str             # captured | recovered | missing
    rooms_visited: list[str]
    provenance: dict                 # 输入、代码、环境、配置、终态恢复方法

@dataclass
class SlotSpec:
    role: str                        # key / postprocess / transition / distractor / key_update / query_prepare
    source: str
    ref: str | dict

@dataclass
class EpisodeSpec:
    schema_version: int
    episode_id: str
    suite: str
    split: str                       # dev / train / val / test
    scene_model: str
    memory_task: str
    variant: dict                    # 内部答案，不传给策略
    initial_ref: dict                # template / instance / existing_snapshot；instance 可选
    history_slots: list[SlotSpec]
    query_demo: dict | None           # 独立保存，dev 和 train 可采集
    protocol: str                    # 冻结的观测与评测配置
```

Query 是在线评测阶段，训练示范由 `query_demo` 引用，不混入 `history_slots`。内部 manifest 记录每个 slot 的快照路径和哈希、产物引用、阶段信息及 QC。

### 5.2 StateAdapter

```python
class StateAdapter(Protocol):
    def capture(self, env) -> WorldSnapshot: ...
    def restore(self, env, snapshot: WorldSnapshot) -> None: ...
    def decode_recording(self, raw_ref: dict) -> DecodedRecording: ...
    def apply_patch(self, env, patch: dict, mode: str) -> None: ...
    def base_pose(self, env) -> dict: ...
    def prepare_query(self, env, config: dict) -> SegmentArtifact: ...
    def bind_task(self, env, binding: dict) -> None: ...
    def check_predicates(self, env, conditions: list) -> dict: ...
    def compare(self, expected: dict, actual: dict, profile: str) -> dict: ...
```

R1Pro 实际底盘通过机器人位姿 API 读取，不直接取 `root_link`。Query 准备保持实际底盘，处理非底盘关节、速度、控制器和抓取约束，不能只把整个关节向量替换成 `reset_joint_pos`。

恢复后显式刷新任务 scope；写入 `inst_to_name` 不代表已有 scope 同步。BDDL 检查先传播状态，再直接评价谓词，或通过正确的环境 step 更新任务结果；不能用 `og.sim.step()` 后的缓存 `task.success` 代替重新评价。

### 5.3 SegmentSource

```python
class SegmentSource(Protocol):
    name: str
    def export(self, raw_ref: dict, ctx: ExportContext) -> SegmentArtifact: ...

class SegmentGenerator(Protocol):
    name: str
    needs_human: bool
    def generate(self, request: dict, start: WorldSnapshot, ctx: BuildContext) -> SegmentArtifact: ...
```

已有录制的 `export` 不依赖目标 episode，导出一次后进入 pool；复用时检查前提。遥操、导航和后处理的 `generate` 依赖具体起始世界。一个来源可以支持两种能力。先获得 pool 元数据，再初始化世界，避免导出与 C0 构建循环依赖。

### 5.4 MemoryTask

已实现部分（`tasks/base.py`）是一个冻结的 dataclass。每个任务在 `tasks/<name>/task.py` 里定义一个实例 `TASK`，公共脚本按任务名导入它，不需要再为每个任务写脚本：

```python
@dataclass(frozen=True)
class MemoryTask:
    name: str
    scene_model: str
    key_activity: str
    query_activity: str
    query_instruction: str
    room_types: list[str]
    whitelist: dict                      # task_custom_lists.json 格式
    variants: dict[str, dict[str, str]]  # 变体 -> BDDL 实例 -> 必须绑定的场景物体
    def bind_variant(self, inst_to_name, variant) -> dict: ...
```

`bind_variant` 把变体指定的实例和当前占用目标物体的实例互换，各任务通用。下面这些能力等到对应阶段实现时，再以字段或方法的形式加到 `MemoryTask` 上；只有当任务之间的行为真的不同时，才改成子类覆写：

- 分阶段的读写权限和不变量；
- 边界操作；
- 操作员提示；
- QC；
- 监控和评分。

`PhaseContext` 包含 slot 索引、角色、阶段前后和当前记忆状态。权限按阶段授权：干扰禁止碰杯子，Key/合法 `key_update` 可以操作。边界操作由适配器生成有记录的段。`audience` 区分操作员提示和策略指令，含目标柜的提示不进入 Query 输入。

### 5.5 Policy 与历史可见范围

```python
class Policy(Protocol):
    def reset(self, history: HistoryView, query: QueryRequest) -> None: ...
    def observe(self, observation: HistoryObservation) -> None: ...  # 在线历史模式
    def act(self, observation: QueryObservation) -> Action: ...
```

`HistoryView` 只引用筛选后的历史媒体、允许的本体感知和时间索引；`QueryRequest` 只包含公开指令与动作/观测规格。离线提供完整历史，在线按相同顺序逐帧发送，每条 episode 重置记忆。

内部 manifest、spec、Cq、目标柜、真实物体位置、demo 来源标识、QC 和 Query 示范不传给策略。环境显式 `include_obs = False`，奖励、goal status 和调试 info 也不混入观测。策略进程只获得公开产物引用；内部数据访问与模型通信由 harness 管理。

默认协议 `visual_v0` 提供 RGB、选定本体感知和时间戳；Key 目标柜文字提示仅给操作员。允许历史动作或语言的实验使用另一个具名协议，分别报告结果。首版不提供段角色标签，避免直接告诉模型哪段是 Key。

### 5.6 Stage 与注册

Stage 声明输入、输出、是否需要仿真/人工及 QC 条件。任务、来源和策略按名称注册，pipeline 通过能力接口调用。首版只实现本地执行和明确失败报告，等待人工不标记为成功。

## 6. 数据目录与可复现性

```text
$MB_ROOT/
├── external/2026-rawdata/task-0018/episode_*.hdf5  # 外部来源只读
├── recordings/                                   # JoyLo 原始录制
├── pools/replay2026/<id>/{meta.json,frames.pt,_stage.json}
├── worlds/<world_id>.json
├── episodes/<suite>/<episode_id>/
│   ├── spec.yaml
│   ├── states/{C0,C1,...,Cq}.json
│   ├── slots/<k>_<role>/{meta.json,frames.pt,raw.hdf5,terminal.json}
│   ├── query_demo/{raw.hdf5,terminal.json,meta.json,frames.pt}
│   ├── manifest.json
│   └── qc.json
├── renders/<suite>/<profile>/<episode_id>/{history/,query_demo/,history_index.json}
├── reports/
├── releases/<suite>@<version>/{public/,internal/}
└── eval_runs/<run_id>/
```

原生快照 JSON 保持可直接加载，状态摘要另存同名 sidecar。`raw.hdf5`、`terminal.json` 只在来源需要时存在；终态 sidecar 绑定 raw 哈希、轨迹编号和末动作索引。

任务实例单独放在 `datasets/memory-bench-task-instances/`，和官方的 `datasets/2026-challenge-task-instances/` 同级，布局也一样：`metadata/{task_custom_lists.json,available_tasks.yaml}` 和 `scenes/<scene>/json/`（稳定基础场景、`mb_*` 模板、`*_instances/`）。官方目录只读，benchmark 从不写入或登记任何东西。这个目录由 `prepare_*` 脚本从任务代码生成（白名单在 `task.py`，基础场景从官方目录复制），所以不需要纳入 git。

OmniGibson 用宏 `gm.TASK_INSTANCES_DATASET` 选择任务实例数据集，由环境变量 `OMNIGIBSON_TASK_INSTANCES_DATASET` 控制，默认是官方目录。采样脚本、`BehaviorTask` 的模板/实例查找和 `save_task`、JoyLo 的 `available_tasks.yaml` 都读这个宏。Memory Bench 的采样、遥操和评测进程设为 `memory-bench-task-instances`；回放 2026 原始 demo 的进程保持默认。合成世界等直接读取官方 JSON 的代码，用 `memory_bench.paths.OFFICIAL_TASK_INSTANCES_PATH` 显式访问。

每个阶段写 `_stage.json`：输入内容哈希、配置、schema、代码 git SHA、未提交源码内容哈希，以及 OmniGibson/BDDL/JoyLo/Isaac Sim 和资产版本、机器人配置、宏和仿真频率。仅 git SHA 不足以识别未提交实现。缓存命中要求签名匹配、输出完整且 QC 通过；临时输出验证后再原子发布。

渲染与构建分开缓存。公开索引不携带内部路径或答案；训练 Query 示范单独发布，测试内部 Cq 由评测器恢复。

## 7. 流水线

| 阶段 | 读入 | 产出 | 实施时机 |
|---|---|---|---|
| `task.prepare` | 任务代码、官方稳定场景 | `memory-bench-task-instances` 中的模板、实例、元数据 | 首版 |
| `episode.init` | 手写 spec、任务需求、可选 pool | C0、完整绑定、QC | 首版 |
| `episode.key` | C0、操作员提示 | 真实 Key Segment 与终态 | 首版 |
| `episode.compose` | 段及边界操作 | 各 slot 快照、Cq、内部 manifest | 首版 |
| `episode.query_demo` | Cq | 开发/训练 Query 示范 | 首版 |
| `episode.render` | 起始快照和 Segment | 历史、独立 Query 媒体、公开索引 | 首版 |
| `eval.run` | Cq、HistoryView、策略 | 事件、结果、视频、运行配置 | 首版 |
| `pool.export` | 一条已有 demo | 可复用 Segment | 无干扰闭环后 |
| `suite.plan` | pool 元数据、划分规则 | 成组 spec | 小规模实验后 |
| `suite.release` | QC 通过的构建和渲染产物 | public/internal 清单与校验和 | 批量阶段 |

遥操首版手动指定 episode；队列式 `mb teleop next` 待流程稳定后实现。导航失败回退 cut 时记录原因，并按连续性分别统计。

## 8. 扩展边界

| 扩展 | 插件提供 | 核心验证 |
|---|---|---|
| 新任务 | 世界需求、阶段绑定/约束、操作、监控和评分 | 首个任务接口能否承接 |
| 记忆从 A 更新到 B | `key_update`、阶段记忆状态和写权限 | 更新后不再要求杯子在 A |
| 已有 demo 作为 Key | 导出来源、初始引用、变体推断/校验 | 不要求采样实例编号 |
| 新场景 | 绑定、资产签名、可操作性和 pool | 坐标、支撑面及几何前提 |
| 新来源 | export 或 generate 能力 | 时间语义、终态及依赖 |
| 超长历史 | 更多 slot、分块导出和流式读取 | 存储、显存、渲染吞吐 |
| 新模态/模型 | render profile 或 Policy | 协议一致、无答案泄露 |

首版不实现第二个任务，但用这些场景检查接口，不承诺任意任务都无需核心改动。

## 9. 测试与质量

- 纯逻辑：补丁缺帧保留、睡眠终态累计、动作对齐、字段容差、完整绑定、阶段权限、公开字段筛选、指标事件和分组划分。
- 仿真：快照往返、底盘保持复位、控制器/抓取状态、BDDL 重新评价、真实 Key/Query、短片段端点和相机刷新。
- 黄金 episode 保存输入和预期语义结果。确定性 JSON 操作比较哈希；仿真比较字段容差和谓词，不要求物理快照哈希恒定。
- replay 从录制时等价起点执行，记录成功率和偏差。失败先排查配置和恢复一致性，不把开环物理复演当作无条件确定。
- 假 Key 和直接设柜门的 scripted 策略只验证工具；正式数据必须通过真实经历与输入隔离检查。

所有测试设置 `OMNIGIBSON_HEADLESS=1`，Python 使用 `behavior` 环境。QC 保存测量值、阈值、失败原因和是否通过；release 拒绝未完成或跳过的必需检查。

## 10. 实施顺序

| 里程碑 | 工作 | 验收 | 工程任务对应 |
|---|---|---|---|
| M0 | schema、白名单、适配器和最小任务模板 | 契约与绑定可检查 | T0、B1–B3、D0 |
| M1 | 无干扰真实 Key → Cq → Query | 状态恢复、历史和计分闭环 | B4、E1–E4、F1、F3、G1、G3、H1–H3 |
| M2 | 一条干扰导出，先选短且稳定的片段接入 cut | 累计终态、合并、渲染和 Cq 一致 | A1、C1–C3、D1–D3、F2、G2 |
| M3 | A/B/C 小规模配对样例与对照策略 | 区分记忆与操作/导航能力 | H4、I1 |
| M4 | 三段干扰、长历史和批量工具 | 吞吐、兼容性、划分和发布通过 | C4、F4–F5、G4、I2–I4 |

本地执行和必要 provenance 从首版保留，通用调度、导航、多模态和队列逐步增加。每一步以真实产物验收，单条 episode 不承担统计有效性的证明。
