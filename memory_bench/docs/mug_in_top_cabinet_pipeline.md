# 「吊柜里的杯子」长程记忆 Benchmark：Pipeline 规划

> [架构文档](architecture.md) 是公共格式与接口的唯一约定；[工程任务](mug_in_top_cabinet_tasks.md) 给出实现顺序和验收。本篇说明任务设定、数据构建与实验设计。
> 场景 `house_single_floor`，机器人 R1Pro。任务插件和 B1–B3 准备命令已实现；通用 benchmark pipeline 尚待实现。先完成真实 Key/Query 闭环，再复用已有遥操 demo 构建干扰历史。

## 0. 首个任务要验证什么

机器人将岛台上的杯子放进三个同款吊柜之一，关门后经历与杯子无关的家务，最后收到“把杯子拿回岛台”的 Query。主指标要求取回成功且从未打开错误柜门，用于区分直接找对柜子与逐个搜索。

先证明工程闭环，再验证记忆效果：

| 阶段 | 最小范围 | 要回答的问题 |
|---|---|---|
| M0 | 一个可用模板和实例 | 能否明确绑定、导出和恢复状态？ |
| M1 | 一条真实 Key、无干扰、真实 Query | 采集、历史、Cq 与评测是否一致？ |
| M2 | 一条已有干扰 demo，先使用短片段和 cut | 稀疏状态、合成世界与统一渲染能否工作？ |
| M3 | A/B/C 配对小样本 | 历史是否提供有效信息，当前画面是否泄露答案？ |
| M4 | 三段完整干扰、约 20 分钟历史 | 数据能否批量生成，资源成本是否可接受？ |

假 Key 用于调试快照和工具，不算 M1 完成；一条 episode 的无历史结果也不能证明随机水平为 1/3。

## 1. 任务设定

### 1.1 场景中的候选物体

下表是设计时从场景文件记录的候选绑定。实施时以安装资产和模板版本复核，保存资产签名；位置不作为跨版本常量。

| 物体 | 名称 | 参考位置 (x, y, z)，m | 用途 |
|---|---|---|---|
| 吊柜 A | `top_cabinet_lkxmne_0` | (4.01, -1.62, 1.79) | 同款双开门吊柜 |
| 吊柜 B | `top_cabinet_lkxmne_1` | (4.01, -0.48, 1.79) | 同上 |
| 吊柜 C | `top_cabinet_lkxmne_2` | (4.01, 0.65, 1.79) | 同上 |
| 岛台 | `bar_udatjt_0` | (7.29, 0.21, 0.66) | Key 起点、Query 终点 |
| 下方台面 | `countertop_kelker_0` | (4.18, -0.50, 0.87) | 几何与碰撞参考 |

三个吊柜在 `kitchen_0`，沿 y 轴排列。`top_cabinet → cabinet.n.01`、`bar → countertop.n.01`、`mug → mug.n.04` 的映射需要与当前 taxonomy 核对。操作员的“左/中/右”以固定厨房视角确认，并与对象名绑定，不能随机器人朝向改变含义。

首个可操作性检查同时包含放入和取出：模型尺寸、柜门抓取、杯子释放、`inside`/`ontop` 稳定性以及机器人与灶台的碰撞。若吊柜无法稳定完成，改用三个同款下柜，并重新检查干扰依赖与监控参数。

### 1.2 完整历史的候选干扰

| 段 | 来源 | 内容 | 原设计估计时长 |
|---|---|---|---|
| Key | 自己遥操 | 放进目标柜、关门 | 1–3 分钟 |
| 后处理 | 适配器生成 | 完全关门并记录状态变化 | 由协议定义 |
| 过渡 | cut；后续可导航 | 对齐下一段机器人起点 | cut 不计虚构导航时间 |
| D1 | `tidying_bedroom`，task 18 | 卧室整理 | 约 6 分钟 |
| D2 | `outfit_a_basic_toolbox`，task 19 | 工具箱整理 | 约 6 分钟 |
| D3 | `putting_away_toys`，task 54 | 收纳玩具 | 约 6 分钟 |
| Query 准备 | 适配器生成 | 保持底盘、复位操作状态 | 由协议定义 |
| Query | 模型在线执行；dev/train 可另录示范 | 取回杯子 | 约 1–2 分钟 |

