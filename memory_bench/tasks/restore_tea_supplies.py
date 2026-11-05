from memory_bench.tasks.base_task import MemoryTask


# Conditions use fixed physical roles; only the remembered facts vary.
VARIANT_INIT = {
    "A1_B1": {
        "key_1": [
            ["ontop", "coffee_cup.n.01_1", "bookcase.n.01_1"],
            ["ontop", "jar__of__sugar.n.01_1", "bookcase.n.01_3"],
        ],
        "key_2": [["ontop", "coffee_cup.n.01_1", "desk.n.01_1"], ["ontop", "jar__of__sugar.n.01_1", "bookcase.n.01_3"]],
        "query": [["ontop", "coffee_cup.n.01_1", "desk.n.01_1"], ["ontop", "jar__of__sugar.n.01_1", "desk.n.01_1"]],
    },
    "A1_B2": {
        "key_1": [
            ["ontop", "coffee_cup.n.01_1", "bookcase.n.01_1"],
            ["ontop", "jar__of__sugar.n.01_1", "bookcase.n.01_4"],
        ],
        "key_2": [["ontop", "coffee_cup.n.01_1", "desk.n.01_1"], ["ontop", "jar__of__sugar.n.01_1", "bookcase.n.01_4"]],
        "query": [["ontop", "coffee_cup.n.01_1", "desk.n.01_1"], ["ontop", "jar__of__sugar.n.01_1", "desk.n.01_1"]],
    },
    "A2_B1": {
        "key_1": [
            ["ontop", "coffee_cup.n.01_1", "bookcase.n.01_2"],
            ["ontop", "jar__of__sugar.n.01_1", "bookcase.n.01_3"],
        ],
        "key_2": [["ontop", "coffee_cup.n.01_1", "desk.n.01_1"], ["ontop", "jar__of__sugar.n.01_1", "bookcase.n.01_3"]],
        "query": [["ontop", "coffee_cup.n.01_1", "desk.n.01_1"], ["ontop", "jar__of__sugar.n.01_1", "desk.n.01_1"]],
    },
    "A2_B2": {
        "key_1": [
            ["ontop", "coffee_cup.n.01_1", "bookcase.n.01_2"],
            ["ontop", "jar__of__sugar.n.01_1", "bookcase.n.01_4"],
        ],
        "key_2": [["ontop", "coffee_cup.n.01_1", "desk.n.01_1"], ["ontop", "jar__of__sugar.n.01_1", "bookcase.n.01_4"]],
        "query": [["ontop", "coffee_cup.n.01_1", "desk.n.01_1"], ["ontop", "jar__of__sugar.n.01_1", "desk.n.01_1"]],
    },
}

VARIANT_GOAL = {
    "A1_B1": {
        "key_1": [["ontop", "coffee_cup.n.01_1", "desk.n.01_1"], ["ontop", "jar__of__sugar.n.01_1", "bookcase.n.01_3"]],
        "key_2": [["ontop", "coffee_cup.n.01_1", "desk.n.01_1"], ["ontop", "jar__of__sugar.n.01_1", "desk.n.01_1"]],
        "query": [
            ["ontop", "coffee_cup.n.01_1", "bookcase.n.01_1"],
            ["ontop", "jar__of__sugar.n.01_1", "bookcase.n.01_3"],
        ],
    },
    "A1_B2": {
        "key_1": [["ontop", "coffee_cup.n.01_1", "desk.n.01_1"], ["ontop", "jar__of__sugar.n.01_1", "bookcase.n.01_4"]],
        "key_2": [["ontop", "coffee_cup.n.01_1", "desk.n.01_1"], ["ontop", "jar__of__sugar.n.01_1", "desk.n.01_1"]],
        "query": [
            ["ontop", "coffee_cup.n.01_1", "bookcase.n.01_1"],
            ["ontop", "jar__of__sugar.n.01_1", "bookcase.n.01_4"],
        ],
    },
    "A2_B1": {
        "key_1": [["ontop", "coffee_cup.n.01_1", "desk.n.01_1"], ["ontop", "jar__of__sugar.n.01_1", "bookcase.n.01_3"]],
        "key_2": [["ontop", "coffee_cup.n.01_1", "desk.n.01_1"], ["ontop", "jar__of__sugar.n.01_1", "desk.n.01_1"]],
        "query": [
            ["ontop", "coffee_cup.n.01_1", "bookcase.n.01_2"],
            ["ontop", "jar__of__sugar.n.01_1", "bookcase.n.01_3"],
        ],
    },
    "A2_B2": {
        "key_1": [["ontop", "coffee_cup.n.01_1", "desk.n.01_1"], ["ontop", "jar__of__sugar.n.01_1", "bookcase.n.01_4"]],
        "key_2": [["ontop", "coffee_cup.n.01_1", "desk.n.01_1"], ["ontop", "jar__of__sugar.n.01_1", "desk.n.01_1"]],
        "query": [
            ["ontop", "coffee_cup.n.01_1", "bookcase.n.01_2"],
            ["ontop", "jar__of__sugar.n.01_1", "bookcase.n.01_4"],
        ],
    },
}


