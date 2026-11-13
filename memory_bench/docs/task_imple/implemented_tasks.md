# 已实现任务：mb000–mb007

mb000–mb007 共八项任务已实现任务定义、BDDL、配置和注册，覆盖空间回忆、状态回忆、多条记忆整合、位置记忆更新，以及物体搬动后的来源回忆。各任务的场景、操作、变体与记忆要求如下；通用约束见 [设计与实施要求](requirements.md)。

K（Key）是提供历史信息的关键片段，I 是间隔任务，Q（Query）是需要利用历史信息完成的目标任务。mb000–mb003 为 L1，使用 `1K + 2I + Q`；mb004–mb007 为 L2，使用 `2K + 2I + Q`。Q 最后执行，双 Key 任务中 K₁ 先于 K₂，相邻片段由 Bridge 衔接。

SP 为空间回忆，ST 为状态回忆，MI 为多条记忆整合，MU 为记忆更新；mb006 与 mb007 的具体记忆要求见各任务介绍。

所有任务在 K/Q 之间保留物体身份并连续继承世界状态。同一任务的 Q 指令不随变体改变；历史答案应来自 Key，不能由 Q 入口的摆位、姿态或残留痕迹替代。

英文任务名统一使用“动作动词原形＋具体物体＋必要位置或历史关系”，如 `Return the book to its original bookcase`。任务标识使用 `mb00x_<动作短语>`，Key/Query 的 BDDL 活动名使用 `mb_<动作短语>`，统一采用小写下划线格式。

| 编号 | 类别与序列 | K/Q 房间 | Key | Query |
|---|---|---|---|---|
| mb000 | SP · L1 · 1K + 2I + Q | lower / kitchen_0 | 牛奶从餐桌存入指定冰箱或微波炉，关好门，微波炉保持关机 | 从之前存放的位置取回牛奶，放回餐桌并关好门 |
| mb001 | SP · L1 · 1K + 2I + Q | upper / living_room_0 | 从来源书架顶板取书到阅读桌 | 归还原书架顶板 |
| mb002 | ST · L1 · 1K + 2I + Q | upper / bedroom_0 | 同一支手电筒从书桌暂放到床面并关闭；记住原先开关状态 | 原先开则归书桌并开启，原先关则收放矮书柜顶面并关闭 |
| mb003 | ST · L1 · 1K + 2I + Q | lower / living_room_0 | 同一台收音机从茶几暂放到沙发并关闭；记住原先开关状态 | 原先开则放电视柜并开启，原先关则归茶几并关闭 |
| mb004 | MI · L2 · 2K + 2I + Q | upper / living_room_0 | 分别从独立来源取杯子、纸巾盒到茶几 | 两件用品各归原来源 |
| mb005 | MI · L2 · 2K + 2I + Q | single / kitchen_0 | 从微波炉顶部取碗到吧台，再从来源台面取砧板到吧台 | 取来的碗归微波炉顶部，砧板归原台面，另一只碗留吧台 |
| mb006 | MU · L2 · 2K + 2I + Q | single / kitchen_0 | 一根熟香肠装罐存入吊柜，再将密封肉罐搬到另一个吊柜 | 从最新存放柜取肉罐到吧台，保持密封及香肠在罐内，关好全部柜门 |
| mb007 | MU · L2 · 随容器位置更新＋来源回忆 · 2K + 2I + Q | upper / bedroom_1 | 手柄从床头柜放进床上的盒子，再连盒搬到脚凳 | 手柄归还最初床头柜，盒子留在脚凳 |

`lower`、`upper`、`single` 分别表示 `house_double_floor_lower`、`house_double_floor_upper`、`house_single_floor`。mb006 要求使用更新后的隐藏位置；mb007 保留 MU 分组，但 Q 仍依赖初始来源，不要求凭记忆寻找隐藏的最新位置。

## mb000 · Retrieve the stored bottle of milk