这些来源是候选池：设计上尽量避开吊柜、岛台及粒子/加热/切割任务，但是否合格由每条 demo 的写集合、读取依赖、transitions 和碰撞 QC 决定，不按任务名字直接保证互不干扰。时长从真实时间戳重新统计。

M2 只选一条完整 demo 导出，再挑有完整初态和终态的短片段接入。截取新段要重新计算初态、依赖、写集合和端点，不能沿用原 demo 的初终态。M4 再接入三个来源、随机顺序和完整时长。

### 1.3 随机化与世界签名

- 目标柜 A/B/C 按父 episode 分组均衡；三种目标都验证可操作性。
- 杯子初始位姿和 Key 起点使用少量实例起步，批量阶段再增加。
- 首版固定一个杯子模型；多个模型按世界签名分组，不能只换位姿就假设所有 episode 共用同一世界。
- demo、干扰数量和顺序写入 spec。跨 split 按来源轨迹和派生关系划分。
- Query 的底盘配置属于实验协议。测试记忆距离时尽量固定同一配对组的 Query 位姿，最后插入显式过渡；若使用干扰终点，单独报告导航距离与可见画面差异。

### 1.4 可见信息与捷径

默认协议 `visual_v0` 的历史输入为头部 RGB、选定本体感知和时间戳；Query 只提供“把杯子拿回岛台”。Key 的具体目标柜提示给操作员，不给策略。历史动作、语言和段角色标签默认不提供；允许这些信息的实验使用单独协议报告。

内部 spec、manifest、目标柜、`inst_to_name`、Cq、原始 HDF5、QC 和训练 Query 示范不进入策略输入。评测 `include_obs=False`，避免 BehaviorTask 的真实物体位置成为捷径；奖励、goal status 和调试 info 同样不传给策略。

所有柜门统一完全关闭，且验证关闭不会碰动杯子。检查当前画面的门缝、阴影、杯子可见性与残留机器人姿态；目标柜均衡不能替代这些检查。Key 总在开头、操作风格与 cut 可能让模型容易定位 Key，因此首版只声称评估该历史协议，后续通过 Key 位置变化或相同来源干扰检查泛化。

## 2. 总体流程与产物

```text
模板/实例 → C0 → 真实 Key Segment
                    │
              显式完全关门 → C1
                    │
         cut / 导航 + 合格干扰 Segment → 各段端点
                    │
              显式 Query 准备 → Cq
                    │
          ┌─────────┴─────────┐
       离线历史渲染       Query 示范（dev/train）
          │                   │
       HistoryView        独立媒体与动作
          │
    恢复 Cq → 策略 Query → 事件与指标
```

每个历史 slot 保存起始/结束快照哈希、Segment 引用和 QC。C1 特指 Key 后处理后的世界，Cq 特指 Query 准备后的世界；其他中间状态按 slot 顺序命名，不写死必须存在 C2/C3/C4。

统一数据根目录 `$MB_ROOT`，目录见架构第 6 节。历史视频、内部 manifest、Query 示范分别存放，不输出包含 Query 答案轨迹的测试历史视频。

## 3. 前置准备

1. 使用预装 `behavior` 环境及 Isaac Sim；需要仿真的命令和所有测试设置 `OMNIGIBSON_HEADLESS=1`。缺少环境或依赖时先由维护者完成安装。
2. 核对资产。任务数据放在独立的 `datasets/memory-bench-task-instances/`，通过 `OMNIGIBSON_TASK_INSTANCES_DATASET` 交给官方工具使用；官方 `2026-challenge-task-instances/` 只读，不修改其中的元数据（见架构第 6 节）。
3. M1 不依赖 2026 demo。M2 先获取一条候选数据，失败再补少量备选；不将 60 条数据检查作为首次闭环前提。
4. HDF5 只读侦察：记录 config、scene_file、每个 `demo_N` 的轨迹编号/instance_id、`num_samples`、state/action 长度、state_size、transitions、init_metadata、频率与实际时间戳（若存在）。
5. 明确录制版本与末帧时间语义。当前上游回放将 `state[i]` 解释为执行 `action[i]` 前的状态，不能把最后存储帧直接当终态；原始数据可能来自其他版本，需逐来源确认。

