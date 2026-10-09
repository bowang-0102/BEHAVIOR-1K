# Memory Bench Demo：Key + 干扰 + Query 拼接

> 本文是 Memory Bench 的首个可跑通 demo 方案，取代原来的 `architecture.md`、`mug_in_top_cabinet_pipeline.md`、`mug_in_top_cabinet_tasks.md`。demo 跑通后再规划完整 benchmark。
> 已实现：「吊柜里的杯子」的 BDDL、任务插件、实例准备脚本（`src/memory_bench/tasks/mug_in_top_cabinet/README.md`、`scripts/prepare_teleop.sh`）。其余是本文的设计，尚未实现。

## 0. 目标与范围

demo 产出一个小规模的 LeRobot 数据集。每条 episode 是同一场景里一条连贯的长轨迹，由三个任务段组成：一个 Key、一个干扰（distractor）、一个 Query。Key 和干扰的先后顺序随机，Query 总在最后。段与段之间插入合成的衔接轨迹（bridge），所以整条轨迹里机器人没有瞬移。

demo 要验证四件事：

1. 段接口：任何一段的首尾都满足统一的机器人和物体约束，因此段可以按任意合法顺序拼接。
2. 衔接合成：在两段之间自动生成「收臂、导航、展臂」的轨迹，动作格式与遥操数据相同。
3. 离线渲染：拼好的 episode 用类似 `OmniGibson/scripts/learning/replay_obs.py` 的脚本逐帧回放渲染，输出 VLA 数据集和注释文件，注释里标出每段的角色、指令和帧区间。
4. 数据组织：沿用 BEHAVIOR 的「场景 → 活动模板 → 多个实例」组织，训练实例遥操采集，测试实例用于评测。

接口设计要直接支持后续扩展：多个 Key、多个干扰、Query 穿插，以及「干扰段移动了哪些物体」这类动态物体追踪的记忆问题（第 10 节）。demo 本身只做一个 Key、一个干扰、一个 Query。

## 1. 术语

| 术语 | 含义 |
|---|---|
| 段（segment） | 一份 HDF5 录制里的一个帧区间，加上它的角色、指令和接口信息。段是拼接的最小单位 |
| Key | 需要被记住的操作，例如把杯子放进某个吊柜。来自自己的 JoyLo 遥操 |
| 干扰（distractor） | 与 Key 无关的家务。来自 2026 challenge 的原始 demo |
| Query | 依赖 Key 记忆的任务，例如把杯子取回岛台。与对应的 Key 在同一次遥操录制里 |
| 衔接（bridge） | 两段之间合成的过渡轨迹，存成与遥操同格式的 HDF5 |
| 实例（instance） | BEHAVIOR 意义下的任务实例：活动模板加一份 TRO 状态文件，决定初始世界和机器人起点 |
| 配方（recipe） | 一条 episode 的完整描述：实例、各段及顺序、各衔接轨迹 |

## 2. 一条 episode 的组成

两种合法顺序，每条录制可以各生成一条：

```text
顺序 KDQ: [Key] → bridge → [干扰] → bridge → [Query]
顺序 DKQ: [干扰] → bridge → [Key] → bridge → [Query]
```

KDQ 的记忆距离是整段干扰；DKQ 的记忆距离只有一段衔接，可以作为短记忆对照。两条 episode 来自同一份录制，必须在同一个数据划分里。

| 段 | 来源 | 大致时长（30 Hz） |
|---|---|---|
| Key | JoyLo 遥操：放杯子进目标柜并关门 | 1–2 分钟 |
| 干扰 | 2026 原始 demo，首选 `tidying_bedroom`（task 18），备选 `outfit_a_basic_toolbox`（19）、`putting_away_toys`（54） | 约 6 分钟 |
| Query | 同一次遥操：把杯子取回岛台并关门 | 1–2 分钟 |
| bridge ×2 | 合成 | 每段 5–30 秒 |

三个候选干扰都在 `house_single_floor`，设计上避开厨房吊柜和岛台；能不能用由第 3 节的检查决定，不按任务名保证。

