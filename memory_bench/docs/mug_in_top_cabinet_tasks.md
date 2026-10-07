# 「吊柜里的杯子」工程任务拆解

> 配套文档：[架构](architecture.md) 定义公共格式和接口，[Pipeline](mug_in_top_cabinet_pipeline.md) 定义实验流程。本文只定义实现落点、依赖、产物和验收，不另建一套格式。
> B1–B3 的代码骨架和可复现命令见 `src/memory_bench/tasks/mug_in_top_cabinet/README.md`；仿真采样与 B4 操作验收仍需在可用 GPU/硬件环境执行。

## 0. 范围与完成标准

### 0.1 分阶段交付

| 阶段 | 最小范围 | 完成标准 |
|---|---|---|
| M0 | 一个模板/实例、四个接口的最小实现 | 绑定、状态契约与策略白名单可检查 |
| M1 | 无干扰，真实 Key 和真实 Query | 采集、恢复、历史渲染、监控和计分完整闭环 |
| M2 | 一条已有 demo，先接入短片段和 cut | 稀疏终态、世界合并和历史/Cq 一致 |
| M3 | A/B/C 配对小样本 | 对照实验能区分记忆、操作和当前画面线索 |
| M4 | 三段完整干扰、长历史、批量工具 | 兼容性、资源成本、划分和发布检查通过 |

首版固定一个杯子模型、R1Pro、`house_single_floor`、本地进程、头部 RGB 360×360 和手写 spec。M1 不需要已有干扰数据，M2 不需要下载 60 条 demo。自动导航、扫掠筛选、三相机、深度、Slurm、批量队列和发布工具在后续实现。

假 Key 可以解耦快照/渲染工具开发，但不能替代真实 Key。直接设门的 scripted 策略只测试指标，开环 replay 只测试恢复和动作配置；三者都不是记忆能力 baseline。

### 0.2 四个优先稳定的接口

- StateAdapter：坐标转换、状态捕获/恢复、实际底盘、操作复位、scope 绑定和谓词检查。
- SegmentSource：已有录制导出；SegmentGenerator：按指定起始世界生成遥操、过渡及后处理段。
- MemoryTask：世界需求、分阶段读写权限、不变量、操作员/策略指令、QC、监控和评分。
- Policy：只接收公开 HistoryView、QueryRequest 和当前观测，不读取内部 manifest。

接口骨架见架构第 5 节。首版只实现当前闭环用到的能力，不先搭完全部通用设施。

## 1. 公共约定与骨架（T0）

### T0.1 最小包与路径

**落点**：`memory_bench/src/memory_bench/`、`configs/paths.yaml`、`core/paths.py`。

创建最小模块和配置，统一 `$MB_ROOT`、`mb_*` BDDL 名、`frames.pt` 文件名。代码目录使用架构第 4 节，数据目录使用第 6 节；不再保留旧的按任务平铺 scripts 方案。

Python 使用已有 `behavior` 环境。包未安装时可用：

```bash
OMNIGIBSON_HEADLESS=1 PYTHONPATH=memory_bench/src conda run -n behavior python -m memory_bench.cli --help
```

这条命令需等 CLI 实现后执行。不要自动安装项目/依赖；所有测试都设置 `OMNIGIBSON_HEADLESS=1`，物理作业与视觉作业分进程，首版 `num_envs=1`。

**验收**：纯 core 可以独立导入；路径解析不依赖具体任务名；环境和资产缺失时清楚报告。

### T0.2 schema 与稀疏状态语义

**落点**：`core/schemas.py`、`core/segment_ops.py`、`core/world_json.py`。

实现架构第 2、5 节的 EpisodeSpec、SegmentMeta、WorldSnapshot 和补丁累计：

- complete initial_state、动作前 frames、末动作后 terminal_patch；effects 由累计结果生成。
- 缺失对象保留上一状态；没有 transitions 时不表示对象删除。
- 状态坐标、四元数 xyzw、关节顺序、机器人配置和动作规格显式声明。
- `terminal_status` 为 captured/recovered/missing；缺失终态拒绝正式 compose。
- 前提条件按位置、旋转、关节、模型/尺寸及非运动状态分别检查。
- 内部 history_slots 与独立 query_demo 引用分开。

