# 已实现任务：mb000–mb007

mb000–mb007 共八项任务已实现任务定义、BDDL、配置和注册，覆盖空间回忆、状态回忆、多条记忆整合，以及物体搬动后的来源回忆。各任务的场景、操作、变体与记忆要求如下；通用约束见 [设计与实施要求](requirements.md)。

K（Key）是提供历史信息的关键片段，I 是间隔任务，Q（Query）是需要利用历史信息完成的目标任务。mb000–mb003 为 L1，使用 `1K + 2I + Q`；mb004–mb007 为 L2，使用 `2K + 2I + Q`。Q 最后执行，双 Key 任务中 K₁ 先于 K₂，相邻片段由 Bridge 衔接。

SP 为空间回忆，ST 为状态回忆，MI 为多条记忆整合，MU 为记忆更新；mb006 与 mb007 的具体记忆要求见各任务介绍。

所有任务在 K/Q 之间保留物体身份并连续继承世界状态。同一任务的 Q 指令不随变体改变；历史答案应来自 Key，不能由 Q 入口的摆位、姿态或残留痕迹替代。

| 编号 | 类别与序列 | K/Q 房间 | Key | Query |
|---|---|---|---|---|
| mb000 | SP · L1 · 1K + 2I + Q | lower / kitchen_0 | 牛奶从餐桌存入指定冰箱或微波炉，关好门，微波炉保持关机 | 从之前存放的位置取回牛奶，放回餐桌并关好门 |
| mb001 | SP · L1 · 1K + 2I + Q | upper / living_room_0 | 从来源书架顶板取书到阅读桌 | 归还原书架顶板 |
| mb002 | ST · L1 · 1K + 2I + Q | upper / bedroom_2 | 关掉原先唯一亮着的阅读灯，两灯均关 | 只打开之前使用的那盏阅读灯 |
| mb003 | ST · L1 · 1K + 2I + Q | lower / living_room_0 | 关闭茶几上原先唯一开启的收音机 | 将之前使用的收音机放到电视柜，另一台留在茶几，两台均关 |
| mb004 | MI · L2 · 2K + 2I + Q | upper / living_room_0 | 分别从独立来源取杯子、纸巾盒到茶几 | 两件用品各归原来源 |
| mb005 | MI · L2 · 2K + 2I + Q | single / kitchen_0 | 从下层架取碗到吧台，再从来源台面取砧板到吧台 | 取来的碗归下层架，砧板归原台面，另一只碗留吧台 |
| mb006 | L2 · 搬动干扰下的来源回忆 · 2K + 2I + Q | single / kitchen_0 | 汉堡从来源台面到吧台，再放入微波炉并关门 | 取出汉堡归还最初台面，关好微波炉 |
| mb007 | MU · L2 · 随容器位置更新＋来源回忆 · 2K + 2I + Q | upper / bedroom_1 | 手柄从床头柜放进床上的盒子，再连盒搬到脚凳 | 手柄归还最初床头柜，盒子留在脚凳 |

`lower`、`upper`、`single` 分别表示 `house_double_floor_lower`、`house_double_floor_upper`、`house_single_floor`。mb006 不按 MU 计数；mb007 保留 MU 分组，但 Q 仍依赖初始来源，不要求凭记忆寻找隐藏的最新位置。

## mb000 · 从之前存放的位置取回牛奶

任务标识为 `mb000_retrieve_milk`。入口：[任务代码](../../tasks/mb000_retrieve_milk.py)、[配置](../../configs/mb000_retrieve_milk.yaml)、[Key BDDL](../../../bddl3/bddl/activity_definitions/mb_store_milk/problem0.bddl)、[Query BDDL](../../../bddl3/bddl/activity_definitions/mb_retrieve_milk/problem0.bddl)。

### 场景与资产

使用 `house_double_floor_lower / kitchen_0`。公共取放点为厨房现有餐桌，冰箱和微波炉作为两个独立存放位置，不要求容器同类。任务只做牛奶存取，不倒奶、不加热。

| 资产 | 模型或场景对象 | 用途 |
|---|---|---|
| 牛奶 | `bottle_of_milk/mrejrs`，外包尺寸约 13.7 × 5.1 × 4.5 cm | 唯一新增物品；指令称“牛奶”（`milk`） |
| 冰箱 | `fridge_petcxr_0` | A 变体的存放位置 |
| 微波炉 | `microwave_hjjxmi_0` | B 变体的存放位置，保持关机 |
| 餐桌 | `breakfast_table_xftrki_0`，桌高约 68 cm | 所有变体共同的起始与归还位置 |

