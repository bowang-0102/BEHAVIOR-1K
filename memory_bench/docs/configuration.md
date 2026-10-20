# Session YAML 阅读与填写指南

先确定任务和顺序，再看采样参数，最后看机器人及导出设置。带注释的正式全流程模板见 [session_template.yaml](session_template.yaml)，对应已注册的单 Key 杯柜任务。模板包含各阶段需要的字段；更换任务还需匹配 `tasks/` 中的定义和 BDDL 活动，不能仅修改 YAML 中的活动名。

## 先理解三套定义

| 配置 | 回答的问题 | 示例 |
| --- | --- | --- |
| `segments` | 有哪些任务，各自在哪里执行、依赖谁？ | `key_1`、`intervent_1`、`intervent_2`、`query` |
| `collection.key_segment_ids` | 人工遥操时，按什么顺序录制 Key？ | `[key_1]`，之后自动追加 Query |
| `composition.task_orders` | 最终完整序列按什么顺序组合任务？ | `[intervent_1, key_1, intervent_2, query]` |

`key_1` 等是配置内的片段 ID；`role` 表示片段角色；`activity` 才是 BDDL 活动名。依赖及顺序列表引用片段 ID。

连续采集依次完成全部 Key 和 Query，每个子任务独立保存 HDF5。I 从已有录制中准备，Bridge 在组装时生成。因此配置中只列 K/I/Q，最终序列会自动变成 `I → B → K → B → I → B → Q` 等形式。候选顺序由 `pair_segments.py` 使用独立的顺序种子分配，种子通过导出脚本的 `ORDER_SEED` 设置。

## 1. 身份、场景与路径

| 字段 | 用途 | 使用位置 |
| --- | --- | --- |
| `session_id` | 本次任务及输出分组的标识；正式配置必须等于 Query 的活动名，并据此查找注册任务 | [Session](../session.py)：校验、`task`；所有阶段的输出路径 |
| `scene_model` | 场景模型；应与任务定义、准备场景和输入录制一致 | [录制扫描](../processing/scan_recordings.py)、[Bridge 环境构建](../episodes/synthesize_bridges.py) |
| `memory_room` | 约束所有 K/Q 在同一房间，I 位于该房间之外 | [Session.validate](../session.py) |
| `benchmark.level` | 声明记忆等级：一个 Key 为 1，两个及以上为 2；与实际 Key 数校验 | [Session.validate](../session.py)、[episode 注释](../episodes/compose_episodes.py) |
| `paths.data_root` | OmniGibson 资产和官方任务实例根目录 | [SessionPaths](../utils/paths.py)、[实例准备](../collection/prepare_session.py) |
| `paths.intervent_rawdata_root` | 已有 I 录制的根目录 | [录制扫描](../processing/scan_recordings.py)、调试录制路径解析 |
| `paths.output_root` | 本次所有生成产物的根目录 | `SessionPaths.resolve()` 及所有阶段 |

三个根路径必须是绝对路径。配置中生成产物的相对路径，例如 `assets.manifest`，相对于 `output_root`。

## 2. 任务片段及顺序

| 字段 | 用途 | 使用位置 |
| --- | --- | --- |
| `segments.<id>.role` | `key_task`、`intervent_task` 或 `query_task` | Session 校验、采集、切段、拼接 |
| `segments.<id>.activity` | 对应的 BDDL 活动名；K/Q 必须匹配注册任务 | [实例准备](../collection/prepare_session.py)、[素材检查](../utils/recordings.py)、[任务条件检查](../tasks/base_task.py) |
| `segments.<id>.room_id` | 具体房间实例，如 `kitchen_0` | Session 房间隔离校验、实例采样、素材匹配 |
| `segments.<id>.depends_on` | 当前任务之前必须完成的片段 ID；Query 必须声明全部 Key | `Session.validate_order()`、采集顺序校验 |
| `segments.<I>.source.task_id` | I 原始录制目录编号，例如 18 对应 `task-0018/` | [scan_sources](../processing/scan_recordings.py) |
| `segments.<I>.extra_objects` | 可选，声明录制中额外使用的物体，默认空列表 | [task_assets / inspect_source](../utils/recordings.py) |
| `collection.key_segment_ids` | 全部 Key 的实际遥操顺序，每个 Key 恰好一次；Query 自动追加 | [SessionCollector](../collection/collect_session.py) |
| `composition.task_orders` | 1～3 个最终候选顺序；每个 K/I/Q 恰好一次、依赖满足、Q 最后 | [素材匹配](../episodes/pair_segments.py)、[组装](../episodes/compose_episodes.py) |