任务标识为 `mb000_retrieve_bottle_of_milk`。入口：[任务代码](../../tasks/mb000_retrieve_bottle_of_milk.py)、[配置](../../configs/mb000_retrieve_bottle_of_milk.yaml)、[Key BDDL](../../../bddl3/bddl/activity_definitions/mb_store_bottle_of_milk/problem0.bddl)、[Query BDDL](../../../bddl3/bddl/activity_definitions/mb_retrieve_bottle_of_milk/problem0.bddl)。

### 场景与资产

使用 `house_double_floor_lower / kitchen_0`。公共取放点为厨房现有餐桌，冰箱和微波炉作为两个独立存放位置，不要求容器同类。任务只做牛奶存取，不倒奶、不加热。

| 资产 | 模型或场景对象 | 用途 |
|---|---|---|
| 牛奶 | `bottle_of_milk/mrejrs`，外包尺寸约 13.7 × 5.1 × 4.5 cm | 唯一新增物品；指令统一称 `bottle of milk` |
| 冰箱 | `fridge_petcxr_0` | A 变体的存放位置 |
| 微波炉 | `microwave_hjjxmi_0` | B 变体的存放位置，保持关机 |
| 餐桌 | `breakfast_table_xftrki_0`，桌高约 68 cm | 所有变体共同的起始与归还位置 |

冰箱、微波炉和餐桌各自使用固定的 BDDL 角色绑定，A/B 不交换不同类别的物体。两变体分别以冰箱或微波炉为存放位置；同一件牛奶贯穿 K/Q。

### 操作与指令

- **初态：** 牛奶在餐桌上，冰箱和微波炉的门均关闭，微波炉关机。
- **Key A：** “把餐桌上的一瓶牛奶放进冰箱并关门。”
- **Key B：** “把餐桌上的一瓶牛奶放进微波炉并关门，保持微波炉关机。”
- **Query 固定指令：** “取回之前存放的一瓶牛奶并放到餐桌上，然后关门，不开启所用电器。”

代码中的 Query 指令为：

> Retrieve the bottle of milk you stored earlier and place it on the dining table, then close the door without turning on the appliance.

### 条件与记忆要求

| 边界 | 条件 |
|---|---|
| Key 开始 | `ontop(牛奶, 餐桌)` |
| A：Key 完成 / Query 开始 | `inside(牛奶, 冰箱)` |
| B：Key 完成 / Query 开始 | `inside(牛奶, 微波炉)` |
| Query 完成 | `ontop(牛奶, 餐桌)` |

所有边界还要求 `not open(冰箱)`、`not open(微波炉)` 和 `not toggled_on(微波炉)`。仅使用现有 `ontop`、`inside`、`open`、`toggled_on` 条件，不增加专用区域、层级判定或公共流程分支。

任务定义按 A/B 精确检查牛奶的存放位置。Query 需要记住之前使用的电器，打开对应容器取出牛奶。

Query 措辞不随 A/B 改变，不提示牛奶所在的电器；变体标签不进入策略观测。

间隔任务为 `turning_on_radio`（`living_room_0`，task 0）和 `putting_shoes_on_rack`（`corridor_0`，task 22）。K/Q 各一次牛奶转移及目标电器的开合；片段切换连续继承非机器人世界。

## mb001 · Return the book to its original bookcase

任务标识为 `mb001_return_book_to_original_bookcase`。入口：[任务代码](../../tasks/mb001_return_book_to_original_bookcase.py)、[配置](../../configs/mb001_return_book_to_original_bookcase.yaml)、[Key BDDL](../../../bddl3/bddl/activity_definitions/mb_place_book_on_desk/problem0.bddl)、[Query BDDL](../../../bddl3/bddl/activity_definitions/mb_return_book_to_original_bookcase/problem0.bddl)。

### 场景与资产

使用 `house_double_floor_upper / living_room_0`。书为 `hardback/acbrnv`，约 22.3 × 17.2 × 3.1 cm；来源书架为一排两端的 `bookcase_otwukr_0/2`，约 30 × 76 × 105 cm；公共阅读桌为 `desk_iotfzl_0`，高约 72.8 cm。使用书架顶板取还书，不执行竖放或插缝。