冰箱、微波炉和餐桌各自使用固定的 BDDL 角色绑定，A/B 不交换不同类别的物体。两变体分别以冰箱或微波炉为存放位置；同一件牛奶贯穿 K/Q。

### 操作与指令

- **初态：** 牛奶在餐桌上，冰箱和微波炉的门均关闭，微波炉关机。
- **Key A：** “把餐桌上的牛奶放进冰箱，关好冰箱和微波炉的门，保持微波炉关机。”
- **Key B：** “把餐桌上的牛奶暂放进微波炉，关好冰箱和微波炉的门，保持微波炉关机。”
- **Query 固定指令：** “取出刚才放好的牛奶，放到餐桌上，并关好冰箱和微波炉的门，保持微波炉关机。”

代码中的 Query 指令为：

> Take the milk out of where you put it earlier and place it on the dining table. Close the refrigerator and the microwave, and leave the microwave off.

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

## mb001 · 借书后归还原书架

任务标识为 `mb001_putting_book_back_on_bookshelf`。入口：[任务代码](../../tasks/mb001_putting_book_back_on_bookshelf.py)、[配置](../../configs/mb001_putting_book_back_on_bookshelf.yaml)、[Key BDDL](../../../bddl3/bddl/activity_definitions/mb_taking_book_from_bookshelf/problem0.bddl)、[Query BDDL](../../../bddl3/bddl/activity_definitions/mb_putting_book_back_on_bookshelf/problem0.bddl)。

### 场景与资产

使用 `house_double_floor_upper / living_room_0`。书为 `hardback/acbrnv`，约 22.3 × 17.2 × 3.1 cm；来源书架为 `bookcase_otwukr_0/3`，约 30 × 76 × 105 cm；公共阅读桌为 `desk_iotfzl_0`，高约 72.8 cm。使用书架顶板取还书，不执行竖放或插缝。

| 变体 | 来源书架绑定 | 公共阅读桌 |
|---|---|---|
| A | `bookcase_otwukr_0` | `desk_iotfzl_0` |
| B | `bookcase_otwukr_3` | `desk_iotfzl_0` |

`bookcase.n.01_1` 表示来源书架。BDDL 声明四个现有书架；`bookcase_otwukr_1/2` 为受保护的背景支撑物。同一本书贯穿 K/Q。

### 操作与指令

- **初态：** 目标书在来源书架顶板，另一候选书架顶板与公共阅读桌为空；不留书本轮廓或其他来源提示。
- **Key：** “从书架顶板取书，放到阅读桌上备用。”A/B 使用相同指令，来源由初态决定。
- **Query 固定指令：** “把阅读桌上的书归还到之前取书的书架顶板。”

代码中的 Query 指令为：

> Return the book from the desk to the top of the bookcase you took it from.

### 条件与记忆要求

| 边界 | 条件 |
|---|---|
| Key 开始 | `ontop(书, 来源书架)` |
| Key 完成 / Query 开始 | `ontop(书, 阅读桌)` |
| Query 完成 | `ontop(书, 来源书架)` |

A/B 交换来源书架绑定，Query 需要记住 Key 取书的书架。顶板取放由指令和布局引导，条件仍使用原生 `ontop`，不增加专用坐标区、层号或角度规则。桌面上的书姿态与落点应与来源独立，K/Q 之间不重摆。

间隔任务为 `organizing_art_supplies`（`bedroom_0`，task 59）和 `getting_organized_for_work`（`bedroom_2`，task 28）。K/Q 各一次书本转移，完成 Key 后两个来源位置均为空；I/Bridge 不改变或重新揭示来源。

## mb002 · 恢复原先使用的阅读灯

任务标识为 `mb002_restore_reading_lamp`。入口：[任务代码](../../tasks/mb002_restore_reading_lamp.py)、[配置](../../configs/mb002_restore_reading_lamp.yaml)、[Key BDDL](../../../bddl3/bddl/activity_definitions/mb_turning_off_reading_lamps/problem0.bddl)、[Query BDDL](../../../bddl3/bddl/activity_definitions/mb_restoring_reading_lamp/problem0.bddl)。