## 3. 段接口

每一段都要满足同一套首尾约束，拼接器只看这些约束，不关心段的来源和角色。这是后续支持任意顺序、多 Key、多干扰的基础。

### 3.1 机器人接口

停靠姿态（home）取 R1Pro 的复位关节值，与 JoyLo 启动姿态和 `OmniGibson/omnigibson/eval/r1pro.yaml` 的 `reset_joint_pos` 一致：躯干 `[1.025, -1.45, -0.47, 0]`，双臂全 0，夹爪张开。

每段的第一帧（入口）和最后一帧（出口）必须满足：

- 硬约束：两个夹爪张开且没有抓着物体；机器人基本静止（底盘和关节速度低于阈值，录制时要求静止约 1 秒）。
- 软约束：躯干和手臂接近停靠姿态（初始容差每个关节 0.3 rad，实测后冻结）。超出容差但不太远时，衔接轨迹负责插值过去；插值不能无碰撞完成就判为不兼容。
- 底盘位姿不做要求，由衔接轨迹导航过去。

遥操段靠录制规范保证（第 6.2 节）。2026 段的入口就是该 demo 的初始状态（复位姿态），出口是 demo 结束时的姿态，通常不在停靠姿态，由衔接轨迹的收臂阶段处理；结束时手里还抓着东西的 demo 不能用。

### 3.2 物体接口

对每一段，先只解码不渲染地扫一遍帧区间，得到写集合 `W(s)`：区间内位姿或关节变化超过阈值的物体（不含机器人）。同时记下这些物体在段首帧和段末帧的状态（前置状态和后置状态）。

拼接时要满足：

1. 写集合不相交：不同段的写集合两两不相交。唯一例外是声明了依赖的段，例如 Query 依赖它的 Key，两者共享杯子和目标柜。
2. 前置状态一致：每段写集合里的物体，在拼接后的世界里轮到该段时的状态，要和该段首帧记录的状态一致（位置 2 cm、角度 5°、关节 0.05 内）。
3. 空间不冲突：拼接后的世界里，有些物体的状态和某段录制时不同（被别的段移动过）。该段机器人扫过的区域和它移动的物体，不能和这些物体在拼接世界里的位置重叠（逐帧 AABB 检查，留 5 cm 余量）。
4. 保护对象：任务插件声明的保护对象（本任务是杯子、三个吊柜、岛台）不能出现在任何干扰段的写集合里。
5. 依赖内部连续：Query 和 Key 在同一份录制里，两者之间被丢弃的录制帧 `[key_end, query_start)` 的写集合只能是机器人。

由于所有段都从同一个初始世界录制（第 5 节），写集合不相交时条件 2 基本自动成立，检查主要用来发现录制里的意外碰撞。

### 3.3 段清单

`make_segments.py` 为每一段生成一份段清单，之后的编排和渲染只读段清单：

```yaml
# $MB_ROOT/segments/mug_in_top_cabinet/i0003_d0_key.yaml
id: mug_in_top_cabinet/i0003_d0/key
role: key
memory_task: mug_in_top_cabinet
instance_id: 3
source: {hdf5: recordings/mug_in_top_cabinet.hdf5, demo: 0, frames: [40, 2610]}
instruction: "Put the mug from the kitchen island into a top cabinet and close the door."
robot_entry: {base: [5.18, 1.40, 0.82], arm_trunk_dev: 0.04, gripper_empty: true}
robot_exit:  {base: [4.75, -0.48, 0.00], arm_trunk_dev: 0.11, gripper_empty: true}
write_set: [mug_ntgftr_0, top_cabinet_lkxmne_1]
depends_on: null
answer: {target_cabinet: top_cabinet_lkxmne_1}
```

Query 段写 `depends_on: mug_in_top_cabinet/i0003_d0/key`。干扰段的 `instruction` 用 2026 任务名，下划线换成空格（与 `LeRobotDataWrapper` 默认一致），`id` 形如 `b1k/tidying_bedroom/episode_0018XXXX`。

### 3.4 排序规则