## 4. Key / Query 的 BDDL 与采样

### 4.1 BDDL 草稿

放到 `bddl3/bddl/activity_definitions/<task_name>/problem0.bddl`，解析测试通过后再做仿真采样。

**Key：`mb_mug_into_top_cabinet`**

```lisp
(define (problem mb_mug_into_top_cabinet-0)
    (:domain behavior-1k)

    (:objects
        mug.n.04_1 - mug.n.04
        cabinet.n.01_1 cabinet.n.01_2 cabinet.n.01_3 - cabinet.n.01
        countertop.n.01_1 - countertop.n.01
        floor.n.01_1 - floor.n.01
        agent.n.01_1 - agent.n.01
    )

    (:init
        (ontop mug.n.04_1 countertop.n.01_1)
        (not (open cabinet.n.01_1))
        (not (open cabinet.n.01_2))
        (not (open cabinet.n.01_3))
        (inroom cabinet.n.01_1 kitchen)
        (inroom cabinet.n.01_2 kitchen)
        (inroom cabinet.n.01_3 kitchen)
        (inroom countertop.n.01_1 kitchen)
        (inroom floor.n.01_1 kitchen)
        (ontop agent.n.01_1 floor.n.01_1)
    )

    (:goal
        (and
            (inside ?mug.n.04_1 ?cabinet.n.01_1)
            (forall
                (?cabinet.n.01 - cabinet.n.01)
                (not (open ?cabinet.n.01))
            )
        )
    )
)
```

`cabinet.n.01_1` 代表当前阶段的目标柜，`_2/_3` 代表其他柜。变体通过完整的 `inst_to_name` 绑定表达，不能仅更新目标柜一个键。

**Query：`mb_retrieve_mug_from_top_cabinet`**

```lisp
(define (problem mb_retrieve_mug_from_top_cabinet-0)
    (:domain behavior-1k)

    (:objects
        mug.n.04_1 - mug.n.04
        cabinet.n.01_1 cabinet.n.01_2 cabinet.n.01_3 - cabinet.n.01
        countertop.n.01_1 - countertop.n.01
        floor.n.01_1 - floor.n.01
        agent.n.01_1 - agent.n.01
    )

    (:init
        (inside mug.n.04_1 cabinet.n.01_1)
        (not (open cabinet.n.01_1))
        (not (open cabinet.n.01_2))
        (not (open cabinet.n.01_3))
        (inroom cabinet.n.01_1 kitchen)
        (inroom cabinet.n.01_2 kitchen)
        (inroom cabinet.n.01_3 kitchen)
        (inroom countertop.n.01_1 kitchen)
        (inroom floor.n.01_1 kitchen)
        (ontop agent.n.01_1 floor.n.01_1)
    )

    (:goal
        (and
            (ontop ?mug.n.04_1 ?countertop.n.01_1)
        )
    )
)
```

Query 不独立采样，初态来自 Cq。当前缓存加载路径建立对象作用域，不替代 benchmark 对 `:init` 的显式验证。适配器检查杯子在目标柜、三柜关闭、机器人未抓住物体且 Query 目标尚未满足。

严格成功和错误开柜事件由评测器监控。完成时除 Query BDDL 成功，还要求杯子已释放并满足配置中的稳定性检查，避免抓着杯子停在台面附近被记为完成。

### 4.2 最小采样流程

先固定一个杯子模型，要求放得进柜子、夹爪好抓。白名单示意：