TASK = MemoryTask(
    task_name="mb_restore_tea_supplies",
    scene_model="house_double_floor_upper",
    room_types=("living_room",),
    whitelist={
        "bookcase.n.01": {"bookcase": {"otwukr": None}},
        "desk.n.01": {"desk": {"iotfzl": None}},
        "coffee_cup.n.01": {"coffee_cup": {"nbhcgu": None}},
        "jar__of__sugar.n.01": {"jar_of_sugar": {"pnbbfb": None}},
    },
    key_activities=("mb_prepare_tea_cup", "mb_prepare_tea_sugar"),
    intervent_activities=("organizing_art_supplies", "setting_mousetraps"),
    query_activity="mb_restore_tea_supplies",
    query_instruction=(
        "Put away the tea-break supplies: return the cup and sugar jar to their respective original "
        "bookcase tops."
    ),
    key_instructions={
        "A1_B1": {
            "mb_prepare_tea_cup": "Bring the cup from its bookcase top to the clear part of the desk.",
            "mb_prepare_tea_sugar": "Bring the sugar jar from its bookcase top to the desk. Leave the cup in place.",
        },
        "A1_B2": {
            "mb_prepare_tea_cup": "Bring the cup from its bookcase top to the clear part of the desk.",
            "mb_prepare_tea_sugar": "Bring the sugar jar from its bookcase top to the desk. Leave the cup in place.",
        },
        "A2_B1": {
            "mb_prepare_tea_cup": "Bring the cup from its bookcase top to the clear part of the desk.",
            "mb_prepare_tea_sugar": "Bring the sugar jar from its bookcase top to the desk. Leave the cup in place.",
        },
        "A2_B2": {
            "mb_prepare_tea_cup": "Bring the cup from its bookcase top to the clear part of the desk.",
            "mb_prepare_tea_sugar": "Bring the sugar jar from its bookcase top to the desk. Leave the cup in place.",
        },
    },
    variants={"A1_B1": {}, "A1_B2": {}, "A2_B1": {}, "A2_B2": {}},
    object_usage={
        "mb_prepare_tea_cup": {
            "used": (
                "bookcase.n.01_1",
                "bookcase.n.01_2",
                "bookcase.n.01_3",
                "bookcase.n.01_4",
                "desk.n.01_1",
                "coffee_cup.n.01_1",
                "jar__of__sugar.n.01_1",
            ),
            "read": (
                "bookcase.n.01_1",
                "bookcase.n.01_2",
                "bookcase.n.01_3",
                "bookcase.n.01_4",
                "desk.n.01_1",
                "coffee_cup.n.01_1",
                "jar__of__sugar.n.01_1",
            ),
            "write": ("coffee_cup.n.01_1",),
            "protected": (
                "bookcase.n.01_1",
                "bookcase.n.01_2",
                "bookcase.n.01_3",
                "bookcase.n.01_4",
                "desk.n.01_1",
                "coffee_cup.n.01_1",
                "jar__of__sugar.n.01_1",
            ),
        },
        "mb_prepare_tea_sugar": {
            "used": (
                "bookcase.n.01_1",
                "bookcase.n.01_2",
                "bookcase.n.01_3",
                "bookcase.n.01_4",
                "desk.n.01_1",
                "coffee_cup.n.01_1",
                "jar__of__sugar.n.01_1",
            ),
            "read": (
                "bookcase.n.01_1",
                "bookcase.n.01_2",
                "bookcase.n.01_3",
                "bookcase.n.01_4",
                "desk.n.01_1",
                "coffee_cup.n.01_1",
                "jar__of__sugar.n.01_1",
            ),
            "write": ("jar__of__sugar.n.01_1",),
            "protected": (
                "bookcase.n.01_1",
                "bookcase.n.01_2",
                "bookcase.n.01_3",
                "bookcase.n.01_4",
                "desk.n.01_1",
                "coffee_cup.n.01_1",
                "jar__of__sugar.n.01_1",
            ),
        },
        "mb_restore_tea_supplies": {
            "used": (
                "bookcase.n.01_1",
                "bookcase.n.01_2",
                "bookcase.n.01_3",
                "bookcase.n.01_4",
                "desk.n.01_1",
                "coffee_cup.n.01_1",
                "jar__of__sugar.n.01_1",
            ),
            "read": (
                "bookcase.n.01_1",
                "bookcase.n.01_2",
                "bookcase.n.01_3",
                "bookcase.n.01_4",
                "desk.n.01_1",
                "coffee_cup.n.01_1",
                "jar__of__sugar.n.01_1",
            ),
            "write": ("coffee_cup.n.01_1", "jar__of__sugar.n.01_1"),
            "protected": (
                "bookcase.n.01_1",
                "bookcase.n.01_2",
                "bookcase.n.01_3",
                "bookcase.n.01_4",
                "desk.n.01_1",
                "coffee_cup.n.01_1",
                "jar__of__sugar.n.01_1",
            ),
        },
    },
    scene_bindings={
        "bookcase.n.01_1": "bookcase_otwukr_0",
        "bookcase.n.01_2": "bookcase_otwukr_3",
        "bookcase.n.01_3": "bookcase_otwukr_1",
        "bookcase.n.01_4": "bookcase_otwukr_2",
        "desk.n.01_1": "desk_iotfzl_0",
    },
    top_surfaces=("bookcase.n.01_1", "bookcase.n.01_2", "bookcase.n.01_3", "bookcase.n.01_4", "desk.n.01_1"),
    goal_regions={
        "A1_B1": {
            "key_1": {"coffee_cup.n.01_1": ("desk.n.01_1", 0.2, 0.12, 0.8, 0.3)},
            "key_2": {
                "coffee_cup.n.01_1": ("desk.n.01_1", 0.2, 0.12, 0.8, 0.3),
                "jar__of__sugar.n.01_1": ("desk.n.01_1", 0.2, 0.4, 0.8, 0.55),
            },
        },
        "A1_B2": {
            "key_1": {"coffee_cup.n.01_1": ("desk.n.01_1", 0.2, 0.12, 0.8, 0.3)},
            "key_2": {
                "coffee_cup.n.01_1": ("desk.n.01_1", 0.2, 0.12, 0.8, 0.3),
                "jar__of__sugar.n.01_1": ("desk.n.01_1", 0.2, 0.4, 0.8, 0.55),
            },
        },
        "A2_B1": {
            "key_1": {"coffee_cup.n.01_1": ("desk.n.01_1", 0.2, 0.12, 0.8, 0.3)},
            "key_2": {
                "coffee_cup.n.01_1": ("desk.n.01_1", 0.2, 0.12, 0.8, 0.3),
                "jar__of__sugar.n.01_1": ("desk.n.01_1", 0.2, 0.4, 0.8, 0.55),
            },
        },
        "A2_B2": {
            "key_1": {"coffee_cup.n.01_1": ("desk.n.01_1", 0.2, 0.12, 0.8, 0.3)},
            "key_2": {
                "coffee_cup.n.01_1": ("desk.n.01_1", 0.2, 0.12, 0.8, 0.3),
                "jar__of__sugar.n.01_1": ("desk.n.01_1", 0.2, 0.4, 0.8, 0.55),
            },
        },
    },
    init=VARIANT_INIT['A1_B1'],
    goal=VARIANT_GOAL['A1_B1'],
    variant_init=VARIANT_INIT,
    variant_goal=VARIANT_GOAL,
    initial_states={variant: conditions["key_1"] for variant, conditions in VARIANT_INIT.items()},
)