编排器把一条 episode 要用的段放进一个集合，按以下约束随机采样顺序：依赖段排在被依赖段之后；demo 阶段 Query 固定在最后。然后检查 3.2 的条件和衔接可行性，不通过就换一个顺序或丢弃这个组合。顺序的随机种子写进配方。

## 4. 衔接轨迹合成

### 4.1 轨迹构成

在前一段出口和后一段入口之间合成三个阶段：

1. 收臂：躯干和手臂从前一段出口姿态插值到停靠姿态，底盘不动。
2. 导航：底盘从前一段出口位姿开到后一段入口位姿。路径用场景的可通行地图规划（`scene.get_shortest_path(..., entire_path=True, robot=robot)`，按机器人尺寸腐蚀），先原地转向行进方向，沿路径行驶，到达后转到目标朝向。头部相机因此始终朝前。
3. 展臂：从停靠姿态插值到后一段入口的躯干和手臂姿态。

速度上限取遥操常见值并低于控制器限幅：底盘 0.4 m/s、0.6 rad/s，关节 0.5 rad/s，梯形速度曲线。

### 4.2 执行与录制

衔接轨迹在带物理的环境里闭环执行，而不是直接写位姿：

- 环境与遥操相同（R1Pro、2026 控制器配置），世界状态设为拼接到这一点时的状态。非机器人物体设为 kinematic，不会被推动，但碰撞照常检测。
- 每步动作与遥操格式一致（23 维）：底盘是机器人坐标系下归一化的速度指令，由跟踪路径前视点的控制器算出；躯干和手臂是插值得到的绝对关节目标；夹爪发张开指令。
- 用 `DataCollectionWrapper` 录成 HDF5，格式与 JoyLo 录制相同。

这样衔接轨迹的动作标签能在评测用的控制器下复现对应的运动，构建阶段也可以把它当成普通段处理（写集合只有机器人）。

验收条件：机器人与除地面外的任何物体没有接触；结束时底盘与目标位姿相差不超过 3 cm、3°，关节在 0.05 rad 内。残差会在下一段首帧表现为一次小跳变，记入注释。规划失败或碰撞时，加大腐蚀半径重试；仍失败就判这个顺序不可行。

如果物理闭环执行在 demo 阶段难以调稳，退路是运动学生成：直接按插值写位姿，动作由相邻帧差分得到。注释里记录用了哪种方式。

## 5. 数据组织

### 5.1 沿用 BEHAVIOR 的实例组织

任务实例数据集是 `datasets/memory-bench-task-instances/`，结构与官方 `2026-challenge-task-instances/` 相同（官方目录只读），采样、遥操、编排、渲染、评测进程都设置 `OMNIGIBSON_TASK_INSTANCES_DATASET=memory-bench-task-instances`。

- 活动模板：Key 活动 `mb_mug_into_top_cabinet` 的完整模板 JSON。`add-distractor` 把干扰 demo 录制时场景里模板没有的物体连同初始状态并入模板（用 `merge_scene_files(scene_a=demo 场景, scene_b=模板)`，冲突时以模板为准；调用前深拷贝，它会原地修改输入）。同名物体模型不同时报错。
- 实例：每个实例一份 TRO 文件，记录杯子位姿、机器人起点和变体绑定。实例编号沿用 BEHAVIOR 约定：训练实例从 1 开始，测试实例从 301 开始。
- 变体：A/B/C 在训练集和测试集内各自均衡，划分冻结在 `metadata/mb_mug_into_top_cabinet_variants.json`。

TRO 文件只能装 BDDL 里声明的物体，所以干扰物体的初始状态只能放在模板里。demo 阶段每个干扰活动只用一条 2026 demo，所有实例共用它。不同实例用不同干扰 demo 需要给 JoyLo 加一个按实例加载额外物体状态的小补丁，放到后续（第 10 节）。

模板并入干扰物体之后，已有实例要逐个加载、静置，确认杯子、机器人起点和新物体没有冲突，有冲突的实例重新采样。现有的实例 1–3 和录制 `recordings/mug_in_top_cabinet.hdf5` 是在并入之前做的，只用于开发联调。