```json
{
  "mb_mug_into_top_cabinet": {
    "room_types": ["kitchen"],
    "house_single_floor": {
      "whitelist": {
        "mug.n.04": {"mug": {"<MODEL_1>": null}},
        "cabinet.n.01": {"top_cabinet": {"lkxmne": null}},
        "countertop.n.01": {"bar": {"udatjt": null}}
      },
      "blacklist": {}
    }
  }
}
```

白名单限制类别/模型，采样后还要验证具体对象名及三个柜子的数量。先采一份模板和一个实例，可操作后再扩到 3–5 个；不要先生成 300 个再验证。

```bash
OMNIGIBSON_HEADLESS=1 conda run -n behavior python OmniGibson/scripts/sampling/sample_b1k_tasks.py -t mb_mug_into_top_cabinet
OMNIGIBSON_HEADLESS=1 conda run -n behavior python OmniGibson/scripts/sampling/sample_robot_pose.py -t mb_mug_into_top_cabinet
```

两条命令之间先生成实例。`multiply_b1k_tasks.py` 的参数为 `--partial_save --start_idx 1 --end_idx 1 -t mb_mug_into_top_cabinet`，场景从白名单推断，不支持 `-s`。当前脚本显式设置 `gm.HEADLESS=False`，会覆盖环境变量初始化的宏；无显示器时先通过兼容包装或独立小补丁保留 headless 设置，再执行实例采样，不能直接把设置环境变量视为修复。此问题列入工程任务 D0/B3。

示例假设 conda 已加入 PATH；否则使用本机实际 conda 路径。

可用任务信息写入本数据集自己的 `available_tasks.yaml`。房间列表从模板、实际路径和相关几何验证，不把固定房间数量当成保证。采样产物直接写在 `memory-bench-task-instances` 中，benchmark 只读取这个数据集。

## 5. 已有 demo 的导出

### 5.1 原生解码

按 `HDF5PlaybackWrapper.create_from_hdf5` 的参数构造原生环境，关闭 transition rules；解码/渲染可用视觉模式，末动作恢复与稳定性验证另用物理进程。使用录制 scene_file 和所需完整模板，先对齐 non-kinematic 状态 schema，再按 `state_size` 解码。

`og.sim.deserialize` 返回的是稀疏对象字典。先恢复初态，再逐帧累计补丁；不能把每个返回字典当作完整世界。验证解码消费的长度、对象签名、机器人配置和初始元数据。初始元数据可能含未进入状态向量的物理属性，不能直接丢弃。

### 5.2 写集合与依赖

以完整初态为基准，检查整个序列的位置、旋转、关节和相关非运动状态变化。位移 1 cm、转角 2°、关节变化 0.01 是初始筛选参数，不是正确性保证；旋转用四元数角距离，关节区分角度和位移。

移动后睡眠的对象仍属于写集合。支撑面、容器和抓取引用进入读取依赖，即使它们没有移动。对保留物体做更严格的全序列检查，不能用筛选阈值掩盖小幅碰动。

### 5.3 导出与截取

- 输出统一 `meta.json` + `frames.pt`，累计终态产生 effects；确认末动作后的终态或标为 missing。
- 通过 StateAdapter 读取实际底盘位姿，不能从机器人 `root_link` 直接取底盘。
- 名字映射包括机器人键、约束中的对象/prim 引用和传感器名，不只是把外层键改成 `robot`。
- 导出 sample_rate、时间戳、动作顺序/单位、关节顺序、配置和版本签名；来源动作先保持原语义，不默认可以直接用于正式评测机器人。
- 短片段选稳定边界，用累计状态建立新初态，显式获得截取终点；重新计算写集合、依赖和 QC。

### 5.4 后续几何检查

完整长段接入前增加机器人和搬运对象的扫掠检查。采样 AABB 只是保守筛选，需考虑帧间运动，不能靠每 10 帧一个盒子保证无碰撞。抽样密度、外扩量和误拒率由实测选择；人工视频检查仍保留。

