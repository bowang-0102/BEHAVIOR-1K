# Memory Bench 代码流程

`memory_bench` 的核心是构建记忆任务数据：采集 Key → Query 的连续操作，再加入干扰任务，通过物理导航连接各片段，最终重新渲染并导出 LeRobot 数据。

- **K（Key）**：提供需要记住的信息。
- **I（Intervent）**：插入的干扰任务。
- **Q（Query）**：依赖前面信息完成的任务。
- **B（Bridge）**：连接两个任务的机器人导航片段。

## 整体流程

```mermaid
flowchart TD
    CFG["configs/*.yaml<br/>场景、任务、依赖、顺序、导出配置"]
    TASK["tasks/*.py → MemoryTask<br/>变体、物体绑定、指令、前后置条件"]
    SESSION["session.py → Session.load / validate<br/>校验配置，通过 session_id 获取任务"]

    CFG --> SESSION
    TASK --> SESSION

    subgraph PREP["① 准备实例：prepare_session.sh"]
        INIT["prepare_task.init<br/>注册采样任务，冻结 session 配置"]
        SAMPLE["OmniGibson 采样脚本<br/>生成 K/Q 模板、首个 Key 的 TRO 和机器人姿态"]
        ASSIGN["prepare_task.assign_variants<br/>分配 case，统一跨任务物体身份，冻结绑定"]
        SCENE["sample_instances.sample_instances<br/>准备完整场景，采样 Q 起点<br/>检查初态、稳定性和路线"]
        SCAN["processing/scan_recordings.py<br/>扫描并检查已有 I 录制"]
        ASSEMBLE["prepare_task.assemble_instances<br/>合并素材、场景与姿态<br/>写入 instances.json、assets.json"]

        INIT --> SAMPLE --> ASSIGN --> SCENE --> SCAN --> ASSEMBLE
    end

    SESSION --> INIT
    RAW[("已有干扰任务 HDF5")] --> SCAN

    subgraph COLLECT["② 联合采集：collect.sh + JoyLo"]
        LOAD["collect_session / SessionCollector<br/>加载实例场景"]
        TELEOP["按配置顺序遥操各 Key，最后执行 Q<br/>N 键触发边界检查<br/>任务间自动导航、同步控制器"]
        JOINT[("联合录制 HDF5<br/>marks.yaml + 入口/出口快照")]

        LOAD --> TELEOP --> JOINT
    end

    ASSEMBLE --> LOAD

    subgraph PROCESS["③ 切段与素材匹配"]
        NORMAL["normalize_recording<br/>统一逐帧状态格式，补齐终态"]
        CUT["make_joint_segments → make_segment<br/>按 marks 切出 K/Q<br/>记录读写集合、边界和源文件哈希"]
        IPREP["processing/prepare_intervent.py<br/>规范化并审计 I 素材<br/>筛选足量有效片段"]
        PAIR["episodes/pair_segments.py → match_sources<br/>K/Q 与 I 一对一匹配<br/>独立分配预设任务顺序"]
        GROUP[("material_groups.json")]

        NORMAL --> CUT --> PAIR
        IPREP --> PAIR --> GROUP
    end

    JOINT --> NORMAL
    SCAN --> IPREP
    ASSEMBLE --> IPREP
    ASSEMBLE --> PAIR

    subgraph EXPORT["④ 拼接、渲染与导出：export_session.sh"]
        COMPOSE["compose_batch → compose_episode<br/>校验素材并合成物理 Bridge<br/>按读写集合继承世界状态"]
        EPISODE[("episode HDF5 + 场景 JSON<br/>recipe + 片段与记忆跨度注释")]
        RENDER["render_episode<br/>逐帧恢复已拼接状态并渲染<br/>写入观测、原始动作和当前指令"]
        DATA[("LeRobot 数据集<br/>视频、观测、动作、注释")]
        READBACK["readback<br/>检查帧数、动作、指令、时间戳和视频"]

        COMPOSE --> EPISODE --> RENDER --> DATA --> READBACK
    end

    GROUP --> COMPOSE
```

准备阶段会标记实例是否仍需物理标定；采集入口还会检查实例状态。采集时只录制 K/Q，最终任务顺序中的 I 和 B 在后处理阶段加入。

## 核心拼接逻辑

这部分对应 [compose_episodes.py](../episodes/compose_episodes.py)。每个片段只更新自己的 `write` 集合和机器人状态，其他物体继续继承此前结果。

