# Memory Bench 遥操使用说明

本说明面向使用 GELLO 和 JoyCon 采集 Memory Bench 数据的操作者。一份场景初态称为一个“实例”；在该实例中依次完成所有 Key 和最后的 Query，构成一轮采集（demo）。Key 和 Query 的具体要求以窗口左上角指令为准。

## 启动前准备

- 使用已安装的 `behavior` conda 环境、可用的 NVIDIA GPU 和图形桌面。
- 接好 GELLO，连接左右 JoyCon；连接与校准方法见 [JoyLo 说明](../../joylo/README.md)。
- 确认所选任务已完成实例准备。若尚未准备，先在 `memory_bench/scripts/prepare_session.sh` 中选择 `MB_CONFIG`，确认采样数量和 GPU 设置，再从仓库根目录运行 `bash memory_bench/scripts/prepare_session.sh`。
- 在 `memory_bench/scripts/collect.sh` 中选择同一份 `MB_CONFIG`，确认 GPU 和窗口设置适合当前机器。
- 在 `memory_bench/scripts/run_joylo.sh` 中确认 GELLO 串口 `GELLO_PORT`。默认连接本机 `127.0.0.1:6001`；跨机器使用时须修改客户端地址。

## 启动采集

两个终端均进入仓库根目录，并激活 `behavior` 环境。

终端一启动仿真窗口：

```bash
conda activate behavior
bash memory_bench/scripts/collect.sh
```

终端二启动硬件控制：

```bash
conda activate behavior
bash memory_bench/scripts/run_joylo.sh
```

每次启动从实例 1 开始，随后用 **Home** 切换到下一已准备实例。切换实例无需重启两个程序。

加载、回滚或切换任务后，GELLO 会自动移动到与仿真手臂一致的姿态。等待右下角从 `Waiting for GELLO to align.` 变为 `Ready. Press Right (JoyCon) or X (keyboard) to start.`，再按 JoyCon **右方向键**或键盘 **X** 开始。对齐完成后仍需手动开始。

## 移动与操作物体

| 控件 | 操作 |
| --- | --- |
| GELLO 双臂 | 控制机器人双臂 |
| 左摇杆 | 移动底盘 |
| 右摇杆 | 转向、调整躯干倾斜 |
| 上 / 下方向键 | 调整躯干高度 |
| ZL / ZR | 控制左右夹爪开合 |
| L / R | 锁定对应侧手腕 |
| 减号 / 加号 | 锁定对应侧手臂上部关节 |
| JoyCon **B** | 切换视角 |
| JoyCon **A** | 切换无关物体的显示与任务物体高亮 |

## 采集按键

键盘操作前，先点击 OmniGibson 窗口使其获得焦点。左、右方向键均指左 JoyCon 的方向键。

| 按键 | 用途 |
| --- | --- |
| JoyCon **右方向键** / 键盘 **X** | 开始或恢复当前子任务 |
| JoyCon **左方向键** | 检查并保存当前子任务；保存通过后准备下一子任务 |
| 键盘 **P** | 暂停 |
| JoyCon **X** | 录制中保存检查点；暂停时恢复；选中检查点后确认回滚 |
| JoyCon **Y** | 循环选择当前子任务的检查点，再按 JoyCon X 回滚 |
| 键盘 **R** | 从第一个 Key 重录当前实例，新建下一轮 demo |
| JoyCon **Home** | 切换到下一已准备实例 |
| 键盘 **Esc** / JoyCon **Capture** | 退出采集 |

注意区分 JoyCon X 与键盘 X：保存检查点、确认回滚使用 **JoyCon X**。本采集模式中，左、右方向键用于保存和开始，Home 用于切换实例。

## 完成一轮采集

1. 等待对齐完成，按 **右方向键**或键盘 **X**，看到 `Recording started.` 后开始操作。
2. 按左上角指令完成当前 Key。任务目标未满足时文字为红色，满足后变绿；绿色表示目标满足，还需手动保存。
3. 放下手中物体，停止移动并等待机器人停稳，再按 **左方向键**。保存还要求空手、稳定且无碰撞。若任务要求关闭柜门，须先关好。
4. 等待保存完成。系统保留物体状态，将机器人恢复到本实例的公共起点，并准备下一个 Key 或 Query。再次等待对齐，按 **右方向键**或键盘 **X** 开始下一段。
5. 最后一个 Query 完成后，同样按 **左方向键**保存。看到 `Collection complete. Press Home for the next instance.` 才表示整轮完成。
6. 按 **Home** 进入下一实例，或按 **R** 重录当前实例。所有实例完成后按 **Esc** 退出。