## 6. 世界构建与 EpisodeSpec

### 6.1 世界模板

M1 使用 Key 世界。M2 再加入一个干扰段所需的对象及读取依赖；M4 才考虑三个来源的并集。按场景、物体模型/尺寸、机器人及状态 schema 生成 world_id，相同签名才共享模板。

若使用 `merge_scene_files`，兼容层先深拷贝输入。该函数采用 scene_b 的 metadata、同名状态及 system registry，不能直接视为只追加物体。构建器必须：

1. 显式选择基础场景与同名物体状态，检测签名冲突；保留 Key 的基础布局。
2. 合并新对象与所需状态字段，并核对系统信息；首版拒绝不支持的系统。
3. 根据 Key 实例及各段初态设置对象状态，处理重叠写集合和读取依赖冲突。
4. 重建完整任务 metadata，绑定杯子、三个柜子、岛台、地板和 agent；不沿用最后一份干扰模板的任务绑定。
5. 房间取所有段的读取/几何需求与经过路径的并集，验证没有因 partial load 缺对象。

### 6.2 内部 spec 示例

```yaml
schema_version: 1
episode_id: mugcab_v0_000
suite: mb_v0
split: dev
scene_model: house_single_floor
memory_task: mug_in_top_cabinet
variant: {target_cabinet: top_cabinet_lkxmne_1}
initial_ref:
  kind: instance
  task: mb_mug_into_top_cabinet
  instance_id: 1
history_slots:
  - {role: key, source: teleop, ref: {recording: pending}}
  - {role: postprocess, source: bridge, ref: {mode: close_doors}}
  # M1 没有以下两行；M2 导出段后填入确切 segment_id
  - {role: transition, source: bridge, ref: {mode: cut, next_segment: replay2026/540123_clip}}
  - {role: distractor, source: replay2026, ref: replay2026/540123_clip}
  - {role: query_prepare, source: bridge, ref: {mode: reset_manipulation}}
query_demo: {source: teleop, recording: pending}
protocol: visual_v0
```

示例 ID 只表达格式，不承诺对应数据存在。val/test 的 `query_demo: null`，Query 请求由任务和协议生成。slot 编号和快照映射由 compose 写入内部 manifest，模型不会读取这份 spec。

### 6.3 C0 的验证

先在纯 JSON 中建立候选 C0，再在物理副本上静置检查。初始参数为 50 个动作周期、可移动物体平移小于 5 mm，另测旋转、关节、支撑/接触及速度；频率和具体容差写入配置。

检查 Key 初始谓词成立、Key 目标不成立、所有绑定非空、杯子模型和机器人配置匹配。首版保存候选 C0，静置结果仅作为 QC；如果采用静置后的状态，生成显式 settle 段并重新验证。

## 7. 真实 Key 采集

### 7.1 JoyLo 的必要扩展

- 可选参数：`scene_file`、房间、机器人起点和 `instruction`，绕过可用任务及 partial-load 推断。
- 自定义场景使用 `instance_id=None`，但 reset 仍必须回到指定快照，不能仅跳过 tro_state 加载就认为正确。
- 明确场景机器人与配置机器人只有一个。原配置 `include_robots=False` 时，通过配置创建机器人，再由适配器恢复其完整操作状态。
- 在正常结束且 reset/关闭前捕获终态 sidecar，绑定轨迹编号、末动作索引和 raw 哈希。重新录制产生新 attempt，不覆盖已用于导出的来源。
- `replay_data.py` 支持自定义完整世界和房间用于 QA；正式历史仍走统一 Segment 渲染路径。

所有终态/复位调用上游内部 API 的部分经 StateAdapter/compat 收口。录制、校验、Query 和评测共享机器人控制器、抓取模式、频率、质量及宏配置。

### 7.2 Key QC

记录真实完整动作：拿杯子、开目标柜、放入、释放、关门。终态检查杯子在目标柜、所有柜门关闭，非目标柜全序列未打开。检查干扰依赖未被破坏、机器人没有抓着物体，原始终态与导出累计终态一致。

