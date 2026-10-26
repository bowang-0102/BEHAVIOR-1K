# Memory Bench 长任务遥操采集说明

本说明适用于 `memory_bench/scripts/collect.sh` 启动的采集模式。同一实例的多个 Key 和 Query 在连续世界中依次采集，每个子任务独立保存 HDF5；每个子任务内可以保存检查点、回滚和暂停。切换子任务由两个独立按键完成。普通 JoyLo 模式保留原来的手电筒按键。

机器人位置和朝向沿用实例已采样的第一 Key 起点。每轮采集启动或 Home 切换实例时，使用当前 `joylo/gello/utils/og_teleop_cfg.py` 的 reset 关节配置，停稳并通过检查后建立本轮公共起点；全部 Key/Query 都恢复到这个起点。修改 reset 后重启采集即可，无须重新准备实例。公共起点只保存在内存中，录制继续使用现有入口快照和 HDF5。

启动、暂停、回滚、切换子任务或 Home 切换实例后，GELLO 自动对齐仿真手臂姿态，后台持续检查同步。启动、回滚、切换子任务或实例时，JoyCon 的夹爪开合状态同步到服务端恢复后的目标，普通暂停保留当前开合状态。未对齐时显示“Waiting for JoyLo to align...”，对齐后显示“Ready. Press Right (JoyCon) or X (keyboard) to start”。对齐不会自动开始录制；按开始键后，再用新的硬件数据确认同步，通过后释放 GELLO 定位并继续仿真。

左上角常驻显示一条 `Key 1: 任务指令` 或 `Query: 任务指令`。Key 使用当前变体对应的 `key_instructions`，Query 使用 `query_instruction`。任务目标未满足时整条为红色，全部满足后变绿。暂停保留显示，回滚和切换子任务或实例后重新判断颜色。变绿后放下物体、停稳，再按左方向键检查并保存；保存失败原因仍在右下角提示。

Query 过程中可以打开任务涉及的任意柜子，完成状态由当前 `goal` 决定；若 `goal` 要求柜门关闭，保存前需关好。

运行 `prepare_session.sh` 时，先准备和检查间隔任务录制，在 `intervent_assignments.json` 中固定每个实例的分配，再生成对应 TRO。采集加载的间隔物体已处于这些录制的入口状态；采集不会回写模板或 TRO。原始采样文件、已有变体分配和源 HDF5 均保持不变。

## 大任务和子任务的边界

- **大任务**：当前实例的全部 Key 和 Query，按 Key 编号递增，最后采集 Query。启动时从实例 1 开始；一轮采集（demo）对应一个 `demo_NNN/` 目录，`manifest.json` 记录其状态和文件关联。
- **子任务**：一个 Key 或 Query。检查点只属于当前子任务；完成并切换后，不能回滚到上一子任务。
- **子任务切换**：校验当前出口，先保存并关闭当前子任务的 HDF5，再只恢复机器人到下一子任务的公共起点，保留物体状态和前序任务成果。恢复过程和等待开始均不写入任何子任务。
- **实例切换**：Home 加载下一已准备实例，在其目录中建立新 run；同一服务端、硬件客户端和窗口持续使用，加载后暂停等待开始。

## 按键表

键盘操作需要先让 OmniGibson 窗口获得焦点。手柄的左、右方向键指左 JoyCon 上原来控制两盏手电筒的按键。

| 按键 | 功能 | 作用范围 |
|---|---|---|
| JoyCon **左方向键** | 请求完成当前子任务；检查通过后独立保存 HDF5，恢复下一子任务的机器人起点，并停下来等待开始 | 子任务边界 |
| JoyCon **右方向键** | 开始已准备好的子任务；暂停或回滚后，也可以恢复当前子任务 | 当前子任务 |
| JoyCon **X** | 正在遥操时保存检查点；暂停时开始或恢复；已用 Y 选中检查点时执行回滚 | 当前子任务 |
| JoyCon **Y** | 循环选择当前子任务内的检查点，随后按 JoyCon X 执行回滚 | 当前子任务 |
| 键盘 **X** | 开始或恢复遥操，确认 GELLO 与机器人状态同步后进入录制；不负责结束子任务 | 当前待采或暂停的子任务 |
| 键盘 **P** | 暂停，也会取消正在等待的开始同步 | 当前子任务 |
| 键盘 **R** | 从第一个 Key 重录当前实例的整个大任务，新建下一个 demo；保留旧文件，恢复完整初始场景后暂停等待开始 | 当前实例 |
| JoyCon **Home** | 加载下一已准备实例并暂停；旧 run 未完成时丢弃当前缓冲并标为中止，已保存文件保留；无下一实例时只提示 | 实例边界；每次按下只推进一次，沿用冷却限制 |
| 键盘 **Esc** / JoyCon **Capture** | 丢弃当前未完成段并退出，未完成 run 标记为中止；已完成文件及 run 完成状态保留 | 整个大任务 |
| JoyCon **B** | 切换视角 | 显示 |
| JoyCon **A** | 隐藏或显示无关物体，并切换任务相关物体的高亮提示；相关物体覆盖本轮 Key 和 Query 的绑定对象 | 显示 |
| 摇杆、上下方向键、ZL/ZR、L/R、减号/加号 | 保留原来的底盘、躯干、夹爪和锁臂控制 | 遥操 |