| 变体 | 来源书架绑定 | 公共阅读桌 |
|---|---|---|
| A | `bookcase_otwukr_0` | `desk_iotfzl_0` |
| B | `bookcase_otwukr_2` | `desk_iotfzl_0` |

`bookcase.n.01_1` 表示来源书架。BDDL 声明四个现有书架；`bookcase_otwukr_1/3` 为受保护的背景支撑物。同一本书贯穿 K/Q。

### 操作与指令

- **初态：** 目标书在来源书架顶板，另一候选书架顶板与公共阅读桌为空；不留书本轮廓或其他来源提示。
- **Key：** “从书架顶板取书，放到阅读桌上备用。”A/B 使用相同指令，来源由初态决定。
- **Query 固定指令：** “把桌上的书放回你之前取书的那一个书柜顶板。”

代码中的 Query 指令为：

> Return the book from the desk to the top of the exact same bookcase you took it from earlier.

### 条件与记忆要求

| 边界 | 条件 |
|---|---|
| Key 开始 | `ontop(书, 来源书架)` |
| Key 完成 / Query 开始 | `ontop(书, 阅读桌)` |
| Query 完成 | `ontop(书, 来源书架)` |

A/B 交换来源书架绑定，Query 需要记住 Key 取书的书架。顶板取放由指令和布局引导，条件仍使用原生 `ontop`，不增加专用坐标区、层号或角度规则。桌面上的书姿态与落点应与来源独立，K/Q 之间不重摆。

间隔任务为 `organizing_art_supplies`（`bedroom_0`，task 59）和 `getting_organized_for_work`（`bedroom_2`，task 28）。K/Q 各一次书本转移，完成 Key 后两个来源位置均为空；I/Bridge 不改变或重新揭示来源。

## mb002 · Place the flashlight by its previous state

任务标识为 `mb002_place_reading_lamp_by_previous_state`。入口：[任务代码](../../tasks/mb002_place_reading_lamp_by_previous_state.py)、[配置](../../configs/mb002_place_reading_lamp_by_previous_state.yaml)、[Key BDDL](../../../bddl3/bddl/activity_definitions/mb_move_reading_lamp_to_bed/problem0.bddl)、[Query BDDL](../../../bddl3/bddl/activity_definitions/mb_place_reading_lamp_by_previous_state/problem0.bddl)。

### 场景与资产

使用 `house_double_floor_upper / bedroom_0`，与官方 task 37 `clean_a_trumpet` 使用同一房间。只插入一支单刚体手电筒 `flashlight/jtrbkc`，保持原尺寸（scale=1.0，长约 23.7 cm，最大直径约 5.5 cm）。手电筒替代阅读灯，保留原有任务流程、任务标识和活动名；任务代码及 BDDL 内部统一使用 `flashlight.n.01_1`。灯光通过 memory_bench 本地同步逻辑跟随 `ToggledOn` 状态。

| 用途 | 固定物体 |
|---|---|
| 初始位置／继续使用 | 书桌 `desk_aduafr_0` |
| 共同暂存位置 | 床面 `bed_klcjcy_0` |
| 收起位置 | 矮书柜顶面 `bookcase_kdnyfh_0` |

A/B 的物体绑定、模型、尺寸和支撑物完全相同，只改变同一支手电筒在 Key 开始时的开关状态。

### 操作与条件

| 边界 | A：原先开启 | B：原先关闭 |
|---|---|---|
| Key 开始 | 手电筒在书桌，开启 | 同一支手电筒在书桌，关闭 |
| Key 完成／Query 开始 | 手电筒在床面，关闭 | 手电筒在床面，关闭 |
| Query 完成 | 手电筒在书桌，开启 | 手电筒在矮书柜顶面，关闭 |

固定 Key 指令：

> Clear the desk by moving the flashlight to the bed, leaving it off.