采集使用左方向键完成当前子任务、右方向键或键盘 X 开始或恢复，Home 加载下一已准备实例。K/Q 指令来自任务定义，I 指令由输入录制的活动名生成，配置中没有 `instruction` 或切换按键字段。

## 3. 实例、资产与起点

| 字段 | 用途 | 使用位置 |
| --- | --- | --- |
| `instance_sampling.count` | 计划实例数；冻结变体分配前供 `instance_count` 使用，分配后以实际 TRO 数量为准 | [Session.instance_count](../session.py) |
| `instance_sampling.seed` | 实例物理状态的随机种子，也是默认的变体分配种子 | [sample_instances](../collection/sample_instances.py)、[assign_variants](../collection/prepare_session.py) |
| `case_sampling.seed` | 可选，独立控制任务变体的平衡分配和打乱；默认沿用实例种子 | `assign_variants()` |
| `assets.manifest` | **输出文件路径**：准备阶段生成资产清单，后续读取它检查资产身份和隔离 | [assemble_scene_assets](../collection/prepare_session.py)、切段及拼接 |
| `assets.requirements.<label>.instances` | **配置输入**：Query 活动里的 BDDL 角色列表，例如 `mug.n.04_1`；分配时解析为真实物体 | `assign_variants()`、`assemble_scene_assets()` |
| `assets.requirements.<label>.source` | 资产来源约束：`scene` 必须原有，`insert` 必须新增，`scene_or_insert` 两者均可 | `assemble_scene_assets()` |
| `intervent_selection.candidate_count_per_task` | 素材盘点报告的预期候选数量，用于计算缺口；有效 I 的需求量由实际实例数决定 | `scan_sources()` |

当前采样请求数量还由 `scripts/common.sh` 的 `INSTANCE_COUNT` 设置。它控制请求生成多少 TRO；`instance_sampling.count` 是配置中的计划值，变体分配会使用实际已有的全部匹配 TRO。使用现有脚本时需同步检查这两个值。路径、session、场景和活动列表也需与该脚本一致。

`requirements` 的 label（如 `mug`）是资产分组名。`instances` 写 Query 的 BDDL 角色，不能写采样生成的 `mug_231` 等场景物体名。场景物体绑定及保护集合由准备阶段生成。

## 4. 机器人运行与边界检查

| 字段 | 用途 | 使用位置 |
| --- | --- | --- |
| `bridge.action_frequency` | 采集及 Bridge 的动作频率，默认 30 Hz；通过 `session.action_frequency` 统一读取 | [collect_session](../collection/collect_session.py)、[synthesize_bridge](../episodes/synthesize_bridges.py) |
| `bridge.max_base_linear_velocity` | Bridge 的底盘线速度，默认 0.2 m/s | Bridge 导航 |
| `bridge.max_base_angular_velocity` | Bridge 的底盘角速度，默认 0.5 rad/s | Bridge 导航 |
| `bridge.map_resolution` | 可选，Bridge 导航地图分辨率，默认 0.02 m | `_build_config()` |

状态比较阈值在 [scene_state.py](../utils/scene_state.py) 中维护；位置和派生包围盒坐标使用相同的 0.02 m 容差，姿态比较使用四元数分量差。机器人到点和停稳阈值由导航、采集及边界检查函数维护，仍需实际场景标定。上述检查始终执行，Session YAML 不提供校验阈值配置。