`N` 不再用于任务推进。在此采集模式中，左右方向键不再切换手电筒；普通 JoyLo 模式仍使用原来的手电筒映射。

## 一轮采集流程

1. 在 `memory_bench/scripts/collect.sh` 选择 `MB_CONFIG`，采集固定从实例 1 开始。GPU 和窗口设置保留在 `collect.sh`，连接与硬件参数沿用 `run_joylo.sh`；服务端默认地址为 `127.0.0.1:6001`。分别启动服务端和硬件客户端，无需逐实例重新启动：

   ```bash
   bash memory_bench/scripts/collect.sh
   # 另一个终端
   bash memory_bench/scripts/run_joylo.sh
   ```

2. 等待完整场景加载，机器人恢复公共起点并停稳，GELLO 自动对齐。看到“Ready. Press Right (JoyCon) or X (keyboard) to start”后，按 **右方向键**或键盘 **X** 开始遥操。
3. 长子任务中可以按 JoyCon **X** 保存检查点。需要重试时，按 **Y** 选择检查点，再按 JoyCon **X** 回滚。回滚后保持暂停，再按右方向键或 X 同步并继续。
4. 子任务完成后放下手中物体，让机器人停稳，按 **左方向键**。系统检查任务完成条件、空手、稳定和碰撞状态；不满足时提示原因，继续操作后可以重试。
5. 检查通过后，系统保存出口快照，将当前文件写完、刷新到磁盘并关闭，再恢复下一子任务的机器人起点。保存失败时保留缓冲和当前世界，保持暂停；按左方向键重试。此时不会自动开始下一段；按 **右方向键**或 X 确认开始。
6. 最后一个 Query 完成后按左方向键。检查通过并保存 Query 后，当前 run 标为 `complete`，暂停等待 **Home** 加载下一已准备实例，或按 **R** 重录当前实例；再按右方向键或键盘 X 开始。最后一个已准备实例完成后也保持暂停，按 **Esc** 退出。

左右方向键同时按下时，只处理结束请求；下一子任务仍需单独按开始键确认。

## 回滚的范围和记录

每个子任务开始时自动保存一个入口检查点。手动检查点和 JoyLo 配置启用的自动检查点都保留。切换到下一子任务时清空上一段的检查点，所以 `Y → X` 只能回到当前子任务内的位置。

回滚同时恢复机器人和物体的世界状态，并从正式轨迹中移除该检查点之后的动作。被移除的尝试保存在当前子任务 HDF5 的 `data/demo_0/rollbacks` 分支中；统一后处理只使用正式轨迹，不将失败分支导入训练序列。已完成子任务的文件保持不变。

任务中途暂停或回滚时允许继续抓着物体。恢复时要求底盘/躯干输入回中、手臂与 GELLO 同步；躯干和夹爪继续暂停前尚未完成的目标，不会因为物理动作被暂停而永久等待到位。新子任务入口仍要求空手并对齐起点；出口仍要求空手、稳定、无碰撞。

## R 重录、Home 切换实例和 Esc 退出

**R 从头重录当前实例。** 例如当前为 `instance_0001/demo_001`，按 R 后在同一实例下建立 `demo_002`，重新加载实例初始场景，从第一个 Key 开始。旧轮未完成时丢弃当前未保存段并将旧轮标为 `aborted`，已保存文件保留；旧轮已完成时保持 `complete`。新轮清空进度和检查点，暂停等待 GELLO 对齐，再按右方向键或键盘 X 开始。长按 R 不会连续新建 demo。

**Home 加载下一已准备实例。** 当前 run 尚未完成时，丢弃未完成段的缓冲和检查点，将旧 run 标为 `aborted`，保留已保存文件。下一实例在同一父目录下的 `instance_NNNN/demo_NNN/` 中建立新 run 和 `manifest.json`，加载后暂停，等待右方向键或键盘 X。已完成 run 保持 `complete`。没有下一实例时留在当前实例并提示，不丢弃当前缓冲；长按 Home 不会连续推进，仍需满足原来的冷却限制。

**Esc / Capture 退出。** 它保留已完成文件，丢弃当前未完成段，将未完成 run 标为 `aborted`；已完成并等待 Home 的 run 保持 `complete`。中止时不生成未完成段的 HDF5 或出口快照。后处理只接受包含全部 Key 和 Query 的完整 run，不能跨 run 拼接采集文件。

默认输出位于 session 的 `paths.output_root` 下：