### 7.3 显式后处理与 C1

按每扇门的关节范围/方向和资产关闭端点完全关门，并清速度；不能把所有模型的关闭值一律当成 0。后处理保存 before/after 和无动作补丁，渲染对应边界观测。再次检查杯子位置、稳定性和 Key 谓词，得到 C1。

## 8. 干扰拼接与 Cq

### 8.1 cut 与导航

首版 cut 显式将机器人设到下一段初态，校验落点无碰撞，并记录不连续事件。允许跳变不意味着可以跳到家具内部。后续导航要考虑新增对象、搬运对象和手臂插值，路径失败记录原因再回退 cut。

### 8.2 每段兼容性

- 写集合符合当前阶段权限；干扰不得写杯子、三个柜子和岛台。
- 读取依赖与当前世界匹配，包含支撑面、容器、关节状态及模型/尺寸。
- 机器人及搬运对象轨迹不与其他段的物体或受保护对象冲突；M2 短片段先人工检查，标明检查方式。
- 有完整初态和已验证终态，机器人配置与动作语义满足所用模式。

### 8.3 生成端点

从段起点累计补丁和 terminal_patch，校验 effects 后生成候选端点。物理副本静置并直接评价阶段谓词；不要只 `og.sim.step()` 后读缓存 `task.success`。检查失败换段或修改组合；禁止静默修物体位置后继续渲染。

### 8.4 Query 准备

通过 StateAdapter 保持实际底盘位姿，复位非底盘关节、速度、控制器并清理抓取约束，输出 `query_prepare` Segment 与 Cq。不能直接将全关节向量替换为 reset 值，这会影响 R1Pro 的虚拟底盘关节。

若实验固定 Query 位姿，先插入显式 cut/导航，再执行复位。最终检查杯子仍在目标柜、三柜完全关闭、Query 目标为假、机器人无抓取且落点可操作。历史末尾累计状态必须与 Cq 一致。

## 9. Query 示范

M1/M2 的 dev episode 各录一条真实 Query 用于检查恢复与动作复演；批量阶段只有 train 采示范，val/test 不采。

Query 从同一 Cq 和同一准备流程启动，操作员提示可含目标柜但不进入模型输入。示范要直接取回、释放杯子并通过完成 QC，不打开错误柜。导出为统一 Segment，保存到独立 `query_demo/`，其视频和动作不拼入历史。

## 10. 统一离线渲染

1. 每个 slot 使用自己的起始快照和 Segment，Key、干扰和 Query 示范采用同一渲染路径。
2. 视觉进程在创建环境前设置宏并禁用控制；恢复初态后累计补丁，明确相机刷新步骤。根据上游回放做必要传播并保持对象静止，不能假设 `load_state` 后直接 `get_obs` 一定是新画面。
3. 检查首帧、末帧、终态补丁、后处理和 cut 的观测，保存实际帧数与半开区间 `[start, end)`；索引注明帧是动作前还是终态，并分别记录模拟时间和视频播放时间，无动作边界不增加虚构的记忆距离。
4. 将最后历史状态与 Cq 比较，并将相机 profile 与评测观测规格对齐。相机、分辨率、模态或采样率变化只重渲。
5. 输出头部 RGB 和公开 `history_index.json`；本体感知、动作与内部段索引可以另存 parquet，公开导出必须按白名单筛选。
6. Query 示范渲染至独立目录。公开 HistoryView 只引用历史目录，不靠截断一个含 Query 的完整文件防止泄露。

首版头部 RGB 360×360；长历史阶段再增加手腕、深度及 LeRobot 导出。记录渲染耗时、存储与显存，评估是否需要按段并行和分块读取。

## 11. 评测协议与指标

### 11.1 环境恢复与策略输入

先实现本地 harness，不必先继承官方 evaluator。配置固定 `num_envs=1`、关闭自动 reset 和任务观测；恢复 Cq、重建完整 scope，按统一配置完成状态传播和相机刷新，再启动监控及 Policy。