固定 Query 指令：

> If the flashlight was on when you first found it on the desk, return it to the desk and turn it on. If it was off, put it away on top of the low bookcase and leave it off.

两个分支的 Key 和 Query 都必须搬动手电筒；A 的 Query 还需开启手电筒。只使用原生 `ontop`、`toggled_on` 及其否定，只有手电筒可写，书桌、床和书柜均受保护。

间隔任务为 `re_shelving_library_books`（`living_room_0`，task 55）和 `getting_organized_for_work`（`bedroom_2`，task 28）。task 59 `organizing_art_supplies` 使用记忆任务的同一张书桌，因此不作为本任务的 I。

**记忆要求：** 记住同一支手电筒搬动前的开关状态，决定之后搬到哪里以及是否开启。Query 的暂存落点、朝向和按钮状态不得编码 A/B。

## mb003 · Place the radio by its previous state

任务标识为 `mb003_place_radio_by_previous_state`。入口：[任务代码](../../tasks/mb003_place_radio_by_previous_state.py)、[配置](../../configs/mb003_place_radio_by_previous_state.yaml)、[Key BDDL](../../../bddl3/bddl/activity_definitions/mb_move_radio_to_sofa/problem0.bddl)、[Query BDDL](../../../bddl3/bddl/activity_definitions/mb_place_radio_by_previous_state/problem0.bddl)。

使用 `house_double_floor_lower / living_room_0`。只插入一台 `radio/vgioak`；全部支撑物复用现有家具。

| 用途 | 固定物体 |
|---|---|
| 初始位置／放回位置 | 茶几 `coffee_table_koagbh_0` |
| 共同暂存位置 | 沙发 `sofa_frlxgz_0` 的坐垫 |
| 继续使用位置 | 电视柜 `bottom_cabinet_rhdbzv_0` |

| 边界 | A：原先开启 | B：原先关闭 |
|---|---|---|
| Key 开始 | 收音机在茶几，开启 | 同一台收音机在茶几，关闭 |
| Key 完成／Query 开始 | 收音机在沙发，关闭 | 收音机在沙发，关闭 |
| Query 完成 | 收音机在电视柜，开启 | 收音机在茶几，关闭 |

固定 Key 指令：

> Move the radio from the coffee table to a sofa cushion, leaving it off.

固定 Query 指令：

> If the radio was on when you first found it on the coffee table, place it on the TV stand and turn it on. If it was off, return it to the coffee table and leave it off.

A/B 使用相同物体绑定与指令，每个分支都必须搬运。判定使用原生 `ontop`、`toggled_on` 及其否定；只有收音机可写，茶几、沙发和电视柜均受保护。间隔任务为 `hanging_pictures`（`kitchen_0`，task 34）和 `putting_shoes_on_rack`（`corridor_0`，task 22）。

**记忆要求：** 回忆同一台收音机搬动前是否开启，并据此决定目的地与最终开关状态。Key 初始画面需能看清原生开关视觉标记；Query 的摆位和朝向不得编码答案。

### 两项状态任务的准备与验收

两项任务的历史状态均指 Key 入口首次看到目标时的状态；Key 中先关机再搬动不会改变历史答案。两项任务的首次 Key BDDL 只规定公共摆放条件，历史开关状态由任务定义的 `init[variant]["key_1"]` 给出。实例准备恢复原始 TRO 后，通过通用 `sample_initial_states()` 调用原生一元状态采样接口初始化 A/B，不重新采样几何位置；稳定后同时检查公共 BDDL 和该变体初态，不满足时拒绝发布实例。初始化只发生在实例准备阶段，采集与拼接不调用该方法，不在 K/Q 之间重置状态。

Query BDDL 的 `or` 仅表示采样模板允许的两个终态；实际采集通过 `MemoryTask.check_goal()` 严格检查冻结变体，不能任选一支完成。首次 Key 和 Query 的入口均不满足本段目标，保留采集器对空任务的拒绝检查。