**验收**：覆盖移动后睡眠、末帧缺对象、动作最后一步才完成、关节/旋转不匹配和初态无法恢复等情况。累计终态与 effects 相同，输入 JSON 不被原地污染。

### T0.3 公开输入和 provenance

**落点**：`eval/protocol.py`、`pipeline/stages.py`。

定义 `visual_v0` 的观测白名单及公开历史索引；禁止传 spec、manifest、目标柜、真实物体位置、来源 ID、QC、reward/goal status 和 Query 示范。Key 的目标柜提示仅给操作员。

每个产物记录输入哈希、配置、schema、git SHA 和未提交源码哈希，以及软件/资产版本、宏、频率和机器人签名。首版就保留 provenance，完整自动缓存可后续实现。

**验收**：公开输入可独立读取；未提交代码或配置改变会改变阶段签名；Query 示范不会出现在 HistoryView 引用中。

## 2. 依赖关系与推荐顺序

```text
M0：T0 → B1 → B2/B3 → D0
                    │
M1：B4 → E1–E4 → 无干扰 C0 → 真实 Key → F1 → F3
                                                │
                          真实 Query → G1/G3 → H1–H3
                                                │
M2：A1 → C1–C3 → D1–D3 → 新 C0 重录 Key → F1/F2/F3 → G1–G3 → H1–H3
                                                │
M3：H4 / I1 配对实验 → M4 长历史与批量化
```

接口和数据侦察可并行。M2 在合成世界中重新验证/采集 Key，不能假设向 M1 世界增加物体后，原 Key 仍可无检查复用。若要复用，必须通过来源前提、全轨迹冲突和公开观测一致性检查。

遥操依赖可用硬件和操作员。硬件未就绪时可用 F0 验证工具并推进 A/C 系列，但 M1 保持未完成，不能以假 Key 标记通过。

## 3. 任务清单

### B. 最小 Key / Query 任务（M0–M1）

#### B1 BDDL 与任务插件　依赖：T0

**落点**：两份 `mb_*` BDDL、`tasks/mug_in_top_cabinet/task.py`。

使用 Pipeline 第 4.1 节定义，先验证解析，再实现世界需求、变体、完整绑定、阶段权限和 QC。Key 可操作杯子/目标柜；干扰保护杯子、三柜和岛台；后处理按明确权限关门。Query 的策略指令不含目标柜。

**验收**：五类对象解析正确；A/B/C 变体覆盖全部 BDDL 实例绑定；阶段上下文允许合法更新并更换不变量，不要求杯子永远在初始柜。解析和绑定单测已实现；阶段 QC 为符号级契约，物理稳定性仍待 B4。

#### B2 模板与白名单　依赖：B1、资产

**落点**：`task.py` 的 `TASK` 字段、`scripts/prepare_task.py`、`datasets/memory-bench-task-instances/`。

`prepare_task.py mug_in_top_cabinet init` 生成独立的任务实例数据集：写入白名单，并从官方目录复制稳定基础场景。设置 `OMNIGIBSON_TASK_INSTANCES_DATASET=memory-bench-task-instances` 后，用官方采样脚本采样一份模板，再核对实际岛台、三个 lkxmne 柜子和厨房地板。官方目录只读。

**验收**：`init` 和 `set-variant` 已在数据副本上验证；真实采样、scope 和杯子可放入柜内仍待 GPU 验证。

#### B3 一个实例与机器人起点　依赖：B2

使用 `multiply_b1k_tasks.py` 先生成一个实例，再采样机器人位姿。该脚本从白名单推断场景，不支持 `-s`；其 `gm.HEADLESS=False` 覆盖需由 D0 的兼容包装或独立小补丁处理，见 Pipeline 第 4.2 节。验证完成后可增加至 3–5 个，批量规模待 M3 后确定。`set-variant A|B|C` 把 `cabinet.n.01_1` 绑定到所选吊柜；`register-joylo` 把机器人起点写进本数据集的 `available_tasks.yaml`，供 JoyLo 读取。

