# Session YAML 阅读与填写指南

先确定任务和顺序，再看采样参数，最后看机器人及导出设置。带注释的正式全流程模板见 [session_template.yaml](session_template.yaml)，对应已注册的单 Key 杯柜任务。模板包含各阶段需要的字段；更换任务还需匹配 `tasks/` 中的定义和 BDDL 活动，不能仅修改 YAML 中的活动名。

## 先理解任务定义与拼接顺序

| 配置 | 回答的问题 | 示例 |
| --- | --- | --- |
| `segments` | 有哪些任务，各自在哪里执行、依赖谁？ | `key_1`、`intervent_1`、`intervent_2`、`query` |
| `composition.task_orders` | 最终完整序列按什么顺序组合任务？ | `[intervent_1, key_1, intervent_2, query]` |

`key_1` 等是配置内的片段 ID；`role` 表示片段角色；`activity` 才是 BDDL 活动名。依赖及顺序列表引用片段 ID。

采集固定从实例 1 开始，按 Key 编号递增完成 `key_1`、`key_2` 等片段，最后采集 Query，每个子任务独立保存 HDF5。录制保存到 `output_root/recordings/<session_id>/instance_NNNN/demo_NNN/`，Home 推进到下一实例。I 从已有录制中准备，Bridge 在组装时生成。因此配置中只列 K/I/Q，最终序列会自动变成 `I → B → K → B → I → B → Q` 等形式。候选顺序由 `build_episodes.py` 使用 `composition.order_seed` 分配。四步的数据处理参数从 YAML 读取；遥操硬件、连接、GPU 和窗口设置保留在启动脚本中。

## 1. 身份、场景与路径

| 字段 | 用途 | 使用位置 |
| --- | --- | --- |
| `session_id` | 本次任务及输出分组的标识；正式配置必须等于 Query 的活动名，并据此查找注册任务 | [Session](../session.py)：校验、`task`；所有阶段的输出路径 |
| `scene_model` | 场景模型；应与任务定义、准备场景和输入录制一致 | [I 素材准备](../collection/prepare_intervent.py)、[Bridge 环境构建](../episodes/generate_bridges.py) |
| `memory_room` | 约束所有 K/Q 在同一房间，I 位于该房间之外 | [Session.validate](../session.py) |
| `benchmark.level` | 声明记忆等级：一个 Key 为 1，两个及以上为 2；与实际 Key 数校验 | [Session.validate](../session.py)、[episode 注释](../episodes/compose_episodes.py) |
| `paths.data_root` | OmniGibson 资产和官方任务实例根目录 | [SessionPaths](../utils/paths.py)、[实例准备](../collection/prepare_session.py) |
| `paths.intervent_rawdata_root` | 已有 I 录制的根目录 | [I 素材准备](../collection/prepare_intervent.py) |
| `paths.output_root` | 本次所有生成产物的根目录 | `SessionPaths` 目录初始化及所有阶段 |

三个根路径必须是绝对路径。`SessionPaths` 接收这三个根路径、`session_id` 和 `scene_model`，在加载时一次性生成以下目录属性；这些属性无需填写到 YAML：

| 代码中的目录属性 | 相对于 `output_root` 的位置 |
| --- | --- |
| `task_instances_dir` | `memory-bench-task-instances/` |
| `scene_json_dir` | `memory-bench-task-instances/scenes/<scene_model>/json/` |
| `session_dir` | `sessions/<session_id>/` |
| `recordings_dir` | `recordings/<session_id>/` |
| `recipes_dir` | `recipes/<session_id>/` |
| `bridges_dir` | `bridges/<session_id>/` |
| `episodes_dir` | `episodes/<session_id>/` |
| `export_dir` | `lerobot/<session_id>/` |
| `reports_dir` | `reports/<session_id>/` |