首版支持离线历史输入，在线模式后续通过 `observe` 顺序发送。每条 episode 清空策略记忆。只发送架构第 5.5 节的 HistoryView/QueryRequest/QueryObservation；内部 manifest、环境对象、reward 和 info 不交给外部策略。

以后适配 `BatchedEvaluator` 时，除实例恢复还需处理任务注册、human stats、房间、scope、批量状态和历史分发，不能只重写 `_load_instance_state` 就认为完成接入。

### 11.2 监控与指标

依据 `Open` 的相关关节、方向和范围标定每扇门，保存阈值、容差和版本。直接读关节可用于记录，但要与谓词语义一致。每次物理更新后检查事件，至少覆盖每个 physics substep；同一步多柜打开记为并列，不能按 Python 遍历顺序选“首柜”。

| 指标 | 定义 |
|---|---|
| 严格成功（主指标） | 完成条件成立且从未打开任何非目标柜 |
| 首选正确 | 第一次开柜事件唯一指向目标柜；并列/未开柜分别记录，主分母包含全部有效 episode |
| 宽松成功 | Query BDDL 成功且通过释放与稳定性完成检查 |
| 错误柜数量 | 至少打开过一次的不同非目标柜数量 |
| 错误开柜事件数 | 非目标柜从关闭到打开的次数，含重复打开；抖动去重规则冻结 |
| 完成时间 | 模拟秒数及动作步数；未完成同时报告超时，不混入成功用时均值 |
| 操作/导航诊断 | 失败原因、导航距离、碰撞、开门与抓取阶段耗时 |

最大时长先用固定配置跑通，再依据训练 Query 数据选择并冻结，不用测试结果调参。错误开柜发生后保留失败标记，可继续至完成/超时计算宽松指标；策略不接收该内部标记。

### 11.3 工程策略与实验对照

工程检查用 `hold`（保持当前姿态和零底盘速度）、`replay`（按原动作语义复演）和 `scripted_open`（直接设柜门验证事件）。全零动作不等于 hold，绝对位置控制需要显式保持当前关节目标。replay 检查起点、控制器、抓取、频率和物理属性；记录复演偏差与重复结果，不能保证物理开环每次成功。

M3 在同一 Cq、相同控制器和预算下配对比较：无历史、正确历史、来自不同目标的匹配替换历史、文本 oracle。oracle 是单独标记的辅助条件，绝不混入默认 Query。替换历史匹配时长与干扰来源，记录历史目标/真实目标交叉表，不能把同一历史的帧乱序与跨 episode 替换混为一项。

三个柜子均衡、可操作性近似且样本足够时，无历史首选结果才可与 1/3 比较；报告置信区间与每柜结果，拒绝动作/未开柜计入分母。单条闭环只检查执行结果，不作统计结论。

## 12. 数据划分与规模

M1/M2 使用 `dev`，不进入正式 train/val/test。M3 先建立 A/B/C 配对小样本，用于暴露视觉捷径和操作差异，规模按实际采集成本确定。确认对照成立后再规划约 100 条父 episode；原 70/10/20 只是候选预算，三柜数量尽可能均衡并报告实际计数。

同一 Key 及 k=0..3 等派生版本必须属于同一 split。原始 demo、截取片段和恢复终态共享 source group；采样实例 ID 也连同场景、任务、模板/模型签名组成 group，不能只比较裸数字。配对组整体划分，避免训练集出现测试 Key 的另一种干扰版本。

记忆距离用实际 Key 到 Query 的模拟时长、帧数和干扰数量共同报告。固定 Query 起点的实验与使用最后干扰终点的实验分开。单场景首版只验证场景内泛化；跨模型、跨场景或操作员泛化另设 split。

## 13. QC 关卡