```mermaid
flowchart TD
    START["compose_episode<br/>读取一组实例、素材与 task_order"]
    CHECK["校验任务顺序、场景哈希、素材哈希<br/>检查机器人接口、物体绑定及资产隔离"]
    INIT["合入 I 所需资产<br/>机器人初态对齐第一个片段"]
    NEXT["connect_segments<br/>依次处理下一个任务片段"]
    READ{"当前世界是否满足<br/>片段 read 集合的入口状态？"}
    FIRST{"是否为第一个片段？"}

    START --> CHECK --> INIT --> NEXT --> READ
    READ -- 否 --> FAIL["终止组装并保存失败报告"]
    READ -- 是 --> FIRST
    FIRST -- 是 --> ROBOT["检查机器人与首段入口一致"]
    FIRST -- 否 --> STOW

    subgraph BRIDGE["synthesize_bridge：在继承后的世界中生成导航"]
        STOW["收起机械臂<br/>move_joints"]
        NAV["规划可通行路线并执行底盘动作<br/>drive_to_pose：A* + 闭环控制"]
        READY["恢复下一片段的关节姿态<br/>检查空手、碰撞、稳定性和终点"]
        STOW --> NAV --> READY
    end

    READY --> GUARD["确认 Bridge 保持非机器人物体状态<br/>保存 Bridge 动作与边界"]
    ROBOT --> MERGE
    GUARD --> MERGE["继承任务出口<br/>仅更新 write 集合和机器人状态"]
    MERGE --> MORE{"还有任务片段？"}
    MORE -- 是 --> NEXT
    MORE -- 否 --> WRITE["write_sequence<br/>逐帧合并任务与 Bridge<br/>检查 I/B 全程未改变保护物体"]
    WRITE --> OUTPUT["保存 episode、recipe<br/>计算各 Key 到 Q 的记忆跨度"]

    BRIDGE -. 检查失败 .-> FAIL
```

一条典型产物是 `K → B → I₁ → B → I₂ → B → Q`；也允许 I 出现在 K 前面，顺序须满足配置中的依赖，Q 始终在最后。

Bridge 使用物理仿真执行动作；最终渲染按记录状态逐帧恢复，不推进物理。

## 主要代码入口

| 文件 | 职责 |
| --- | --- |
| [session.py](../session.py) | 加载配置、查找任务、校验依赖和顺序 |
| [tasks/base_task.py](../tasks/base_task.py) | 任务变体、绑定、指令及实时条件检查 |
| [collection/prepare_task.py](../collection/prepare_task.py) | 初始化、变体分配、实例组装 |
| [collection/sample_instances.py](../collection/sample_instances.py) | 完整场景与入口姿态采样 |
| [collection/collect_session.py](../collection/collect_session.py) | 联合遥操采集与片段边界管理 |
| [utils/recordings.py](../utils/recordings.py) | 录制元数据、逐帧状态读取与素材检查 |
| [utils/segment_manifest.py](../utils/segment_manifest.py) | 片段加载、边界对齐、接口及源文件哈希校验 |
| [utils/scene_state.py](../utils/scene_state.py) | 场景状态访问、比较与合并 |
| [processing/scan_recordings.py](../processing/scan_recordings.py) | I 素材扫描与清单生成 |
| [processing/split_recordings.py](../processing/split_recordings.py) | 调用规范化与 K/Q 切段 |
| [processing/prepare_intervent.py](../processing/prepare_intervent.py) | I 素材规范化、审计与筛选 |
| [episodes/pair_segments.py](../episodes/pair_segments.py) | 分配顺序并保存素材组 |
| [episodes/compose_episodes.py](../episodes/compose_episodes.py) | 状态继承、Bridge 连接与 episode 拼接 |
| [episodes/synthesize_bridges.py](../episodes/synthesize_bridges.py) | 物理导航片段合成与世界状态保护 |
| [navigation.py](../navigation.py) | 路径规划、底盘闭环控制和关节运动 |
| [episodes/render_episodes.py](../episodes/render_episodes.py) | 状态回放渲染、LeRobot 导出及读回检查 |
| [tools/debug_intervents.py](../tools/debug_intervents.py) | 独立 I/Bridge 拼接与导出调试 |
| [tools/audit_world.py](../tools/audit_world.py) | 继承场景中的物理可行性审计 |