生成新任务前使用新输出目录，不将旧双物体任务的模板、TRO 或录制迁移为新任务。先各验证 A/B 一个实例，检查床面、沙发和书柜顶面的放置稳定性、抓取可达性、Key 状态可见性以及所有相机中的历史线索；条件测试通过不等同于实际遥操通过。

## mb004 · Put away the mug and tissue box in their original locations

入口：[任务代码](../../tasks/mb004_put_away_mug_and_tissue_box.py)、[配置](../../configs/mb004_put_away_mug_and_tissue_box.yaml)、[Key 1 BDDL](../../../bddl3/bddl/activity_definitions/mb_place_mug_on_coffee_table/problem0.bddl)、[Key 2 BDDL](../../../bddl3/bddl/activity_definitions/mb_place_tissue_box_on_coffee_table/problem0.bddl)、[Query BDDL](../../../bddl3/bddl/activity_definitions/mb_put_away_mug_and_tissue_box/problem0.bddl)。

使用 `house_double_floor_upper / living_room_0`。插入 `mug/ppdqbj`、`box_of_tissues/uglbjc`；公共茶几为 `coffee_table_osroux_0`。杯子来源为 `bookcase_otwukr_0/1`，纸巾盒来源为 `bookcase_otwukr_2/3`，两组独立分配，共 `A1B1`、`A1B2`、`A2B1`、`A2B2` 四种变体。

- **初态：** 杯子和纸巾盒各在自己的来源书架顶板。
- **Key 1：** 杯子取到茶几；纸巾盒保持来源位置。
- **Key 2：** 纸巾盒取到茶几，与杯子分开放置、不叠放；杯子仍在茶几。
- **Query：** 两件物体分别归还各自的历史来源。
- **判定：** 使用原生 `ontop`；各段只写入本段搬动的物品，家具受保护。

固定 Query 指令：

> Return the mug and the tissue box from the coffee table to the respective places you took them from.

间隔任务为 `organizing_art_supplies`（`bedroom_0`，task 59）和 `getting_organized_for_work`（`bedroom_2`，task 28）。

**记忆要求：** K₁/K₂ 分别提供杯子与纸巾盒的来源信息，Query 需要联合两条记忆才能把两件用品都归还正确。两组来源独立变化，不能固定配对。

## mb005 · Return the bowl and cutting board to their original locations

入口：[任务代码](../../tasks/mb005_return_bowl_and_cutting_board.py)、[配置](../../configs/mb005_return_bowl_and_cutting_board.yaml)、[Key 1 BDDL](../../../bddl3/bddl/activity_definitions/mb_place_bowl_on_bar/problem0.bddl)、[Key 2 BDDL](../../../bddl3/bddl/activity_definitions/mb_place_cutting_board_on_bar/problem0.bddl)、[Query BDDL](../../../bddl3/bddl/activity_definitions/mb_return_bowl_and_cutting_board/problem0.bddl)。

使用 `house_single_floor / kitchen_0`，公共操作面为吧台 `bar_udatjt_0`。两只空碗为 `bowl/hpqjug`、`bowl/npuuir`，砧板为 `cutting_board/aibvew`。目标碗来源与归还位置固定为微波炉 `microwave_abzvij_0` 的顶部；另一只碗初始就在吧台。砧板来源为 `countertop_kelker_0` 或 `countertop_kelzer_0`，与目标碗身份独立组合为 `AS1`、`AS2`、`BS1`、`BS2`。壁架不再作为任务支撑物。

- **Key 1：** 从微波炉顶部取碗到吧台，与已有碗并排、不叠放。
- **Key 2：** 从来源台面取砧板到吧台，与两只碗分开放置，两碗保持原位。
- **Query：** 这次取来的碗归微波炉顶部，砧板归历史来源台面，另一只碗留吧台。
- **判定：** 全部使用原生 `ontop`；各片段仅写对应物品，家具保持原身份和布局。

固定 Query 指令：

> Return the bowl you brought over to the top of the microwave and the cutting board to the counter you took it from, leaving the other bowl on the bar.