调用处直接使用 `session.paths.session_dir / "assets.json"` 等路径。路径初始化不创建目录、不修改环境变量；目录由实际写入流程创建，环境变量由运行入口设置。清单中的生成产物引用统一相对于 `output_root`，外部输入使用绝对路径。写入时通过 `Path.relative_to(output_root)` 生成相对引用，读取时通过 `session.paths.resolve(reference)` 定位。LeRobot 自身管理的视频及元数据路径遵循其数据集格式。

## 2. 任务片段及顺序

| 字段 | 用途 | 使用位置 |
| --- | --- | --- |
| `segments.<id>.role` | `key_task`、`intervent_task` 或 `query_task` | Session 校验、采集、切段、拼接 |
| `segments.<id>.activity` | 对应的 BDDL 活动名；K/Q 必须匹配注册任务 | [实例准备](../collection/prepare_session.py)、[素材检查](../utils/recordings.py)、[任务条件检查](../tasks/base_task.py) |
| `segments.<id>.room_id` | 具体房间实例，如 `kitchen_0` | Session 房间隔离校验、实例采样、素材匹配 |
| `segments.<id>.depends_on` | 当前任务之前必须完成的片段 ID；Query 必须声明全部 Key | `Session.validate_order()`、采集顺序校验 |
| `segments.<I>.source.task_id` | I 原始录制目录编号，例如 18 对应 `task-0018/` | [scan_sources](../collection/prepare_intervent.py) |
| `segments.<I>.extra_objects` | 可选，声明录制中额外使用的物体，默认空列表 | [task_assets / inspect_source](../utils/recordings.py) |
| `composition.task_orders` | 1～3 个最终候选顺序；每个 K/I/Q 恰好一次、依赖满足、Q 最后 | [素材匹配](../episodes/build_episodes.py)、[组装](../episodes/compose_episodes.py) |
| `composition.demo_id` | 构建 recipe 时选用的完整采集轮次，1 对应 `demo_001` | `build_episode()` |
| `composition.order_seed` | 候选任务顺序的分配种子 | `build_episodes.main()` |
| `composition.instance_id` | `null` 构建、拼接全部实际实例；整数只处理该实例；最终导出仍按实例顺序处理全部 recipe | `build_episodes.main()`、`compose_episodes.main()` |

构建时可用 `--instance-id`、`--seed`、`--demo-id` 分别覆盖 `composition.instance_id`、`composition.order_seed`、`composition.demo_id`；拼接时可用 `--instance-id`。未指定的参数沿用 YAML，覆盖仅在本次运行的内存配置中生效，不写回文件。单实例构建保留同种子全量构建时的任务顺序。

采集使用左方向键完成当前子任务、右方向键或键盘 X 开始或恢复，Home 加载下一已准备实例。K/Q 指令来自任务定义，I 指令由输入录制的活动名生成，配置中没有 `instruction` 或切换按键字段。

## 3. 实例、资产与起点

| 字段 | 用途 | 使用位置 |
| --- | --- | --- |
| `instance_sampling.count` | 最初请求原始采样器生成的实例数；后续以实际 TRO 数量和冻结分配为准 | [sample_tasks](../collection/prepare_session.py) |
| `instance_sampling.seed` | 原始任务采样及实例准备的随机种子，也是默认的变体分配种子 | [sample_tasks / assign_variants](../collection/prepare_session.py)、[sample_instances](../collection/sample_instances.py) |
| `variant_sampling.seed` | 可选，独立控制任务变体的平衡分配和打乱；默认沿用实例种子 | `assign_variants()` |
| `assets.requirements.<label>.instances` | **配置输入**：Query 活动里的 BDDL 角色列表，例如 `mug.n.04_1`；分配时解析为真实物体 | `assign_variants()`、`assemble_scene_assets()` |
| `assets.requirements.<label>.source` | 资产来源约束：`scene` 必须原有，`insert` 必须新增，`scene_or_insert` 两者均可 | `assemble_scene_assets()` |

`prepare_session.py <config> sample --instance-count N --seed S` 可覆盖本次采样请求数量和原始任务采样种子，N 必须为正整数。未指定的参数沿用 `instance_sampling.count` 和 `instance_sampling.seed`，不写回 YAML；后续分配和准备以实际生成的 TRO 数量为准，种子仍读取各自配置。