```text
recordings/<session_id>/
└── instance_0001/
    ├── demo_001/           # 例如第一段采集中按 Esc 退出
    │   ├── manifest.json        # status: aborted；未完成段不写 HDF5
    │   └── key_01_entry.json
    └── demo_002/
        ├── manifest.json        # status: complete 后可合成 Bridge
        ├── key_01.hdf5          # 每个文件只有 data/demo_0，局部动作区间 [0,N)
        ├── key_01_entry.json
        ├── key_01_exit.json
        ├── query.hdf5
        ├── query_entry.json
        └── query_exit.json
```

录制统一保存到上述标准目录。每次进入实例时选择递增的 run 编号，不覆盖旧文件；Home 推进后使用相邻的 `instance_NNNN/` 目录。多 Key 会额外产生 `key_2.hdf5` 等文件。

新流程不生成 `marks.json` 或 `marks.yaml`。`manifest.json` 记录整轮完成状态、TRO 身份及路径、各独立文件和入口/出口快照、转场检查结果；它不再承担从长录制中定位切段边界的工作。保留它是为了防止中断、切换实例或误用素材造成混轮。原始 HDF5 保存 N 个动作和对应的 N 个动作前状态；出口 JSON 补充最后一个动作后的终态。

## 单独回放 Key / Query 或最终 episode

在仓库根目录、`behavior` 环境中运行，传入 session 配置和单个 Key / Query 或最终 episode HDF5：

```bash
python -m memory_bench.tools.replay_data \
  /path/to/instance_0001/demo_001/key_01.hdf5 \
  --config memory_bench/configs/mb_retrieve_chocolate_from_top_cabinet.yaml
```

每次只回放一个文件内的 `data/demo_0`。检查一轮采集时，分别运行各个 `key_XX.hdf5` 和 `query.hdf5`；不要求整轮已完成。

最终 episode 使用相同命令，将输入路径替换为 `episodes/<session_id>/episode_XXXXXXXX.hdf5` 即可；脚本自动识别 `compose_episodes` 输出的 `state_json` 格式。

脚本直接使用 HDF5 内的入口场景和机器人配置，不需要 `--task`、官方任务场景、episode recipe 或 JoyLo 硬件。默认无窗口运行，需要可用的 NVIDIA GPU 和 Isaac Sim。它逐帧恢复状态，复用正式导出的渲染逻辑，不推进物理、不重新执行动作，也不改变源录制或场景文件。支持采集器保存的 R1Pro Key / Query 和最终合成的 episode；运行时增删物体、粒子系统或非空 `init_metadata` 会明确报错。

每个录制旁的 `replay/` 目录会生成 `<文件名>_replay.mp4` 和 `<文件名>_replay.png`。视频上排为左腕、头部、右腕，下排为跟随底盘的外部视角，并标注帧号和时间；PNG 保存最后渲染的画面。完整回放优先追加 HDF5 内置的真实终态；最终 episode 的 `state_json[N]` 已包含此状态，不需要单独的 `_exit.json`。没有内置终态时使用同目录的 `<文件名>_exit.json`；两者都缺失时明确提示，不把最后一个动作前状态当作动作结果。默认不覆盖已有输出，需要重做时加 `--overwrite`。

常用选项：

- `--check-only`：只读取录制元数据，打印任务名、帧数、时长和出口快照路径，不启动仿真，不扫描整条轨迹的数值。
- `--start-frame 300 --end-frame 600`：只输出原始帧区间 `[300, 600)`，方便查看可疑片段；帧号从 0 开始。
- `--resolution 480 --n-render-iterations 3`：调整每路画面高度和每帧渲染次数。
- `--output-dir /path/to/previews`：指定输出目录。

回放只使用正式轨迹，不把 `rollbacks` 中移除的失败尝试混入视频。通过视频人工检查抓取、柜门、物体状态、轨迹跳变和相机遮挡；该工具不自动判定任务成功或碰撞情况。

## 导航与拼接分开运行

```bash
# Step 3：默认每实例只取 demo_001，生成 B 原始录制和 recipe
bash memory_bench/scripts/build_episodes.sh
# Step 4：直接解码原始 K/I/Q/B，拼接、回放和导出
bash memory_bench/scripts/export_session.sh
```

YAML 的 `composition.demo_id: 1` 选择第一轮采集。第一轮缺失或未完成时停止，其他轮需在配置中显式选择，不能混轮；顺序种子使用 `composition.order_seed`。失败记录保留。多 Key 使用 `key_02.hdf5` 等，HDF5 内部仍只有 `data/demo_0`。入口和出口 JSON 补齐真实动作边界，不重复保存整条规范化轨迹。

拼接不会合成导航。最终 `episodes/<session_id>/episode_XXXXXXXX_query_start.json` 保存插入 I/B 后的真实 Query 起始世界，recipe 记录路径和帧号，供后续评测独立恢复。采集阶段的 `query_entry.json` 仍作为原始边界保留。