## 5. 渲染与导出

| 字段 | 用途 | 使用位置 |
| --- | --- | --- |
| `export.obs_modalities` | 输出观测，如本体状态、RGB、线性深度 | [render_episode](../episodes/render_episodes.py) |
| `export.n_render_iterations` | 每帧恢复状态后、读取观测前的渲染刷新次数 | `_render_observation()` |
| `export.sensor_config` | 传给 OmniGibson 的传感器初始化参数，如图像尺寸、相机孔径 | `render_episode()` |

## 6. 调试配置与正式模板的区别

仅调试两段 I 和一个 Bridge 时，使用 [debug_intervents.yaml](../configs/debug_intervents.yaml)。共同保留路径、场景模型、资产清单路径、片段、物理检查、Bridge 和导出设置；省去 K/Q、实例采样、记忆资产 requirements 和候选顺序设置。

| 调试字段 | 用途 | 使用位置 |
| --- | --- | --- |
| `mode: intervent_debug` | 选择 I/Bridge 调试的校验规则 | `Session.validate()` |
| `full_scene` | 调试使用的完整初始场景 JSON 路径 | [prepare_world](../tools/debug_intervents.py) |
| `task_order` | 两个独立 I 的明确执行顺序 | `prepare_sources()`、Bridge 合成、拼接 |
| `recordings.<id>.hdf5` | 每个 I 对应的录制路径，相对于 `intervent_rawdata_root`，也支持绝对路径 | `Session.recording_path()`、`prepare_sources()` |
| `recordings.<id>.demo` | HDF5 内的 demo 编号，如 0 对应 `demo_0` | 录制解析与 I 素材准备 |
| `open_doorways` | 准备调试场景时需打开的通道门物体名列表 | `prepare_world()` |

调试配置直接指定录制文件，因此 I 片段不用 `source.task_id`。`recordings` 中的任务必须与 `task_order` 一一对应。

## 填写顺序

1. 选择已有任务配置，确认 `session_id`、活动、房间和 BDDL 角色。
2. 设置输入根目录和独立的输出根目录。
3. 确认 Key 的遥操顺序、Query 的依赖和最终候选顺序。
4. 设置实例与变体的种子，盘点 I 素材数量。
5. 在标定后确定速度与容差，再确定输出模态及相机设置。

所有配置统一从 `Session.load()` 读取，仅集中检查任务顺序、依赖、房间隔离等流程约束，不再维护字段白名单；额外字段会保留在配置中，是否使用由各阶段决定。实例清单、资产清单、每轮 `manifest.json` 和统一处理后的 `segment_index.json` 是运行产物，单独由各阶段读取。YAML 不记录逐实例真实物体绑定、文件身份或 Bridge 动作，这些由运行过程生成。

## 准备产物

`assign-variants` 只冻结 `sessions/<session_id>/case_assignments.json`，不复制或改写原始 TRO。`sample_instances` 读取分配清单与干扰素材盘点报告，准备一个 `scene_template.json`，并为每个检查通过的实例保存 `tro/instance_XXXX.json`。最终 TRO 包含状态、对象绑定、共用机器人初态、随机种子和检查结果；公共资产清单仍写到 `assets.manifest`。配置或采样输入变化需要使用新的输出目录。

每个实例的全部 Key 与 Query 共用该实例第一个 Key 的完整初始机器人状态；不同实例仍使用各自原始 TRO 的起点。每个子任务通过检查后独立保存并关闭 HDF5，再只恢复机器人，不重新加载世界或初始化任务。恢复及停稳不进入录制，世界继承检查写入 `manifest.json`；最终 Bridge 仍按实际入口、出口合成物理导航。每段只支持任务内回滚。自采 K/Q 和复用 I 都经 `process_recordings.sh` 生成统一片段接口与索引，再交给 `export_session.sh`。无须配置或单独采样 Query 起点。