### 5.2 训练与测试

| 划分 | 实例 | 采集 | 用途 |
|---|---|---|---|
| 训练 | 1–6（每个变体 2 个） | 每个实例一次 Key+Query 遥操 | 渲染出 KDQ、DKQ 两条 episode |
| 测试 | 301–303（每个变体 1 个） | 同样遥操 | Key 和干扰作为历史；Query 由策略执行，遥操的 Query 只用于 replay 检查，不发布 |

测试也要遥操 Key，因为评测时策略需要 Key 段的历史帧。demo 阶段训练和测试共用同一条干扰 demo。

### 5.3 目录

```text
memory_bench/
├── docs/demo.md
├── configs/demo_v0.yaml        # 本 demo 的总配置：任务、干扰 demo、实例划分、排序规则、衔接参数
├── scripts/
│   ├── prepare_task.py         # 已有；新增 add-distractor，assign-variants 改为按划分均衡
│   ├── prepare_teleop.sh       # 已有
│   ├── make_segments.py        # 录制 + 切分标记 → 段清单（写集合、接口检查）
│   ├── compose_episodes.py     # 采样顺序、兼容性检查、合成衔接 → 配方
│   ├── render_episodes.py      # 配方 → LeRobot 数据集 + 注释，仿照 replay_obs.py
│   └── eval_query.py           # 只执行 Query 的评测
└── src/memory_bench/
    ├── paths.py
    ├── segments.py             # 段清单、写集合扫描、接口和兼容性检查
    ├── bridge.py               # 衔接轨迹规划与闭环执行
    ├── playback.py             # 多段回放，继承 LeRobotPlaybackWrapper
    └── tasks/
        ├── base.py             # MemoryTask：新增 Key 指令、保护对象、Query 起点、检查和计分
        └── mug_in_top_cabinet/
```

```text
$MB_ROOT/
├── external/2026-challenge-rawdata/task-0018/episode_0018XXXX.hdf5   # 只读
├── recordings/mug_in_top_cabinet.hdf5         # JoyLo 录制
├── recordings/mug_in_top_cabinet_marks.yaml   # 每条录制的切分帧
├── segments/<memory_task 或 b1k>/<...>.yaml   # 段清单
├── bridges/demo_v0/episode_XXXXXXXX/bridge_XX.hdf5
├── recipes/demo_v0/episode_XXXXXXXX.yaml
├── lerobot/demo_v0/                           # 最终产物
└── eval_runs/<run_id>/
```

## 6. 流程

```text
S1 场景与实例 → S2 遥操 → S3 切段 → S4 编排与衔接 → S5 渲染导出 → S6 评测
```

### 6.1 S1 场景与实例

1. 已有：`prepare_task.py init`、模板采样、实例采样、机器人起点采样、`register-joylo`（见任务 README）。
2. 下载一条候选干扰 demo，只读检查：config、`scene_file`、机器人名、fps、动作维度、控制器配置、`transitions` 为空、没有粒子系统、结束时没抓着东西。
3. `prepare_task.py mug_in_top_cabinet add-distractor --hdf5 <demo>`：并入干扰物体。同时更新或删除 `-partial_rooms` 模板，避免两份不一致。
4. 采样训练实例 1–6 和测试实例 301–303，`assign-variants` 在两个划分内分别均衡。

### 6.2 S2 遥操规范

Key 和 Query 在 JoyLo 的同一次录制里完成（JoyLo 只能从实例初始状态启动），加载全场景（`--partial-load False`），带 `--instance-id`。开录前按变体告诉操作员目标柜是左、中还是右。每条录制按顺序：

1. 开始：恢复控制后，主手臂放在停靠姿态，静止约 1 秒。
2. Key：拿起杯子，只打开目标柜，放入、松手，把门关严。
3. 收臂：主手臂回到停靠姿态，夹爪张开，静止约 1 秒。Key 段到此结束。
4. 去 Query 起点：底盘开到厨房里固定的 Query 起始位姿（所有变体相同，例如岛台和吊柜之间、面朝吊柜的中线），静止约 1 秒。Query 段从这里开始。
5. Query：取回杯子放到岛台，关好柜门，收臂，静止约 1 秒后保存。