### 场景与资产

使用 `house_double_floor_upper / bedroom_2`，在现有桌面 `desk_uqcmzf_0` 上插入两盏阅读灯：`table_lamp/fhsorf`（约 42.5 × 18.4 × 41.5 cm）和 `table_lamp/wbcgev`（约 30.8 × 30.1 × 51.9 cm）。两盏灯均有原生灯光与开关元数据，模型和尺寸在 K/Q 之间保持一致。

两种灯具保持各自的模型与身份，A/B 只改变哪盏灯在初态亮着。

| 变体 | 原先亮着的灯 | 另一盏灯 | 公共桌面 |
|---|---|---|---|
| A | `table_lamp/fhsorf` | `table_lamp/wbcgev`，关闭 | `desk_uqcmzf_0` |
| B | `table_lamp/wbcgev` | `table_lamp/fhsorf`，关闭 | `desk_uqcmzf_0` |

`table_lamp.n.01_1` 始终表示原先亮着的目标灯，`lamp.n.02_1` 表示另一盏灯。A/B 交换角色绑定；两种模型都可能成为目标，Query 指令不随变体改变。房间原有其他灯不参与任务操作。

### 操作与指令

- **初态：** 两盏阅读灯都在桌面，恰好一盏亮。
- **Key：** “暂时离开，关好桌上的两盏阅读灯。”A/B 使用相同指令，实际关闭原先亮着的那盏。
- **Query 固定指令：** “恢复刚才的阅读照明，只打开之前使用的那盏桌面阅读灯。”

代码中的 Query 指令为：

> Restore the earlier reading lighting: turn on only the desk reading lamp that was in use before.

K/Q 各需一次开关操作，不搬灯、不调灯臂。开关通过原生接触交互操作，不扩大任务专用触发区域。

### 条件与记忆要求

| 边界 | 条件 |
|---|---|
| Key 开始 | 两灯均 `ontop(灯, 桌面)`；目标灯 `toggled_on`，另一灯关闭 |
| Key 完成 / Query 开始 | 两灯仍在桌面，且均关闭 |
| Query 完成 | 两灯仍在桌面；目标灯 `toggled_on`，另一灯关闭 |

判定只使用原生 `ontop`、`toggled_on` 及其否定。两盏灯声明为可写对象，灯和桌面均受保护；桌面不允许写入。Key 出口与 Query 入口的条件一致，片段之间连续继承物体状态。

Query 需要回忆原先的亮灯身份。两灯关闭后的外观、位置、灯臂姿态和按钮状态不能成为历史答案的替代线索。

间隔任务为 `re_shelving_library_books`（`living_room_0`，task 55）和 `organizing_art_supplies`（`bedroom_0`，task 59），使用完整官方录制，操作对象与 K/Q 隔离。

## mb003 · 把之前使用的收音机放到电视柜

入口：[任务代码](../../tasks/mb003_putting_radio_on_tv_stand.py)、[配置](../../configs/mb003_putting_radio_on_tv_stand.yaml)、[Key BDDL](../../../bddl3/bddl/activity_definitions/mb_turning_off_radios/problem0.bddl)、[Query BDDL](../../../bddl3/bddl/activity_definitions/mb_putting_radio_on_tv_stand/problem0.bddl)。

使用 `house_double_floor_lower / living_room_0`。两台插入收音机为 `radio/vgioak`、`radio/wxnicr`，公共茶几为 `coffee_table_koagbh_0`，电视柜为 `bottom_cabinet_rhdbzv_0`。通过 `variant_object_models` 固定两件物体的模型与身份，A/B 交换历史目标角色，不交换实际物体。

- **初态：** 两台收音机都在茶几上，恰好一台开启。
- **Key：** 关闭茶几上的两台收音机，保持两台在茶几上。
- **Query：** 将之前使用的那台放到电视柜顶板，另一台留在茶几，两台保持关闭。
- **判定：** 使用原生 `ontop`、`toggled_on` 及其否定；`radio_receiver.n.01_1` 始终为原先开启的目标，另一角色为非目标。

固定 Query 指令：

> Move the radio you were using earlier from the coffee table to the top of the TV stand. Keep both radios off and leave the other radio on the coffee table.