**验收**：实例生成、robot_poses 和房间加载仍待 GPU 验证。目标状态验收要求初始杯子在岛台、三柜关闭、Key 目标为假。

#### B4 放入与取出的可操作性　依赖：B3、硬件

用原版 JoyLo 检查开门、放入、释放、关门，再取出放回岛台；确认抓取、判定稳定性与碰撞。记录 A/B/C 的固定视角命名。若吊柜不稳定，先换杯子，再考虑下柜并重新绑定。

**验收**：已准备 JoyLo 操作步骤与符号 QC；本机无 NVIDIA 驱动/GELLO 操作环境，首个柜及另外两柜的真实操作均未验收。M3 前补齐三柜检查，不能只由其他任务曾使用吊柜推断本任务可行。

### D0. 状态适配与基础校验（M0–M1）

**落点**：`sim/compat.py`、`session.py`、`state_io.py`、`validate.py`。

实现原生世界/场景坐标转换、快照捕获/恢复、状态 schema 对齐、完整 BDDL scope 刷新及谓词评价。采用官方合并工具时经过兼容层，不在 core import OmniGibson。

R1Pro 实际底盘从机器人位姿 API 读取。复位保留实际底盘、处理非底盘关节、速度、控制器和抓取状态；物理配置包括采集时的底盘质量等属性。

静置检查使用候选快照副本，默认不回写；先传播状态再直接评价谓词，不能只走 `og.sim.step()` 后读缓存 success。若走 env.step，使用有效 hold 动作、关闭自动 reset，并区分验证步与策略步。无头采样包装还需处理官方实例脚本显式覆盖 HEADLESS 的行为。

**验收**：快照往返在字段容差内；移动过的底盘复位后不回原点；切换目标柜后 scope 指向新实体；同一检查器正确识别目标为真/假。记录平移、旋转、关节、速度、接触/支撑和谓词结果。

### E. JoyLo 的最小扩展（M1）

#### E1 自定义场景启动　依赖：D0

**落点**：`joylo/scripts/launch_og.py`、`gello/robots/og_robot.py`、`gello/utils/og_teleop_utils.py`。

增加可选 scene_file、房间、机器人起点和 instruction。自定义场景绕过 available_tasks 与 partial-load 推断，`instance_id=None`，保证只有一个机器人。场景 exclude robot 时使用共享配置创建，再恢复操作状态。

**验收**：从 C0 启动与快照一致，杯子/柜子/岛台 scope 正确，任务面板显示正确 Key 目标。

#### E2 reset 基线与终态捕获　依赖：E1

确保多次 reset 回到指定 C0/Cq；正常完成后、reset/关闭前捕获完整 terminal.json。sidecar 绑定 raw 哈希、轨迹编号、最后动作索引和 attempt，不能将上一条录制终态关联到下一条。

**验收**：连续两个 attempt 均有正确初态和终态；即使末动作才完成目标，也能导出末动作后的状态。录制失败/中断清楚标记，未完成数据不进入正式构建。

#### E3 操作员提示与配置一致性　依赖：E1

操作员界面显示目标柜，Policy Query 只收到取回指令。统一遥操、校验、Query 和评测的机器人模型、控制器、关节/动作顺序、频率、抓取模式、质量和宏配置，保存签名。

**验收**：公开 Query 请求不含柜名/答案；相同 Cq 在采集和评测准备后的状态一致。

#### E4 来源导出与 QA　依赖：E2、T0.2

**落点**：`sources/teleop.py`、JoyLo 回放参数。

实现已有录制 export 与指定快照遥操 generate。原始 HDF5 和终态统一导出 Segment；回放 QA 支持自定义完整世界和房间。正式渲染只消费 Segment。

**验收**：短录制累计状态与 terminal.json 一致；raw 不变；生成的 meta/frames 可被统一渲染器读取。Key 和 Query 示范均完成此验证。