第 4 步之所以保留：Query 起点的位置和姿态如果随变体变化，当前画面就会泄露答案。第 3 到第 4 步之间的录制帧在拼接时被丢弃，由衔接轨迹替代。起始位置靠目测，容差由检查兜底；不要在场景里放标记物，它会被渲染进数据。

录完在回放里标出四个切分帧，写进 marks 文件：

```yaml
# $MB_ROOT/recordings/mug_in_top_cabinet_marks.yaml
0: {instance_id: 3, key: [40, 2610], query: [2900, 4420]}
```

切分帧都选在静止期间。最后一个动作执行后的状态不会存储，所以第 5 步最后要静止，让 Query 目标在最后存储的帧里已经成立。后续可以根据静止和夹爪状态自动提议切分帧，demo 阶段人工标注。

### 6.3 S3 切段

`make_segments.py` 读录制和 marks，为每条录制生成 Key 和 Query 两份段清单，为每条干扰 demo 生成一份；扫描写集合，检查 3.1 的机器人接口和任务检查（第 8.3 节），不通过就报错，需要重录或换 demo。

### 6.4 S4 编排与衔接

`compose_episodes.py` 对每条录制：

1. 取 Key、Query 和干扰段，按 3.4 采样顺序（demo 阶段直接生成 KDQ 和 DKQ 两条）。
2. 在一个带物理的环境里，从实例初始世界开始，按顺序把每段写集合物体设成该段的后置状态，得到每个拼接点的世界；检查 3.2 的条件。
3. 在每个拼接点按第 4 节合成衔接轨迹，存为 `bridges/.../bridge_XX.hdf5`。
4. 写配方：

```yaml
# $MB_ROOT/recipes/demo_v0/episode_00000012.yaml
episode_index: 12
split: train
memory_task: mug_in_top_cabinet
instance_id: 3
order: KDQ
order_seed: 7
segments:
  - {segment: mug_in_top_cabinet/i0003_d0/key}
  - {bridge: bridges/demo_v0/episode_00000012/bridge_00.hdf5}
  - {segment: b1k/tidying_bedroom/episode_0018XXXX}
  - {bridge: bridges/demo_v0/episode_00000012/bridge_01.hdf5}
  - {segment: mug_in_top_cabinet/i0003_d0/query}
```

### 6.5 S5 离线渲染导出

`render_episodes.py` 仿照 `replay_obs.py`：关闭 transition rules 和机器人控制，物体 visual-only，相机和观测设置相同（头部 720×720、两个手腕 480×480，RGB、深度和 R1Pro 本体感知）。实现上是一个继承 `LeRobotPlaybackWrapper` 的类，重写 `playback_episode` 以遍历配方里的多段。

1. 用 Key 录制的 `scene_file` 作为初始世界创建环境（它就是该实例的完整初始场景，含干扰物体），加载全部房间。各段录制的 non-kinematic 状态列表用 `_add_recorded_non_kin_states_to_scene_file` 合并；回放每段前用 `_align_scene_object_states_with_recorded_schema` 切换到该段的 schema。2026 段的机器人名与 JoyLo 不同时，解码时做别名映射，并跳过 `init_metadata`。
2. 逐段逐帧解码，只把机器人和该段写集合里的物体写入场景，渲染观测，和该帧动作一起追加到同一条 LeRobot episode。每帧的 `task` 是该段指令；衔接帧的 `task` 用下一段的指令。
3. 每段第一帧写入后多渲染几次，再把完整场景存为 `segment_states/episode_XXXXXXXX/seg_XX.json`，供评测从该段起点加载。
4. 整条 episode 保存一次，再写注释文件。

帧和动作的对齐沿用上游约定：第 i 帧观测是执行第 i 个动作之前的状态。

## 7. 输出格式