间隔任务为 `putting_away_toys`（主要房间 `dining_room_0`，task 54，涉及餐厅与客厅）和 `bringing_water`（主要房间 `living_room_0`，task 17，涉及厨房与客厅）。两项完整活动的任务物体互不重叠，并与 K/Q 隔离；本地须备齐 task 54、17 的官方录制。

**记忆要求：** 联合“搬来哪只碗”和“砧板原台面”两条独立信息。Query 需要区分目标碗与原有碗，同时记住砧板来源；吧台摆位不能标识目标碗，Key 1 也不能提前泄漏砧板来源。

## mb006 · Retrieve the relocated jar of meat

任务标识为 `mb006_retrieve_relocated_meat_jar`。入口：[任务代码](../../tasks/mb006_retrieve_relocated_meat_jar.py)、[配置](../../configs/mb006_retrieve_relocated_meat_jar.yaml)、[Key 1 BDDL](../../../bddl3/bddl/activity_definitions/mb_can_and_store_meat/problem0.bddl)、[Key 2 BDDL](../../../bddl3/bddl/activity_definitions/mb_relocate_meat_jar/problem0.bddl)、[Query BDDL](../../../bddl3/bddl/activity_definitions/mb_retrieve_relocated_meat_jar/problem0.bddl)。

使用 `house_single_floor / kitchen_0`，参考官方 [can_meat](../../../bddl3/bddl/activity_definitions/can_meat/problem0.bddl)，保留其模型及已采样尺寸，缩减为一只带盖罐、一根熟香肠，并将空罐初始位置改到吧台。

| 资产 | 模型或场景对象 | 尺寸与用途 |
|---|---|---|
| 带盖罐 | `hinged_jar/vzwhbg` | 固定 0.9 缩放，贯穿 K/Q 的同一只罐 |
| 熟香肠 | `bratwurst/pqfrrn` | 包围盒 `[0.1148, 0.0570, 0.0203]` m，初始已熟 |
| 砧板 | `cutting_board/jfrbuz` | 包围盒 `[0.5141, 0.3012, 0.0254]` m，留在吧台 |
| 吧台 | `bar_udatjt_0` | 装罐操作及 Q 取罐落点 |
| 吊柜 A | `top_cabinet_lkxmne_2` | 官方原柜，靠近微波炉 |
| 吊柜 B | `top_cabinet_lkxmne_1` | 中间柜 |
| 吊柜 C | `top_cabinet_lkxmne_0` | 远离微波炉的端柜 |

- **初态：** 空罐在吧台上，熟香肠在同一吧台的砧板上；罐盖和三只吊柜的门均关闭，原家具不移动。
- **Key 1：** 在吧台将香肠装入空罐、关盖，再将肉罐存入本变体指定的吊柜，关好全部柜门，建立旧位置记忆。
- **Key 2：** 将同一密封肉罐从旧柜搬到新柜，香肠留在罐内，关好全部柜门，更新存放位置。
- **Query：** 从最新存放柜取出整罐肉，放到吧台，保持罐盖关闭及香肠在罐内，关好全部柜门。不取出香肠，不归还原柜。
- **判定：** 使用 `inside`、`ontop`、`cooked`、`not open`。K₂ 明确要求罐子离开旧柜；各阶段均检查香肠未丢失。K/Q 均将罐子、香肠及三个柜子声明为可写，砧板和吧台受保护。

| 变体 | K₁ 旧柜 | K₂ 新柜 / Q 取用柜 | 150 实例分配 |
|---|---|---|---|
| AB | A | B | 25 |
| AC | A | C | 25 |
| BA | B | A | 25 |
| BC | B | C | 25 |
| CA | C | A | 25 |
| CB | C | B | 25 |

内部柜子角色 1/2/3 分别绑定旧柜、新柜、未使用候选柜，跨片段保持一致；A/B/C 和角色标签不进入策略观测。Key 指令使用“靠近微波炉的吊柜 / 中间吊柜 / 远离微波炉的吊柜”定位，K₂ 不复述旧位置。固定 Query 指令：