### F. 状态构建（M1–M2）

#### F0 假 Key 工具　依赖：B2、D0；可选

基于明确资产内腔位置生成开发快照，检查谓词和稳定性，标记 synthetic。用于测试状态与渲染工具；缺少真实可观察经历，不进入正式 release 或记忆实验。

#### F1 真实 Key、后处理与 C1　依赖：E4、C0

采集并导出真实 Key；检查目标、错误柜事件和依赖未破坏。通过状态适配器生成完全关门 postprocess 段，关闭端点按资产确认，清速度并检查杯子未被挤动。C1 为后处理端点。

**验收**：Key 和后处理分别有起点/终点，累计状态一致；三柜关闭、杯子释放且在目标柜，静置检查通过。

#### F3 Query 准备与 Cq　依赖：F1；M2 还依赖 F2

生成显式 query_prepare 段，保持实际底盘并复位操作状态；若固定 Query 位置，先生成明确 cut/导航。生成内部 manifest，逐 slot 写入端点哈希与 QC。

**验收**：杯子仍在目标柜、Query 目标为假、机器人无抓取且可操作；历史最终累计状态等于 Cq。不能仅改全关节向量，也不能在评测加载时再隐式改变准备姿态。

### G. 统一渲染（M1–M2）

#### G1 渲染器与真实 Key　依赖：F1、D0

**落点**：`sim/render.py`。

每段独立恢复起始快照，视觉模式禁用控制，累计初态、补丁和 terminal_patch，执行明确的状态传播/相机刷新，输出头部 RGB。后处理有对应边界观测，人工抽查起点、开门、放入、关门和终态。

**验收**：画面没有滞后一帧/黑帧，真实 Key 的状态端点与保存快照一致。假 Key 的两个静态画面只算工具预览。

#### G3 历史索引与独立 Query 媒体　依赖：F3、G1、真实 Query 导出

输出 history/、独立 query_demo/ 和公开 history_index.json。索引用 `[start,end)`，明确动作前帧和终态帧；实际帧数来自 renderer，不假设等于 HDF5 num_samples。

**验收**：公开历史引用不含 Query 示范、角色/来源标签或内部路径；最后历史状态与 Cq 一致，同 profile 的 Query 首个有效观测匹配。internal parquet 与 public 导出分别筛选。

### H. 本地评测（M1）

#### H1 评测恢复　依赖：F3、D0、T0.3

**落点**：`eval/harness.py`。

固定单环境、关闭 automatic_reset、设置 `include_obs=False`，用同一适配器恢复 Cq 并重建 scope。按同一配置传播状态和刷新相机，验证准备一致后开始监控。不把 env、内部 reward/info 或快照路径交给策略。

**验收**：复位后机器人未抓物体，Query 初始谓词成立、目标为假，状态与录制 Query 起点在容差内；策略输入严格符合白名单。

#### H2 开柜事件与指标　依赖：H1

**落点**：任务插件的 monitors/score、`core/metrics.py`。

按 Open 的关节方向和范围标定；按 physics substep 记录关闭→打开事件，保存模拟时间。冻结抖动去重规则，处理未开柜和同一步多柜打开，不按遍历顺序产生首柜。

输出严格成功、首选正确、宽松成功、不同错误柜数量、重复错误开柜事件数、动作步数、模拟秒数和诊断。完成检查包含 Query BDDL、杯子释放及稳定性。

**验收**：覆盖错误后正确、重复开同一错柜、并列、未开柜和超时；首选主分母包含全部有效 episode。成功和失败分开报告用时。

#### H3 三种工程策略　依赖：H2、G3、真实 Query 示范

实现 hold、replay、scripted_open。hold 保持当前关节目标、底盘零速度，不能默认发送全零向量。replay 对齐录制 action 的单位、顺序、控制器与初始状态；scripted 直接设门只检查监控。

**验收**：hold 不能意外运动或完成；scripted 的正确/错误/重复事件符合定义；replay 在对齐配置后能够验证真实 Query，并记录重复结果与偏差。失败时定位状态/配置或物理复演问题，不用作弊状态回放冒充动作成功。