保存和准备下一段期间无需操作。恢复起点和等待开始的过程不计入录制；开始下一段必须单独按开始键。

## 暂停、回滚与重录

**临时暂停：** 按键盘 **P**。恢复时让底盘和躯干输入回中，等待 GELLO 对齐，再按 **右方向键**或键盘 **X**。子任务中途暂停时可以继续抓着物体。

**局部重试：** 在关键操作前按 JoyCon **X** 保存检查点。出错后按 **Y** 选择检查点，可继续按 Y 换一个，再按 JoyCon **X** 确认回滚。机器人和物体恢复后保持暂停，等待对齐，再按开始键继续。回滚后的重试会替换该检查点之后的正式操作。

每个子任务开始时都有入口检查点；只能回滚当前子任务，保存并切换后不能回到上一段。

**整轮重录：** 按键盘 **R**，从该实例的初始场景和第一个 Key 重新开始，生成新的 `demo_NNN`，旧文件保留。

**切换或退出：** Home 加载下一已准备实例；若没有下一实例，会提示并留在当前实例。Esc / Capture 退出。整轮未完成时执行 R、Home 或退出，当前未保存段会被丢弃，已保存段保留，但该轮不能作为完整采集使用。

## 提示与常见问题

右下角的 `Instance 1 | demo_001 | Key 1` 表示当前实例、轮次和子任务。常见提示及处理方法：

| 提示或现象 | 处理方法 |
| --- | --- |
| `Waiting for GELLO to align.` 持续显示 | 确认硬件客户端仍在运行、GELLO 已对齐，底盘和躯干输入已回中；不要反复按开始键 |
| `Paused. Press Right (JoyCon) or X (keyboard) to resume.` | 已暂停且可以恢复，按右方向键或键盘 X |
| `Cannot save: task goal not satisfied.` | 按任务指令继续完成目标，再按左方向键 |
| 保存提示空手、稳定或碰撞检查失败 | 松开夹爪、让机器人停稳或移开接触位置，再按左方向键 |
| `Saving recording. Please wait.` | 等待保存结束 |
| 保存提示文件写入或记录更新失败 | 保持当前程序打开，查看终端错误；处理存储问题后按左方向键重试 |
| 键盘没有反应 | 先点击仿真窗口，再按键 |

## 录制保存在哪里

录制位于所选 YAML 的 `paths.output_root` 下：

```text
recordings/<session_id>/instance_0001/demo_001/
├── manifest.json
├── key_01.hdf5
├── key_01_entry.json
├── key_01_exit.json
├── query.hdf5
├── query_entry.json
└── query_exit.json
```

多个 Key 会增加 `key_02.hdf5` 等文件。每次重录使用递增的 demo 编号，不覆盖旧文件。`manifest.json` 中 `complete` 表示整轮完成，`in_progress` 表示尚未完成，`aborted` 表示已中止。保留整轮目录及其中的配套文件，不要混用不同 demo 的 Key 和 Query。

## 回放检查

无需连接遥操硬件即可生成回放视频。在仓库根目录的 `behavior` 环境中运行，将示例输入替换为实际录制路径：

```bash
OMNIGIBSON_HEADLESS=1 python -m memory_bench.tools.replay_data \
  /path/to/instance_0001/demo_001/key_01.hdf5 \
  --config memory_bench/configs/mb000_retrieve_bottle_of_milk.yaml \
  --output-dir /path/to/previews/instance_0001/demo_001
```

使用与采集一致的配置。每次回放一个文件，其他 Key 和 Query 分别运行。指定的输出目录会生成 `<文件名>_replay.mp4` 和同名 PNG；建议按实例和 demo 分目录保存，避免同名输出冲突。未指定 `--output-dir` 时，默认输出到仓库的 `memory_bench/recordings/`。重新生成已有预览需加 `--overwrite`。

视频包含左右腕、头部和外部视角。检查抓取是否成功、物体是否落稳、柜门状态是否正确，以及是否有轨迹跳变或严重遮挡。只查看局部操作时可加 `--start-frame 300 --end-frame 600`，帧号从 0 开始，结束帧不包含在输出内。回放需要可用的 GPU 和 Isaac Sim；视频供人工检查，不自动判定任务是否合格。