间隔任务为 `hanging_pictures`（`kitchen_0`，task 34）和 `putting_shoes_on_rack`（`corridor_0`，task 22）。

**记忆要求：** 记住 Key 中原先开启的是哪台收音机，并在两台均关闭后识别它。关闭后的落点、朝向与按钮状态不能代替历史记忆。

## mb004 · 杯子与纸巾盒各归原处

入口：[任务代码](../../tasks/mb004_returning_tea_supplies.py)、[配置](../../configs/mb004_returning_tea_supplies.yaml)、[Key 1 BDDL](../../../bddl3/bddl/activity_definitions/mb_taking_mug_to_tea_table/problem0.bddl)、[Key 2 BDDL](../../../bddl3/bddl/activity_definitions/mb_taking_tissues_to_tea_table/problem0.bddl)、[Query BDDL](../../../bddl3/bddl/activity_definitions/mb_returning_tea_supplies/problem0.bddl)。

使用 `house_double_floor_upper / living_room_0`。插入 `mug/ppdqbj`、`box_of_tissues/uglbjc`；公共茶几为 `coffee_table_osroux_0`。杯子来源为 `bookcase_otwukr_0/1`，纸巾盒来源为 `bookcase_otwukr_2/3`，两组独立分配，共 `A1B1`、`A1B2`、`A2B1`、`A2B2` 四种变体。

- **初态：** 杯子和纸巾盒各在自己的来源书架顶板。
- **Key 1：** 杯子取到茶几；纸巾盒保持来源位置。
- **Key 2：** 纸巾盒取到茶几，与杯子分开放置、不叠放；杯子仍在茶几。
- **Query：** 两件物体分别归还各自的历史来源。
- **判定：** 使用原生 `ontop`；各段只写入本段搬动的物品，家具受保护。

固定 Query 指令：

> Put away the prepared tea supplies: return the mug and the tissue box to the respective places you took them from.

间隔任务为 `organizing_art_supplies`（`bedroom_0`，task 59）和 `getting_organized_for_work`（`bedroom_2`，task 28）。

**记忆要求：** K₁/K₂ 分别提供杯子与纸巾盒的来源信息，Query 需要联合两条记忆才能把两件用品都归还正确。两组来源独立变化，不能固定配对。

## mb005 · 取消备餐，将碗与砧板各归原处

入口：[任务代码](../../tasks/mb005_returning_meal_prep_supplies.py)、[配置](../../configs/mb005_returning_meal_prep_supplies.yaml)、[Key 1 BDDL](../../../bddl3/bddl/activity_definitions/mb_taking_bowl_to_prep_counter/problem0.bddl)、[Key 2 BDDL](../../../bddl3/bddl/activity_definitions/mb_taking_cutting_board_to_prep_counter/problem0.bddl)、[Query BDDL](../../../bddl3/bddl/activity_definitions/mb_returning_meal_prep_supplies/problem0.bddl)。

使用 `house_single_floor / kitchen_0`，公共操作面为吧台 `bar_udatjt_0`。两只空碗为 `bowl/hpqjug`、`bowl/npuuir`，砧板为 `cutting_board/aibvew`。目标碗来源与归还位置固定为下层架 `shelf_pfusrd_1`；另一只碗初始就在吧台。砧板来源为 `countertop_kelker_0` 或 `countertop_kelzer_0`，与目标碗身份独立组合为 `AS1`、`AS2`、`BS1`、`BS2`。上层架 `shelf_pfusrd_0` 保留绑定，不放任务物体。

- **Key 1：** 从下层架取碗到吧台，与已有碗并排、不叠放。
- **Key 2：** 从来源台面取砧板到吧台，与两只碗分开放置，两碗保持原位。
- **Query：** 这次取来的碗归下层架，砧板归历史来源台面，另一只碗留吧台。
- **判定：** 全部使用原生 `ontop`；各片段仅写对应物品，家具保持原身份和布局。

固定 Query 指令：

> We are not preparing the meal after all. Return the bowl you brought over to the lower shelf, and return the cutting board to the counter you took it from. Leave the other bowl on the bar.

间隔任务为 `organizing_school_stuff`（`bedroom_0`，task 73）和 `picking_up_toys`（`childs_room_0`，task 7）。

