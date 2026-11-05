# 八项任务实现说明

已新增 **8 个配置、8 个任务模块、20 个 Key/Query BDDL**。场景分配改为上层 3 项、下层 2 项、单层 3 项；详细活动与变体见 [任务设计](task_candidates.md)。

| 配置编号 | 任务模块（`tasks/` 下，不带 mb） | 场景 |
|---|---|---|
| mb000 | `retrieve_stored_flashlight.py`：藏好手电筒后取回 | 上层卧室 2 |
| mb001 | `return_borrowed_cup.py`：杯子归还原位 | 上层客厅 |
| mb002 | `resume_cabinet_storage.py`：记住原开柜状态 | 单层工具间 |
| mb003 | `resume_toolbox_packing.py`：记住原开箱状态 | 下层厨房 |
| mb004 | `restore_coffee_supplies.py`：咖啡用品整合归位 | 单层厨房 |
| mb005 | `restore_tea_supplies.py`：茶歇用品整合归位 | 上层客厅 |
| mb006 | `retrieve_relocated_cup.py`：杯子连续移动后取回 | 下层厨房 |
| mb007 | `retrieve_packed_flashlight.py`：手电筒随盒入柜后取回 | 单层工具间 |

配置名为 `mb000_retrieve_stored_flashlight.yaml` 等；编号只放在配置文件名中。`session_id` 和 BDDL 保持 `mb_xxx`，沿用现有接口。

## 必要的代码修改

- **`tasks/base_task.py`**：实例准备时按变体放置物体、设置开合；补充柜顶和桌面区域检查。区域按物体底座位置判断，避免开盖导致包围盒中心变化。K/Q 之间不调用初态设置。
- **`collection/prepare_session.py`**：固定家具绑定到明确场景实例；mb000/003 的同款双工具箱按角色分别共享，避免跨 Key/Query 串箱。
- **`collection/sample_instances.py`**：在保存实例前应用并检查该变体初态，避免所有实例仍使用同一个 BDDL 默认状态。
- **任务注册和启动脚本**：注册八项，默认配置改为 mb000。MU2 直接使用已有嵌套 `inside` 条件和物体写集合；没有新增搬运或回放框架。

**不支持间隔子任务切段。** I 继续使用完整官方录制及原 `task_id` 接口。旧吊柜任务保留给既有数据和回归测试，不属于首批八项。

## 使用

在 `scripts/prepare_session.sh`、`collect.sh`、`build_episodes.sh`、`export_session.sh` 中选择同一 `MB_CONFIG`，沿用原流程。检查 YAML 数据路径、脚本 GPU 和采样数量；准备脚本当前仍以 5 个实例联调。I 未下载时先运行 `python -m memory_bench.tools.download_rawdata --config <配置路径>`。重新准备使用新的输出目录。

## 已验证与限制

- 八项配置和 20 个 BDDL 可解析；资产类别、模型文件、固定家具房间、变体条件衔接及完整 I 的绑定隔离通过静态检查。
- 本次五个相关测试文件合计 **101 通过、11 失败**；新增的 4 项检查全部通过。11 个失败用例在修改前 HEAD 副本也失败，涉及已有 CLI 参数、recipe 跳过和旧清单字段预期，没有为本次任务扩展修改这些行为。
- 在 behavior 环境使用 GPU0，**mb001 两个变体的 6 项物理状态检查通过**；其余七项因 GPU0 空闲显存降至约 2.4 GiB 未运行。此次不含机器人抓放、视觉或导航验收，详见 [GPU 验证记录](gpu0_validation_20261105.md)。单层厨房 mb004 仍有较长 I 导航。
- 实现了阶段完成判定；首次选位、错误搜索次数等细粒度策略评价尚未新增。环境未安装 Ruff，本次未运行 Ruff。