> Retrieve the jar of meat you stored earlier and place it on the bar, keeping the jar closed and the sausage inside, then close all the cabinet doors.

间隔任务与 mb005 相同：`putting_away_toys`（task 54，`dining_room_0`）和 `bringing_water`（task 17，`living_room_0`）。两项官方任务绑定不使用上述吊柜、吧台或新增的罐子、香肠、砧板；实际录制仍须审查额外交互。

**记忆要求：** 属于 MU · L2，K₂ 更新 K₁ 的旧位置；允许 K₂ 单独提供最新答案，不强求 Q 整合两段独立信息。同一旧柜有两个可能的新柜，不能凭固定互换规则推断。Q 入口所有柜门关闭、罐子完全藏入，头部及双腕相机均不得看见肉罐；机器人起点、任务顺序与答案独立。I/B 不得开柜或重新揭示肉罐位置。采集审查分别记录首次开柜选择和最终取用成功，不能用搜索后的成功代替记忆正确。

**采样与验收：** 沿用官方资产及标准采样路径，不固定内容物、不在片段间补填或重采样。新增了两个候选吊柜与换柜操作，正式采集前须小样检查三柜取放、关门遮挡及带内容物搬运；沿用官方模型不代表新任务采样成功率已经验证。任务更名后须使用新 session 准备实例，不能沿用旧汉堡任务的绑定或录制。

## mb007 · Place the gaming controller back on its original nightstand

入口：[任务代码](../../tasks/mb007_place_gaming_controller_on_original_nightstand.py)、[配置](../../configs/mb007_place_gaming_controller_on_original_nightstand.yaml)、[Key 1 BDDL](../../../bddl3/bddl/activity_definitions/mb_put_gaming_controller_into_storage_box/problem0.bddl)、[Key 2 BDDL](../../../bddl3/bddl/activity_definitions/mb_move_storage_box_to_footstool/problem0.bddl)、[Query BDDL](../../../bddl3/bddl/activity_definitions/mb_place_gaming_controller_on_original_nightstand/problem0.bddl)。

使用 `house_double_floor_upper / bedroom_1`。插入 `gaming_controller/yofqbq` 与开口盒 `packing_box/cjhskr`（统一缩放为 0.75，外尺寸约 28.5 × 35.1 × 16.8 cm）；两处来源为 `nightstand_roxnbe_0/1`，分别对应 A/B。盒子初始在 `bed_siuxgz_0`，固定搬运目的地为床尾脚凳 `ottoman_qsnawv_0`。床头柜只用顶面，全部家具沿用场景身份与布局。

- **初态：** 手柄在来源床头柜顶面且不在盒内；盒子在床上，脚凳上无盒子或手柄。
- **Key 1：** 手柄放入床上的盒子，两处床头柜均无手柄。
- **Key 2：** 抓起盒身或盒沿，连同盒内手柄搬到脚凳，保持 `inside(手柄, 盒)`。
- **Query：** 从脚凳上的盒子取手柄，归还最初床头柜，盒子留在脚凳。
- **判定：** 使用 `ontop`、`inside` 及其否定；Key 2 声明盒子和手柄均可写，Query 仅写手柄。不固定内容物，不在片段间补填。

固定 Query 指令：

> Retrieve the gaming controller from the storage box on the footstool and return it to the top of the nightstand you originally took it from, leaving the box on the footstool.

间隔任务为 `organizing_art_supplies`（`bedroom_0`，task 59）和 `re_shelving_library_books`（`living_room_0`，task 55）。

**记忆要求：** K₂ 搬动盒子时，手柄随容器更新位置；Query 允许看见脚凳上的盒子和手柄，正确归还位置仍由 K₁ 的初始来源决定。保留 MU · L2 分组，并注明“随容器位置更新＋初始来源回忆”，不将其等同于凭记忆寻找隐藏的最新位置。