```text
$MB_ROOT/lerobot/demo_v0/
├── meta/                        # LeRobot v3 标准元数据
├── data/                        # parquet：observation.state、action、task_index 等
├── videos/                      # 头部和左右手腕相机的 RGB 与深度
├── annotations/episode_XXXXXXXX.json
└── segment_states/episode_XXXXXXXX/seg_XX.json
```

`meta/`、`data/`、`videos/` 由 `LeRobotDataWrapper` 原样写出，读 2026 demo 数据集的 dataloader 可以直接读。`meta/info.json` 已有 OmniGibson git hash，再补上 memory_bench 的 git hash 和模板文件哈希。

注释文件示例（KDQ 顺序）：

```json
{
  "episode_index": 12,
  "split": "train",
  "memory_task": "mug_in_top_cabinet",
  "instance_id": 3,
  "variant": "B",
  "order": "KDQ",
  "fps": 30,
  "answer": {"target_cabinet": "top_cabinet_lkxmne_1"},
  "memory_span": {"key_end_frame": 2570, "query_start_frame": 15120},
  "segments": [
    {"role": "key", "id": "mug_in_top_cabinet/i0003_d0/key",
     "instruction": "Put the mug from the kitchen island into a top cabinet and close the door.",
     "frame_range": [0, 2570],
     "source": {"type": "teleop", "hdf5": "recordings/mug_in_top_cabinet.hdf5", "demo": 0, "frames": [40, 2610]},
     "write_set": ["mug_ntgftr_0", "top_cabinet_lkxmne_1"],
     "start_state": "segment_states/episode_00000012/seg_00.json"},
    {"role": "bridge", "frame_range": [2570, 3020], "bridge_to": "distractor",
     "source": {"type": "bridge", "hdf5": "bridges/demo_v0/episode_00000012/bridge_00.hdf5", "mode": "physics"},
     "end_residual": {"pos_m": 0.012, "yaw_deg": 0.8}},
    {"role": "distractor", "id": "b1k/tidying_bedroom/episode_0018XXXX",
     "instruction": "tidying bedroom",
     "frame_range": [3020, 14020],
     "source": {"type": "b1k_2026", "hdf5": "external/2026-challenge-rawdata/task-0018/episode_0018XXXX.hdf5", "demo": 0, "frames": [0, 11000]},
     "write_set": ["..."],
     "start_state": "segment_states/episode_00000012/seg_02.json"},
    {"role": "bridge", "frame_range": [14020, 15120], "bridge_to": "query", "...": "..."},
    {"role": "query", "id": "mug_in_top_cabinet/i0003_d0/query", "depends_on": "mug_in_top_cabinet/i0003_d0/key",
     "instruction": "Retrieve the mug and place it on the kitchen island.",
     "frame_range": [15120, 16640],
     "source": {"type": "teleop", "hdf5": "recordings/mug_in_top_cabinet.hdf5", "demo": 0, "frames": [2900, 4420]},
     "start_state": "segment_states/episode_00000012/seg_04.json"}
  ],
  "object_events": [
    {"object": "mug_ntgftr_0", "segment": 0, "frames": [310, 2200], "from": "bar_udatjt_0", "to": "top_cabinet_lkxmne_1"}
  ]
}
```

字段说明：

- `frame_range` 是本 episode 里的 `[start, end)`，与 LeRobot 的 `frame_index` 对齐；所有段首尾相接，总帧数等于各段长度之和。
- `source` 加配方可以复现整条 episode。
- `memory_span` 给出 Key 结束到 Query 开始的帧，方便按记忆距离分析。
- `object_events` 记录每段写集合里物体的移动（起止帧、起止位置所在的物体或房间），由写集合扫描自动得到。demo 只用于检查，后续动态物体追踪的 Query 直接从这里出答案。
- `answer` 只给评测和分析用。Key 指令不写具体柜子，否则模型从历史文本里就能读到答案。

## 8. 任务「吊柜里的杯子」

### 8.1 设定