I 素材扫描记录实际文件总数、元数据兼容数量及拒绝原因。分配时按实际实例数选择不同录制，仅在兼容素材不足时报告缺口。

各启动脚本直接指定 `MB_CONFIG`，不重复维护数量、数据路径、场景、活动或种子。GPU 和窗口设置写在对应脚本中，JoyLo 硬件及连接参数沿用 `run_joylo.sh` 中的配置。`prepare_session sample` 读取 YAML 并依次调用原始采样器，只有首次采样请求使用 `instance_sampling.count`。变体和 I 分配、采集、构建、拼接及导出都沿用实际结果，缺少上游结果时报错，不回退到配置数量。

`requirements` 的 label（如 `mug`）是资产分组名。`instances` 写 Query 的 BDDL 角色，不能写采样生成的 `mug_231` 等场景物体名。场景物体绑定及保护集合由准备阶段生成。

## 4. 机器人运行与边界检查

| 字段 | 用途 | 使用位置 |
| --- | --- | --- |
| `bridge.action_frequency` | 采集及 Bridge 的动作频率，默认 30 Hz；通过 `session.action_frequency` 统一读取 | [collect_session](../collection/collect_session.py)、[generate_bridge](../episodes/generate_bridges.py) |
| `bridge.max_base_linear_velocity` | Bridge 的底盘线速度，默认 0.2 m/s | Bridge 导航 |
| `bridge.max_base_angular_velocity` | Bridge 的底盘角速度，默认 0.5 rad/s | Bridge 导航 |
| `bridge.map_resolution` | 可选，Bridge 导航地图分辨率，默认 0.02 m | `_build_config()` |

导航采用连续前视跟踪，中间路点不单独停车或转向；急弯提前减速，最终目标单独对齐并停稳。前视线和短程制动预测在预留机器人占地的栅格上检查，碰撞及世界状态检查仍逐步执行。`drive_to_pose()` 默认前视距离为 0.25 m、平移加速度上限为 0.4 m/s²、角加速度上限为 1.0 rad/s²；这些参数仍需实际场景标定。地图分辨率保持不变。

状态比较阈值在 [scene_state.py](../utils/scene_state.py) 中维护；位置和派生包围盒坐标使用相同的 0.02 m 容差，姿态比较使用四元数分量差。机器人到点和停稳阈值由导航、采集及边界检查函数维护，仍需实际场景标定。上述检查始终执行，Session YAML 不提供校验阈值配置。

## 5. 渲染与导出

| 字段 | 用途 | 使用位置 |
| --- | --- | --- |
| `export.obs_modalities` | 输出观测，如本体状态、RGB、线性深度 | [replay_episode](../episodes/replay_episodes.py) |
| `export.n_render_iterations` | 每帧恢复状态后、读取观测前的渲染刷新次数 | `_render_observation()` |
| `export.sensor_config` | 传给 OmniGibson 的传感器初始化参数，如图像尺寸、相机孔径 | `replay_episode()` |

## 填写顺序

1. 选择已有任务配置，确认 `session_id`、活动、房间和 BDDL 角色。
2. 设置输入根目录和独立的输出根目录。
3. 确认 Key 的遥操顺序、Query 的依赖和最终候选顺序。
4. 设置实例与变体的种子，盘点 I 素材数量。
5. 在标定后确定速度与容差，再确定输出模态及相机设置。

所有配置统一从 `Session.load()` 读取，仅集中检查任务顺序、依赖、房间隔离等流程约束，不再维护字段白名单；额外字段会保留在配置中，是否使用由各阶段决定。实例清单、资产清单、每轮 `manifest.json` 和 episode recipe 是运行产物，单独由各阶段读取。YAML 不记录逐实例真实物体绑定、文件身份或 Bridge 动作，这些由运行过程生成。

`session.json` 只记录准备时的完整配置，不筛选字段或比较配置以复用旧产物。重新准备使用新的输出目录；已有 session 或变体分配时直接报错。后续步骤仍以实际分配结果为准，修改 `instance_sampling.count` 不会增减已经生成的实例。