| 关卡 | 检查 | 不通过时 |
|---|---|---|
| G0：C0 | 完整绑定、初始谓词、机器人签名、稳定性和接触 | 修模板或换实例 |
| G1：Key | 真实经历、终态可用、目标成立、错误柜未开、依赖未破坏 | 重录，保留失败记录 |
| G1'：后处理 | 关闭端点、杯子未动、显式补丁及端点一致 | 修改操作并重建 |
| G2：每个干扰 | 写权限、读取前提、轨迹冲突、终态、稳定性和阶段谓词 | 换 demo/片段/顺序 |
| G3：Cq | 底盘保持、无抓取、Query 未完成、起点可操作、历史端点一致 | 排查准备/恢复 |
| G4：渲染 | 刷新、首末帧、帧索引、公开字段、历史不含 Query | 重渲或修导出 |
| G5：Query 示范 | 同一 Cq、严格完成、终态与动作语义 | 重录/修配置 |
| G6：评测 | 输入白名单、scope、监控边界、hold/replay/scripted | 修 harness |
| G7：小样本对照 | 每柜操作性、当前画面泄露、配对历史条件 | 调整实验后重新冻结协议 |

QC 保存测量值、阈值、检查方式和失败原因；人工检查明确标注。M2 可人工检查短片段，M4 发布前必需 QC 不能以“后续再做”跳过。

## 14. 代码改动落点

公共目录和接口见架构第 4–5 节。任务配置/监控在 `tasks/mug_in_top_cabinet/`；纯 JSON 在 `core/`；状态传播、机器人复位和上游私有 API 在 `sim/`；来源导出在 `sources/`；编排和缓存在 `pipeline/`；公开输入与在线执行在 `eval/`。

BDDL 使用本篇两份 `mb_*` 定义。JoyLo 补丁包含自定义快照启动、reset 基线、终态捕获、操作员提示和 QA 参数；不把正式历史渲染塞入 JoyLo。任务数据只写入 `datasets/memory-bench-task-instances/`，官方任务实例目录保持只读。

## 15. 交付顺序

M0 → M1 → M2 → M3 → M4，具体任务见工程文档。M1 交付一条无干扰真实闭环；M2 交付一条短干扰闭环；M3 交付对照报告；M4 才交付长历史和批量数据。

后续任务通过阶段接口加入记忆更新、多杯子身份或状态记忆。使用已有 demo 作为 Key 时，`initial_ref` 指向对应世界，任务推断/校验变体，不要求 `key_instance`。

## 16. 风险与备选方案

| 风险 | 处理 |
|---|---|
| 吊柜放入/取出困难 | M1 前验证；换杯子或同款下柜并重做依赖检查 |
| 原始终态缺失 | 原生环境恢复并验证，或换数据；不能用最后存储帧冒充 |
| 稀疏补丁漏变化 | 初态累计、non-kin 检查、保护物体更严检查 |
| 合并破坏基础布局/绑定 | 明确同名状态优先级、完整重绑、物理 QC |
| 干扰组合碰撞 | 从短片段和少量候选开始；记录拒绝率再扩大 pool |
| 当前画面或语言泄露 | 公开白名单、统一关门/准备姿态、配对无历史实验 |
| replay 不稳定 | 对齐起点与控制配置，报告偏差和重复成功率 |
| 资源成本过大 | 实测短段，再估长历史；分块读取、按段渲染 |

正式发布前核对来源许可与发布范围，保存来源版本；该检查不阻塞本地试点闭环。

## 17. 开始实现前的检查表

- [ ] 确认环境、资产、BDDL 和机器人配置版本。
- [ ] 选定一个杯子模型，验证三个候选柜及岛台绑定。
- [ ] schema 明确初态、稀疏补丁、动作前状态与末动作后终态。
- [ ] 机器人实际底盘、复位、控制器和抓取状态由适配器处理。
- [ ] Key/Query scope 与初始谓词显式检查，避免缓存成功判定。
- [ ] 终态 sidecar 在 reset/关闭前捕获并绑定正确轨迹。
- [ ] 公开 HistoryView 与内部 manifest/Query 示范分开。
- [ ] 先完成 M1，再获取少量干扰数据推进 M2。