机器人先把岛台上的杯子放进三个同款吊柜之一并关门（Key），做一段无关家务（干扰），最后收到「把杯子拿回岛台」（Query）。主指标是严格成功：杯子放回岛台、柜门都关上，并且全程没打开过错误的柜子。这个指标区分「记得放在哪」和「挨个找」。场景 `house_single_floor`，机器人 R1Pro。

| 物体 | 名称 | 参考位置 (x, y, z)，m | 用途 |
|---|---|---|---|
| 吊柜 A | `top_cabinet_lkxmne_0` | (4.01, -1.62, 1.79) | 同款双开门吊柜 |
| 吊柜 B | `top_cabinet_lkxmne_1` | (4.01, -0.48, 1.79) | 同上 |
| 吊柜 C | `top_cabinet_lkxmne_2` | (4.01, 0.65, 1.79) | 同上 |
| 岛台 | `bar_udatjt_0` | (7.29, 0.21, 0.66) | Key 起点、Query 终点 |

变体 A/B/C 把 Key 目标柜 `cabinet.n.01_1` 分别绑定到三个吊柜，绑定写在实例 TRO 里。左/中/右的叫法要在 JoyLo 的固定厨房视角下确认。三个吊柜的放入和取出是否都稳定还没验证；不行时先换杯子模型，再考虑改用三个同款下柜。

BDDL 已在 `bddl3/bddl/activity_definitions/` 下：Key `mb_mug_into_top_cabinet`（杯子从岛台到 `cabinet.n.01_1`，三柜关闭），Query `mb_retrieve_mug_from_top_cabinet`（反过来）。Query 不单独采样，起点就是渲染时保存的 Query 段起点 JSON。

### 8.2 指令

| 段 | 每帧的 `task` |
|---|---|
| Key | "Put the mug from the kitchen island into a top cabinet and close the door." |
| 干扰 | 2026 任务名，例如 "tidying bedroom" |
| Query | "Retrieve the mug and place it on the kitchen island." |
| 衔接 | 下一段的指令 |

### 8.3 任务检查

由任务插件在 S3 和评测时执行，直接重新评价谓词和关节，不读缓存的 `task.success`：

- Key 段末帧 Key 目标成立，非目标柜在 Key 段内从没打开过。
- Query 段首帧：底盘在 Query 起始位姿附近（初始容差 0.3 m、15°）；杯子在目标柜里；三扇柜门关严（关节值低于关闭阈值，不只是 `Open` 为假，避免门缝泄露答案）；Query 目标尚未满足。
- Query 段末帧 Query 目标成立。
- 干扰段写集合里的物体最终不在厨房吊柜和岛台附近，也不进入 Query 操作区域。

### 8.4 评测（只执行 Query）

`eval_query.py` 对每条测试 episode：

1. 从注释读出 Query 段的 `start_state`、指令和历史区间 `[0, query_start)`。
2. 创建单环境，机器人和控制器与采集时一致，`scene_file` 为 Query 起点 JSON，任务为 Query 活动的 `BehaviorTask`（`online_object_sampling=False`、`include_obs=False`）。正常物理下静置几步，用 8.3 重新检查起点。
3. 策略接口沿用 `omnigibson.eval.policies` 的 `LocalPolicy` / `WebsocketPolicy`，观测格式与 2026 评测相同；每步额外附上 Query 指令和历史引用（数据集名、`episode_index`、历史帧区间），策略自己读历史帧。
4. 每个控制步记录三扇柜门的开合事件，直到成功或超时。

不直接继承官方 `BatchedEvaluator`：它只按 TRO 恢复任务相关物体，而 Query 起点还包含干扰段移动过的物体，需要加载整场景。测试集只发布历史帧、Query 起点 JSON 和 Query 指令。

| 指标 | 定义 |
|---|---|
| 严格成功（主指标） | Query 目标成立、杯子已松开且稳定，并且从未打开任何非目标柜 |
| 宽松成功 | Query 目标成立、杯子已松开且稳定 |
| 首选正确 | 第一次开柜事件只指向目标柜；同一步多扇门打开记为并列 |
| 错误开柜次数 | 非目标柜从关到开的次数 |