**记忆要求：** 联合“搬来哪只碗”和“砧板原台面”两条独立信息。Query 需要区分目标碗与原有碗，同时记住砧板来源；吧台摆位不能标识目标碗，Key 1 也不能提前泄漏砧板来源。

## mb006 · 汉堡经吧台与微波炉后归还最初台面

入口：[任务代码](../../tasks/mb006_returning_hamburger_to_original_counter.py)、[配置](../../configs/mb006_returning_hamburger_to_original_counter.yaml)、[Key 1 BDDL](../../../bddl3/bddl/activity_definitions/mb_taking_hamburger_to_bar/problem0.bddl)、[Key 2 BDDL](../../../bddl3/bddl/activity_definitions/mb_putting_hamburger_in_microwave/problem0.bddl)、[Query BDDL](../../../bddl3/bddl/activity_definitions/mb_returning_hamburger_to_original_counter/problem0.bddl)。

使用 `house_single_floor / kitchen_0`，插入一件 `hamburger/fyswch`。A1/A2 的初始来源分别为 `countertop_kelker_0`、`countertop_kelzer_0`；固定中转点为 `bar_udatjt_0`，微波炉为 `microwave_abzvij_0`。

- **初态：** 汉堡在来源台面，微波炉门关闭。
- **Key 1：** 汉堡从来源台面搬到吧台，微波炉门保持关闭。
- **Key 2：** 打开微波炉，将吧台上的汉堡放入并关门。
- **Query：** 从微波炉取出汉堡，归还最初台面并关门。
- **判定：** 使用 `ontop`、`inside`、`not open`。当前任务条件未加入加热或电源状态判定。

固定 Query 指令：

> Take the hamburger out of the microwave and return it to the counter you originally took it from. Close the microwave door.

间隔任务与 mb005 相同。

**记忆要求：** 汉堡经过吧台、微波炉两次搬动后，仍要记住最初取自哪个台面。K₂ 改变当前存放位置，归还答案仍来自 K₁；该任务按搬动干扰下的初始来源回忆描述。

## mb007 · 手柄连盒搬动后归还最初床头柜

入口：[任务代码](../../tasks/mb007_returning_controller_to_original_nightstand.py)、[配置](../../configs/mb007_returning_controller_to_original_nightstand.yaml)、[Key 1 BDDL](../../../bddl3/bddl/activity_definitions/mb_putting_controller_in_box/problem0.bddl)、[Key 2 BDDL](../../../bddl3/bddl/activity_definitions/mb_moving_controller_box_to_ottoman/problem0.bddl)、[Query BDDL](../../../bddl3/bddl/activity_definitions/mb_returning_controller_to_original_nightstand/problem0.bddl)。

使用 `house_double_floor_upper / bedroom_1`。插入 `gaming_controller/yofqbq` 与开口盒 `toy_box/aviadj`；两处来源为 `nightstand_roxnbe_0/1`，分别对应 A/B。盒子初始在 `bed_siuxgz_0`，固定搬运目的地为床尾脚凳 `ottoman_qsnawv_0`。床头柜只用顶面，全部家具沿用场景身份与布局。

- **初态：** 手柄在来源床头柜顶面且不在盒内；盒子在床上，脚凳上无盒子或手柄。
- **Key 1：** 手柄放入床上的盒子，两处床头柜均无手柄。
- **Key 2：** 抓起盒身或盒沿，连同盒内手柄搬到脚凳，保持 `inside(手柄, 盒)`。
- **Query：** 从脚凳上的盒子取手柄，归还最初床头柜，盒子留在脚凳。
- **判定：** 使用 `ontop`、`inside` 及其否定；Key 2 声明盒子和手柄均可写，Query 仅写手柄。不固定内容物，不在片段间补填。

固定 Query 指令：

> We are not playing games after all. Take the gaming controller out of the storage box on the footstool at the foot of the bed and return it to the top of the nightstand you originally took it from.

间隔任务为 `organizing_art_supplies`（`bedroom_0`，task 59）和 `re_shelving_library_books`（`living_room_0`，task 55）。

**记忆要求：** K₂ 搬动盒子时，手柄随容器更新位置；Query 允许看见脚凳上的盒子和手柄，正确归还位置仍由 K₁ 的初始来源决定。保留 MU · L2 分组，并注明“随容器位置更新＋初始来源回忆”，不将其等同于凭记忆寻找隐藏的最新位置。