### A/C/D. 接入一条干扰（M2）

#### A1 一条原始数据侦察　依赖：T0.1

先选 task 18/19/54 中一条候选，必要时补备选。只读检查 cfg、scene_file、init_metadata、轨迹编号、transitions、num_samples、state/action 长度、state_size 和时间/频率。

**输出**：`$MB_ROOT/reports/raw_inspect.csv` 与来源记录。

**验收**：字段可读，来源签名明确，首版不支持的 transitions/系统被拒绝；末帧语义和终态可用性有结论，而不是要求机器人名字一定相同。

#### C1 原生环境解码　依赖：A1、D0

**落点**：`sources/replay2026.py`、`sim/state_io.py`。

参照 HDF5PlaybackWrapper 建原生环境，对齐录制状态 schema，按 state_size 解码。恢复初始元数据与完整初态，再累计稀疏状态。纯解码/视觉模式与需要物理恢复末动作的模式分进程。

**验收**：首帧、中间帧及末帧长度消费正确；字段、对象/关节签名匹配。缺初态或版本不兼容明确失败。

#### C2 写集合和读取依赖　依赖：C1

全序列检查运动、关节和相关 non-kin 状态；移动后睡眠仍保留。容器、支撑面、固定布局与抓取引用进入 required_objects。保留物体使用更严阈值检查。

**验收**：写集合合理，读取依赖完整，不含被禁止操作的目标物体；不能仅以任务名或预想玩具数量作为通过条件。

#### C3 Segment 与短片段　依赖：C2

导出完整 demo 的 meta/frames 与已验证终态；必要时原生物理恢复末动作并记录方法，无法确认则换数据。通过 StateAdapter 获取底盘，转换名字和约束引用。选稳定短片段，重算初态、写集合、依赖、终态与签名。

**验收**：片段累计状态与 effects 一致、终态非 missing、可由统一渲染器使用；动作缺失处标 action_valid=False，不混入训练控制样本。存储规模与处理耗时实测。

#### D1 合成世界　依赖：B2、C3

**落点**：`core/world_json.py`，上游合并调用位于 compat。

明确基础布局和同名状态优先级，加入必要新物体/状态字段；使用 merge_scene_files 时深拷贝输入，处理 scene_b 覆盖 metadata/状态/system 的行为。按 Key 要求重建完整绑定，不沿用干扰模板任务信息。

**验收**：对象/模型/尺寸/关节无冲突、只有一个机器人、系统受支持、绑定包含杯子/三柜/岛台/地板/agent；输入模板不被改写。

#### D2 合成 C0　依赖：D1

写入 Key 实例状态和干扰片段完整初态，设置机器人起点，检查读取依赖和房间并集。按 world signature 标记共享范围，不假设不同杯子模型共用模板。

**验收**：对象在对应位置、初始谓词与签名匹配；新世界重新采集/验证 Key，不能盲用 M1 的轨迹。

#### D3 合成世界校验　依赖：D2、D0

使用 D0 的候选副本静置与直接谓词检查，测量平移/旋转/关节/速度、穿透/支撑和机器人落点。保存原始候选、差异与 QC，不隐式采用静置结果。

**验收**：C0、Key 后和每个干扰端点均满足各阶段要求；需要修改状态时产出显式段并重建，不能在校验中悄悄修正。

#### F2 cut 与短干扰拼接　依赖：D3、新世界 Key/F1、C3

通过生成来源将机器人切到片段初态，检查落点；比较字段前提和阶段写权限，累计补丁与终态生成端点，检查 Key 不变量。短片段人工检查机器人/搬运物体轨迹，记录人工 QC。

**验收**：受保护对象未被覆盖，端点物理 QC 和谓词通过，cut 明确无真实动作和不连续。随后重做 F3，并在新 Cq 录 Query，重新验证 H1–H3。

#### G2 干扰渲染　依赖：F2、G1

复用同一 Segment renderer，恢复本段起点并逐帧写补丁；渲染 cut、睡眠后的物体和终态。