工程检查用两个策略：`replay` 按数据集里 Query 段的动作开环复演，记录成功率；`hold` 保持当前关节目标、底盘零速度，不应运动也不应成功（不能简单发全零动作）。

## 9. 实施步骤与验收

| 步骤 | 内容 | 验收 |
|---|---|---|
| D0 联调 | 用现有录制（无干扰）切出 Key 和 Query，合成一段衔接，渲染两段 episode | `LeRobotDataset` 能加载；总帧数等于各段之和；Key/衔接/Query 交界处视频无跳变；`seg_XX.json` 能在新进程加载且与对应帧一致 |
| D1 场景 | 选定并检查一条干扰 demo，`add-distractor`，采样训练和测试实例 | 模板只增不改；已有物体状态和绑定逐字段相同；所有实例静置后检查通过 |
| D2 采集 | 三个吊柜放入取出验证；训练 6 条、测试 3 条 Key+Query 录制，标好切分帧 | 每条录制通过 3.1 机器人接口和 8.3 任务检查 |
| D3 拼接 | `make_segments`、`compose_episodes`、`render_episodes` 跑通 KDQ 和 DKQ | 3.2 检查全部通过；衔接无接触、残差在容差内；人工抽查每个交界处和干扰段视频；同一配方重建结果不变 |
| D4 评测 | `eval_query.py` 在测试 episode 上运行 | 策略只收到允许的输入；`replay` 能完成 Query；`hold` 不成功；直接设置柜门关节时监控事件符合定义 |

D0 不依赖 2026 数据和新录制，可以最先做，用来打通段清单、衔接合成和多段渲染。

## 10. 后续扩展

段接口按扩展需求设计，下面这些在 demo 之后逐步加入，不需要改接口：

- 多个干扰：段集合里放多段干扰，排序器随机排列，写集合两两不相交即可。用不同数量的干扰控制记忆距离。
- 多个 Key 和 Query：每对 Key+Query 声明依赖；排序约束从「Query 在最后」放宽为「Query 在它的 Key 之后」，可以穿插。
- 动态物体追踪：Query 问干扰段移动过的物体（例如「把刚才收起来的书拿出来」），答案由 `object_events` 自动给出；或者同一物体被多个 Key 先后移动，要求记住最后位置。后者要求后一个 Key 从前一个 Key 的结束状态开始录制，需要 JoyLo 支持从 `segment_states` 里的场景 JSON 启动。
- 每个实例不同的干扰 demo：给 JoyLo 加按实例加载额外物体状态的小补丁（例如 TRO 旁边放一份 `-extra_state.json`），或直接用实例的完整场景 JSON 启动。
- 从任意段开始的评测：从 `seg_k` 起点加载，`[0, start_k)` 作为历史，依次下发后续各段指令；干扰段完成与否用对应 2026 活动的 BDDL 判断或按时间预算切换。
- 规模化：批量配方、按录制分组划分、有历史/无历史/换历史三组对照，确定数据规模后再规划完整 benchmark。

## 11. 实现注意

1. 渲染进程和 `replay_obs.py` 一样是 visual-only 的；段起点 JSON 要在评测进程里用正常物理加载、静置后再检查一次。
2. 段首帧要先写入状态再保存 JSON，并多渲染几次，否则存下的是上一段末尾的世界或滞后的画面。
3. 最后一个动作执行后的状态没有存储，不要把段的最后存储帧当成真正的终态；切分点要求静止就是为此。
4. `BehaviorTask` 的物体绑定从起点 JSON 的 metadata 读取，加载后确认 scope 指向正确的柜子。
5. `og.sim.step()` 之后缓存的 `task.success` 不会更新，检查时直接重新评价谓词。
6. 原则上不修改 JoyLo 和 OmniGibson，新代码放在 `memory_bench/` 下；确实要改上游时单独提交小补丁。
7. 所有仿真命令使用 `behavior` 环境，测试设置 `OMNIGIBSON_HEADLESS=1`。正式发布前核对 2026 数据的许可和发布范围。