## 准备产物

原始任务采样目录是 `session.paths.task_instances_dir`。原始采样入口设置 `OMNIGIBSON_DATA_PATH` 和 `OMNIGIBSON_TASK_INSTANCES_DATASET`，供采样器加载资产及该目录中的任务元数据和稳定场景。实例准备、采集、构建、拼接、渲染和实际回放只设置加载资产所需的 `OMNIGIBSON_DATA_PATH`，场景和录制路径直接传入。初始化、分配、素材盘点及回放的 `--check-only` 不设置这两个变量。分配清单的 `source_tro`、`inputs` 和模板中的分配引用使用相对输出根目录的路径，方便随 `sessions/` 一起搬迁。

`assign-variants` 只冻结 `sessions/<session_id>/variant_assignments.json`，不复制或改写原始 TRO。`prepare_intervent` 在采集前读取素材盘点报告，只检查 I 录制的元数据和资产兼容性，将每个实例的原始 HDF5 路径和文件内 demo 保存到 `intervent_assignments.json`，不启动仿真或解码轨迹，也不写入物理预检结果。已有变体分配不变，I 分配单独按 `instance_id` 保存。

`sample_instances` 读取上述两份分配清单，用各实例原始 Key 状态和已分配 I 片段的真实第 0 帧物体状态生成 `scene_template.json` 和 `tro/instance_XXXX.json`。公共模板不含机器人定义和状态；最终 TRO 只包含实例身份、模板引用、相对模板的世界状态差异、机器人底座位置和朝向、初始条件检查结果。变体、绑定和 I 分配不在 TRO 中重复保存，随机种子由 `instance_sampling.seed + instance_id` 确定。公共模板保存分配文件和固定资产清单 `session_dir/assets.json` 的路径引用。采集时补入当前机器人并应用遥操 reset；采集对模板和 TRO 只读。拼接不再覆盖 I 布局，分配或初态不一致即报错。影响实例准备的配置或采样输入变化需要使用新的输出目录；仅修改 JoyLo reset 关节配置后重启采集即可。

实例准备保留物体停稳、BDDL 初态、起点房间和已分配 I 入口布局检查，不读取遥操 reset 配置；TRO 的 `validation` 仅保存 `first_key_initial_predicates`。`prepare_intervent` 不再复验 I 的首尾任务条件、空手或稳定性。后续 Bridge 合成保留实际边界的空手、碰撞、稳定性和世界状态衔接检查；遥操机器人的空手、碰撞、稳定性和公共 reset 起点统一在采集启动及子任务边界检查。

每轮采集沿用实例第一个 Key 已采样的位置、朝向，应用当前 JoyLo reset 关节配置，检查碰撞、空手和稳定性后，在内存中建立本轮全部 Key/Query 共用的机器人起点。Home 切换实例时按下一实例的采样位置、朝向重新建立起点。每个子任务通过检查后独立保存并关闭 HDF5，再只恢复机器人到本轮起点，保留世界状态。恢复及停稳不进入录制，世界继承检查写入 `manifest.json`；第三、第四步不复查公共起点或采集转场历史，按实际边界生成 Bridge 并验证最终拼接的任务前提与交接。每段只支持任务内回滚。`build_episodes.sh` 默认使用完整 `demo_001` 生成导航和 recipe，再由 `export_session.sh` 逐帧解码拼接并导出。无须配置或单独采样 Query 起点。

四步运行和输出结构见 [README](../README.md#四步流程)。不生成 normalized HDF5、独立段清单或素材索引。`composition.demo_id` 和 `composition.order_seed` 仅在构建时选择录制轮次和任务顺序，recipe 保存已选定的各段来源路径、帧范围、边界引用及任务顺序；拼接时不再读取采集清单或重新选择 demo。Key 文件使用两位编号。最终 Query 评测起点保存在 `episodes/<session_id>/episode_XXXXXXXX_query_start.json`，包含累计世界状态和 Query 绑定。