**验收**：人工检查无穿模/状态回退，帧索引和端点一致，吊柜关闭且杯子保留；公开历史只增加允许的观测。

### 后续工作（M3–M4）

| 编号 | 内容 | 验收 |
|---|---|---|
| H4 | 无历史/正确历史/匹配替换历史/文本 oracle 对照 | 同一 Cq 配对、每柜统计与置信区间，oracle 单独标记 |
| I1 | A/B/C 配对 spec、分组划分与记忆距离 | 父 episode、Key、来源片段和派生版本不跨 split |
| C4 | 机器人与搬运对象扫掠 | 帧间运动覆盖、抽样密度和误拒率有实测 |
| F4 | 导航过渡与固定 Query 位姿 | 新增物体避障、手臂路径、端点匹配；回退 cut 有记录 |
| F5 | 兼容性筛选与失败重试 | 拒绝原因、重试上限、来源拒绝率可追踪 |
| G4 | 三相机/深度/LeRobot/流式长历史 | 时间与动作对齐、内存和吞吐实测 |
| I2 | runner 缓存与本地/Slurm 执行 | 签名匹配才跳过，失败输出不作为成功缓存 |
| I3 | QC 汇总与 public/internal release | 全部必需检查通过，公开文件无答案/Query 泄露 |
| I4 | 三来源长段、批量采集、来源许可记录 | 长历史真实闭环、资源预算、实际 split 计数冻结 |

官方 BatchedEvaluator/websocket 接入放在本地 harness 稳定后，需处理任务注册、human stats、房间、scope、批量状态和历史分发，不只覆盖一个实例加载方法。

## 4. 验收清单

### M1：真实无干扰闭环

- [ ] BDDL、模板、实例与完整绑定可用，首个柜可放入/取出。
- [ ] StateAdapter 快照往返、底盘保持、scope 和谓词检查通过。
- [ ] JoyLo 从 C0/Cq 启动并 reset，终态关联正确 attempt。
- [ ] 真 Key、后处理、query_prepare 均有明确 Segment 和端点。
- [ ] Cq 恢复与录制 Query 起点一致，真 Query 完成并独立保存。
- [ ] 历史渲染首末帧刷新，最后历史状态等于 Cq。
- [ ] Policy 白名单检查通过，hold/replay/scripted 结果可解释。

### M2：一条短干扰闭环

- [ ] 一条 demo 字段、时间语义与终态可用性检查完成。
- [ ] 稀疏累计、睡眠对象、依赖和片段端点导出通过。
- [ ] 合成 C0 的布局、完整绑定、房间和物理 QC 通过。
- [ ] 新世界真实 Key、cut、干扰、Query 准备与 Query 重新验证。
- [ ] 新历史和 Cq 一致、无穿模，公开历史不含 Query。
- [ ] 工程策略结果、运行环境、耗时和失败原因有报告。

M3 另交付配对实验报告，M4 另交付批量/长历史数据与发布清单。人日估计在 M1/M2 实测后更新，分别记录编码、操作员、下载与 GPU 时间，不用未验证的固定总工期作为承诺。

## 5. 实现时重点检查

1. 单进程仿真宏和频率固定；视觉与物理检查分进程。
2. 状态缺帧保持、末动作后终态确认，不能把末帧字典当完整世界。
3. R1Pro root_link 不等于实际底盘；复位保留虚拟底盘状态并处理控制器/抓取。
4. merge_scene_files 的后模板优先行为必须显式处理。
5. metadata 变更后刷新 object_scopes；物理 step 不更新缓存 task.success。
6. include_obs=False、自动 reset 关闭、Policy 不接收奖励和调试 info。
7. Open 阈值由关节范围/方向标定；事件考虑物理子步、并列和重复。
8. 原始物理属性、动作语义、相机刷新与频率都属于复现配置。
9. 快照 JSON/list 与 tensor 互转通过统一 codec，避免各模块自行转换。
10. 候选副本静置只做检查；所有采用的状态修改进入显式段和历史。
