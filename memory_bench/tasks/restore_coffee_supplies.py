from memory_bench.tasks.base_task import MemoryTask


# Conditions use fixed physical roles; only the remembered facts vary.
VARIANT_INIT = {
    "A1_B1": {
        "key_1": [
            ["ontop", "coffee_cup.n.01_1", "countertop.n.01_1"],
            ["inside", "jar__of__sugar.n.01_1", "cabinet.n.01_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "key_2": [
            ["ontop", "coffee_cup.n.01_1", "countertop.n.01_3"],
            ["inside", "jar__of__sugar.n.01_1", "cabinet.n.01_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "query": [
            ["ontop", "coffee_cup.n.01_1", "countertop.n.01_3"],
            ["ontop", "jar__of__sugar.n.01_1", "countertop.n.01_3"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
    },
    "A1_B2": {
        "key_1": [
            ["ontop", "coffee_cup.n.01_1", "countertop.n.01_1"],
            ["inside", "jar__of__sugar.n.01_1", "cabinet.n.01_2"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "key_2": [
            ["ontop", "coffee_cup.n.01_1", "countertop.n.01_3"],
            ["inside", "jar__of__sugar.n.01_1", "cabinet.n.01_2"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "query": [
            ["ontop", "coffee_cup.n.01_1", "countertop.n.01_3"],
            ["ontop", "jar__of__sugar.n.01_1", "countertop.n.01_3"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
    },
    "A2_B1": {
        "key_1": [
            ["ontop", "coffee_cup.n.01_1", "countertop.n.01_2"],
            ["inside", "jar__of__sugar.n.01_1", "cabinet.n.01_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "key_2": [
            ["ontop", "coffee_cup.n.01_1", "countertop.n.01_3"],
            ["inside", "jar__of__sugar.n.01_1", "cabinet.n.01_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "query": [
            ["ontop", "coffee_cup.n.01_1", "countertop.n.01_3"],
            ["ontop", "jar__of__sugar.n.01_1", "countertop.n.01_3"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
    },
    "A2_B2": {
        "key_1": [
            ["ontop", "coffee_cup.n.01_1", "countertop.n.01_2"],
            ["inside", "jar__of__sugar.n.01_1", "cabinet.n.01_2"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "key_2": [
            ["ontop", "coffee_cup.n.01_1", "countertop.n.01_3"],
            ["inside", "jar__of__sugar.n.01_1", "cabinet.n.01_2"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "query": [
            ["ontop", "coffee_cup.n.01_1", "countertop.n.01_3"],
            ["ontop", "jar__of__sugar.n.01_1", "countertop.n.01_3"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
    },
}

VARIANT_GOAL = {
    "A1_B1": {
        "key_1": [
            ["ontop", "coffee_cup.n.01_1", "countertop.n.01_3"],
            ["inside", "jar__of__sugar.n.01_1", "cabinet.n.01_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "key_2": [
            ["ontop", "coffee_cup.n.01_1", "countertop.n.01_3"],
            ["ontop", "jar__of__sugar.n.01_1", "countertop.n.01_3"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "query": [
            ["ontop", "coffee_cup.n.01_1", "countertop.n.01_1"],
            ["inside", "jar__of__sugar.n.01_1", "cabinet.n.01_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
    },
    "A1_B2": {
        "key_1": [
            ["ontop", "coffee_cup.n.01_1", "countertop.n.01_3"],
            ["inside", "jar__of__sugar.n.01_1", "cabinet.n.01_2"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "key_2": [
            ["ontop", "coffee_cup.n.01_1", "countertop.n.01_3"],
            ["ontop", "jar__of__sugar.n.01_1", "countertop.n.01_3"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "query": [
            ["ontop", "coffee_cup.n.01_1", "countertop.n.01_1"],
            ["inside", "jar__of__sugar.n.01_1", "cabinet.n.01_2"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
    },
    "A2_B1": {
        "key_1": [
            ["ontop", "coffee_cup.n.01_1", "countertop.n.01_3"],
            ["inside", "jar__of__sugar.n.01_1", "cabinet.n.01_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "key_2": [
            ["ontop", "coffee_cup.n.01_1", "countertop.n.01_3"],
            ["ontop", "jar__of__sugar.n.01_1", "countertop.n.01_3"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "query": [
            ["ontop", "coffee_cup.n.01_1", "countertop.n.01_2"],
            ["inside", "jar__of__sugar.n.01_1", "cabinet.n.01_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
    },
    "A2_B2": {
        "key_1": [
            ["ontop", "coffee_cup.n.01_1", "countertop.n.01_3"],
            ["inside", "jar__of__sugar.n.01_1", "cabinet.n.01_2"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "key_2": [
            ["ontop", "coffee_cup.n.01_1", "countertop.n.01_3"],
            ["ontop", "jar__of__sugar.n.01_1", "countertop.n.01_3"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "query": [
            ["ontop", "coffee_cup.n.01_1", "countertop.n.01_2"],
            ["inside", "jar__of__sugar.n.01_1", "cabinet.n.01_2"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
    },
}


TASK = MemoryTask(
    task_name="mb_restore_coffee_supplies",
    scene_model="house_single_floor",
    room_types=("kitchen",),
    whitelist={
        "cabinet.n.01": {"bottom_cabinet_no_top": {"rkgjer": None, "gjeoer": None}},
        "countertop.n.01": {"countertop": {"kelzer": None, "kelker": None}, "bar": {"udatjt": None}},
        "coffee_cup.n.01": {"coffee_cup": {"nbhcgu": None}},
        "jar__of__sugar.n.01": {"jar_of_sugar": {"pnbbfb": None}},
    },
    key_activities=("mb_prepare_coffee_cup", "mb_prepare_coffee_sugar"),
    intervent_activities=("outfit_a_basic_toolbox", "collecting_childrens_toys"),
    query_activity="mb_restore_coffee_supplies",
    query_instruction=(
        "Put away the coffee supplies: return the cup to its original counter and the sugar jar to its "
        "original cabinet. Close both cabinets."
    ),
    key_instructions={
        "A1_B1": {
            "mb_prepare_coffee_cup": "Bring the cup to the coffee preparation counter. Keep the sugar cabinets closed.",
            "mb_prepare_coffee_sugar": (
                "Bring the sugar jar from the cabinet nearer the refrigerator to the coffee preparation "
                "counter, and close both cabinets. Leave the cup in place."
            ),
        },
        "A1_B2": {
            "mb_prepare_coffee_cup": "Bring the cup to the coffee preparation counter. Keep the sugar cabinets closed.",
            "mb_prepare_coffee_sugar": (
                "Bring the sugar jar from the cabinet farther from the refrigerator to the coffee "
                "preparation counter, and close both cabinets. Leave the cup in place."
            ),
        },
        "A2_B1": {
            "mb_prepare_coffee_cup": "Bring the cup to the coffee preparation counter. Keep the sugar cabinets closed.",
            "mb_prepare_coffee_sugar": (
                "Bring the sugar jar from the cabinet nearer the refrigerator to the coffee preparation "
                "counter, and close both cabinets. Leave the cup in place."
            ),
        },
        "A2_B2": {
            "mb_prepare_coffee_cup": "Bring the cup to the coffee preparation counter. Keep the sugar cabinets closed.",
            "mb_prepare_coffee_sugar": (
                "Bring the sugar jar from the cabinet farther from the refrigerator to the coffee "
                "preparation counter, and close both cabinets. Leave the cup in place."
            ),
        },
    },
    variants={"A1_B1": {}, "A1_B2": {}, "A2_B1": {}, "A2_B2": {}},
    object_usage={
        "mb_prepare_coffee_cup": {
            "used": (
                "cabinet.n.01_1",
                "cabinet.n.01_2",
                "countertop.n.01_1",
                "countertop.n.01_2",
                "countertop.n.01_3",
                "coffee_cup.n.01_1",
                "jar__of__sugar.n.01_1",
            ),
            "read": (
                "cabinet.n.01_1",
                "cabinet.n.01_2",
                "countertop.n.01_1",
                "countertop.n.01_2",
                "countertop.n.01_3",
                "coffee_cup.n.01_1",
                "jar__of__sugar.n.01_1",
            ),
            "write": ("coffee_cup.n.01_1",),
            "protected": (
                "cabinet.n.01_1",
                "cabinet.n.01_2",
                "countertop.n.01_1",
                "countertop.n.01_2",
                "countertop.n.01_3",
                "coffee_cup.n.01_1",
                "jar__of__sugar.n.01_1",
            ),
        },
        "mb_prepare_coffee_sugar": {
            "used": (
                "cabinet.n.01_1",
                "cabinet.n.01_2",
                "countertop.n.01_1",
                "countertop.n.01_2",
                "countertop.n.01_3",
                "coffee_cup.n.01_1",
                "jar__of__sugar.n.01_1",
            ),
            "read": (
                "cabinet.n.01_1",
                "cabinet.n.01_2",
                "countertop.n.01_1",
                "countertop.n.01_2",
                "countertop.n.01_3",
                "coffee_cup.n.01_1",
                "jar__of__sugar.n.01_1",
            ),
            "write": ("jar__of__sugar.n.01_1", "cabinet.n.01_1", "cabinet.n.01_2"),
            "protected": (
                "cabinet.n.01_1",
                "cabinet.n.01_2",
                "countertop.n.01_1",
                "countertop.n.01_2",
                "countertop.n.01_3",
                "coffee_cup.n.01_1",
                "jar__of__sugar.n.01_1",
            ),
        },
        "mb_restore_coffee_supplies": {
            "used": (
                "cabinet.n.01_1",
                "cabinet.n.01_2",
                "countertop.n.01_1",
                "countertop.n.01_2",
                "countertop.n.01_3",
                "coffee_cup.n.01_1",
                "jar__of__sugar.n.01_1",
            ),
            "read": (
                "cabinet.n.01_1",
                "cabinet.n.01_2",
                "countertop.n.01_1",
                "countertop.n.01_2",
                "countertop.n.01_3",
                "coffee_cup.n.01_1",
                "jar__of__sugar.n.01_1",
            ),
            "write": ("coffee_cup.n.01_1", "jar__of__sugar.n.01_1", "cabinet.n.01_1", "cabinet.n.01_2"),
            "protected": (
                "cabinet.n.01_1",
                "cabinet.n.01_2",
                "countertop.n.01_1",
                "countertop.n.01_2",
                "countertop.n.01_3",
                "coffee_cup.n.01_1",
                "jar__of__sugar.n.01_1",
            ),
        },
    },
    scene_bindings={
        "cabinet.n.01_1": "bottom_cabinet_no_top_rkgjer_0",
        "cabinet.n.01_2": "bottom_cabinet_no_top_gjeoer_0",
        "countertop.n.01_1": "countertop_kelzer_0",
        "countertop.n.01_2": "countertop_kelker_0",
        "countertop.n.01_3": "bar_udatjt_0",
    },
    top_surfaces=("countertop.n.01_1", "countertop.n.01_2", "countertop.n.01_3"),
    goal_regions={
        "A1_B1": {
            "key_1": {"coffee_cup.n.01_1": ("countertop.n.01_3", 0.2, 0.2, 0.4, 0.8)},
            "key_2": {
                "coffee_cup.n.01_1": ("countertop.n.01_3", 0.2, 0.2, 0.4, 0.8),
                "jar__of__sugar.n.01_1": ("countertop.n.01_3", 0.6, 0.2, 0.8, 0.8),
            },
        },
        "A1_B2": {
            "key_1": {"coffee_cup.n.01_1": ("countertop.n.01_3", 0.2, 0.2, 0.4, 0.8)},
            "key_2": {
                "coffee_cup.n.01_1": ("countertop.n.01_3", 0.2, 0.2, 0.4, 0.8),
                "jar__of__sugar.n.01_1": ("countertop.n.01_3", 0.6, 0.2, 0.8, 0.8),
            },
        },
        "A2_B1": {
            "key_1": {"coffee_cup.n.01_1": ("countertop.n.01_3", 0.2, 0.2, 0.4, 0.8)},
            "key_2": {
                "coffee_cup.n.01_1": ("countertop.n.01_3", 0.2, 0.2, 0.4, 0.8),
                "jar__of__sugar.n.01_1": ("countertop.n.01_3", 0.6, 0.2, 0.8, 0.8),
            },
        },
        "A2_B2": {
            "key_1": {"coffee_cup.n.01_1": ("countertop.n.01_3", 0.2, 0.2, 0.4, 0.8)},
            "key_2": {
                "coffee_cup.n.01_1": ("countertop.n.01_3", 0.2, 0.2, 0.4, 0.8),
                "jar__of__sugar.n.01_1": ("countertop.n.01_3", 0.6, 0.2, 0.8, 0.8),
            },
        },
    },
    init=VARIANT_INIT['A1_B1'],
    goal=VARIANT_GOAL['A1_B1'],
    variant_init=VARIANT_INIT,
    variant_goal=VARIANT_GOAL,
    initial_states={variant: conditions["key_1"] for variant, conditions in VARIANT_INIT.items()},
)
