from memory_bench.tasks.base_task import MemoryTask


# Conditions use fixed physical roles; only the remembered facts vary.
VARIANT_INIT = {
    "left_A": {
        "key_1": [
            ["ontop", "packing_box.n.02_1", "tabletop.n.01_1"],
            ["ontop", "flashlight.n.01_1", "tabletop.n.01_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "key_2": [
            ["ontop", "packing_box.n.02_1", "tabletop.n.01_1"],
            ["inside", "flashlight.n.01_1", "packing_box.n.02_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "query": [
            ["inside", "packing_box.n.02_1", "cabinet.n.01_1"],
            ["inside", "flashlight.n.01_1", "packing_box.n.02_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
    },
    "left_B": {
        "key_1": [
            ["ontop", "packing_box.n.02_1", "tabletop.n.01_1"],
            ["ontop", "flashlight.n.01_1", "tabletop.n.01_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "key_2": [
            ["ontop", "packing_box.n.02_1", "tabletop.n.01_1"],
            ["inside", "flashlight.n.01_1", "packing_box.n.02_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "query": [
            ["inside", "packing_box.n.02_1", "cabinet.n.01_2"],
            ["inside", "flashlight.n.01_1", "packing_box.n.02_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
    },
    "right_A": {
        "key_1": [
            ["ontop", "packing_box.n.02_1", "tabletop.n.01_1"],
            ["ontop", "flashlight.n.01_1", "tabletop.n.01_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "key_2": [
            ["ontop", "packing_box.n.02_1", "tabletop.n.01_1"],
            ["inside", "flashlight.n.01_1", "packing_box.n.02_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "query": [
            ["inside", "packing_box.n.02_1", "cabinet.n.01_1"],
            ["inside", "flashlight.n.01_1", "packing_box.n.02_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
    },
    "right_B": {
        "key_1": [
            ["ontop", "packing_box.n.02_1", "tabletop.n.01_1"],
            ["ontop", "flashlight.n.01_1", "tabletop.n.01_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "key_2": [
            ["ontop", "packing_box.n.02_1", "tabletop.n.01_1"],
            ["inside", "flashlight.n.01_1", "packing_box.n.02_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "query": [
            ["inside", "packing_box.n.02_1", "cabinet.n.01_2"],
            ["inside", "flashlight.n.01_1", "packing_box.n.02_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
    },
}

VARIANT_GOAL = {
    "left_A": {
        "key_1": [
            ["ontop", "packing_box.n.02_1", "tabletop.n.01_1"],
            ["inside", "flashlight.n.01_1", "packing_box.n.02_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "key_2": [
            ["inside", "packing_box.n.02_1", "cabinet.n.01_1"],
            ["inside", "flashlight.n.01_1", "packing_box.n.02_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "query": [
            ["ontop", "packing_box.n.02_1", "tabletop.n.01_1"],
            ["ontop", "flashlight.n.01_1", "tabletop.n.01_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
    },
    "left_B": {
        "key_1": [
            ["ontop", "packing_box.n.02_1", "tabletop.n.01_1"],
            ["inside", "flashlight.n.01_1", "packing_box.n.02_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "key_2": [
            ["inside", "packing_box.n.02_1", "cabinet.n.01_2"],
            ["inside", "flashlight.n.01_1", "packing_box.n.02_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "query": [
            ["ontop", "packing_box.n.02_1", "tabletop.n.01_1"],
            ["ontop", "flashlight.n.01_1", "tabletop.n.01_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
    },
    "right_A": {
        "key_1": [
            ["ontop", "packing_box.n.02_1", "tabletop.n.01_1"],
            ["inside", "flashlight.n.01_1", "packing_box.n.02_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "key_2": [
            ["inside", "packing_box.n.02_1", "cabinet.n.01_1"],
            ["inside", "flashlight.n.01_1", "packing_box.n.02_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "query": [
            ["ontop", "packing_box.n.02_1", "tabletop.n.01_1"],
            ["ontop", "flashlight.n.01_1", "tabletop.n.01_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
    },
    "right_B": {
        "key_1": [
            ["ontop", "packing_box.n.02_1", "tabletop.n.01_1"],
            ["inside", "flashlight.n.01_1", "packing_box.n.02_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "key_2": [
            ["inside", "packing_box.n.02_1", "cabinet.n.01_2"],
            ["inside", "flashlight.n.01_1", "packing_box.n.02_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "query": [
            ["ontop", "packing_box.n.02_1", "tabletop.n.01_1"],
            ["ontop", "flashlight.n.01_1", "tabletop.n.01_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
    },
}


TASK = MemoryTask(
    task_name="mb_retrieve_packed_flashlight",
    scene_model="house_single_floor",
    room_types=("utility_room",),
    whitelist={
        "cabinet.n.01": {"bottom_cabinet_no_top": {"gjrero": None}},
        "tabletop.n.01": {"countertop": {"fjkase": None}},
        "packing_box.n.02": {
            "packing_box": {"cjhskr": [0.22799999877155644, 0.28200001600178437, 0.13200000240118606]},
        },
        "flashlight.n.01": {"flashlight": {"jtrbkc": None}},
    },
    key_activities=("mb_pack_flashlight", "mb_store_flashlight_box"),
    intervent_activities=("collecting_childrens_toys", "clean_up_your_desk"),
    query_activity="mb_retrieve_packed_flashlight",
    query_instruction=(
        "Retrieve the box containing the flashlight. Put the box on the worktop, take out the flashlight "
        "into the clear tool area, and close both cabinets."
    ),
    key_instructions={
        "left_A": {
            "mb_pack_flashlight": "Pack the flashlight flat into the small paper box on the worktop.",
            "mb_store_flashlight_box": (
                "Clear the worktop by moving the box, with the flashlight still inside, into the cabinet "
                "nearer the child bedroom. Close both cabinets."
            ),
        },
        "left_B": {
            "mb_pack_flashlight": "Pack the flashlight flat into the small paper box on the worktop.",
            "mb_store_flashlight_box": (
                "Clear the worktop by moving the box, with the flashlight still inside, into the cabinet "
                "farther from the child bedroom. Close both cabinets."
            ),
        },
        "right_A": {
            "mb_pack_flashlight": "Pack the flashlight flat into the small paper box on the worktop.",
            "mb_store_flashlight_box": (
                "Clear the worktop by moving the box, with the flashlight still inside, into the cabinet "
                "nearer the child bedroom. Close both cabinets."
            ),
        },
        "right_B": {
            "mb_pack_flashlight": "Pack the flashlight flat into the small paper box on the worktop.",
            "mb_store_flashlight_box": (
                "Clear the worktop by moving the box, with the flashlight still inside, into the cabinet "
                "farther from the child bedroom. Close both cabinets."
            ),
        },
    },
    variants={"left_A": {}, "left_B": {}, "right_A": {}, "right_B": {}},
    object_usage={
        "mb_pack_flashlight": {
            "used": ("cabinet.n.01_1", "cabinet.n.01_2", "tabletop.n.01_1", "packing_box.n.02_1", "flashlight.n.01_1"),
            "read": ("cabinet.n.01_1", "cabinet.n.01_2", "tabletop.n.01_1", "packing_box.n.02_1", "flashlight.n.01_1"),
            "write": ("flashlight.n.01_1",),
            "protected": ("cabinet.n.01_1", "cabinet.n.01_2", "tabletop.n.01_1", "packing_box.n.02_1", "flashlight.n.01_1"),
        },
        "mb_store_flashlight_box": {
            "used": ("cabinet.n.01_1", "cabinet.n.01_2", "tabletop.n.01_1", "packing_box.n.02_1", "flashlight.n.01_1"),
            "read": ("cabinet.n.01_1", "cabinet.n.01_2", "tabletop.n.01_1", "packing_box.n.02_1", "flashlight.n.01_1"),
            "write": ("packing_box.n.02_1", "flashlight.n.01_1", "cabinet.n.01_1", "cabinet.n.01_2"),
            "protected": ("cabinet.n.01_1", "cabinet.n.01_2", "tabletop.n.01_1", "packing_box.n.02_1", "flashlight.n.01_1"),
        },
        "mb_retrieve_packed_flashlight": {
            "used": ("cabinet.n.01_1", "cabinet.n.01_2", "tabletop.n.01_1", "packing_box.n.02_1", "flashlight.n.01_1"),
            "read": ("cabinet.n.01_1", "cabinet.n.01_2", "tabletop.n.01_1", "packing_box.n.02_1", "flashlight.n.01_1"),
            "write": ("packing_box.n.02_1", "flashlight.n.01_1", "cabinet.n.01_1", "cabinet.n.01_2"),
            "protected": ("cabinet.n.01_1", "cabinet.n.01_2", "tabletop.n.01_1", "packing_box.n.02_1", "flashlight.n.01_1"),
        },
    },
    scene_bindings={
        "cabinet.n.01_1": "bottom_cabinet_no_top_gjrero_0",
        "cabinet.n.01_2": "bottom_cabinet_no_top_gjrero_1",
        "tabletop.n.01_1": "countertop_fjkase_0",
    },
    top_surfaces=("tabletop.n.01_1",),
    initial_regions={
        "left_A": {
            "packing_box.n.02_1": ("tabletop.n.01_1", 0.2, 0.15, 0.8, 0.3),
            "flashlight.n.01_1": ("tabletop.n.01_1", 0.2, 0.45, 0.8, 0.55),
        },
        "left_B": {
            "packing_box.n.02_1": ("tabletop.n.01_1", 0.2, 0.15, 0.8, 0.3),
            "flashlight.n.01_1": ("tabletop.n.01_1", 0.2, 0.45, 0.8, 0.55),
        },
        "right_A": {
            "packing_box.n.02_1": ("tabletop.n.01_1", 0.2, 0.7, 0.8, 0.85),
            "flashlight.n.01_1": ("tabletop.n.01_1", 0.2, 0.45, 0.8, 0.55),
        },
        "right_B": {
            "packing_box.n.02_1": ("tabletop.n.01_1", 0.2, 0.7, 0.8, 0.85),
            "flashlight.n.01_1": ("tabletop.n.01_1", 0.2, 0.45, 0.8, 0.55),
        },
    },
    goal_regions={
        "left_A": {
            "key_1": {"packing_box.n.02_1": ("tabletop.n.01_1", 0.2, 0.15, 0.8, 0.3)},
            "query": {
                "packing_box.n.02_1": ("tabletop.n.01_1", 0.2, 0.15, 0.8, 0.3),
                "flashlight.n.01_1": ("tabletop.n.01_1", 0.2, 0.45, 0.8, 0.55),
            },
        },
        "left_B": {
            "key_1": {"packing_box.n.02_1": ("tabletop.n.01_1", 0.2, 0.15, 0.8, 0.3)},
            "query": {
                "packing_box.n.02_1": ("tabletop.n.01_1", 0.2, 0.15, 0.8, 0.3),
                "flashlight.n.01_1": ("tabletop.n.01_1", 0.2, 0.45, 0.8, 0.55),
            },
        },
        "right_A": {
            "key_1": {"packing_box.n.02_1": ("tabletop.n.01_1", 0.2, 0.7, 0.8, 0.85)},
            "query": {
                "packing_box.n.02_1": ("tabletop.n.01_1", 0.2, 0.15, 0.8, 0.3),
                "flashlight.n.01_1": ("tabletop.n.01_1", 0.2, 0.45, 0.8, 0.55),
            },
        },
        "right_B": {
            "key_1": {"packing_box.n.02_1": ("tabletop.n.01_1", 0.2, 0.7, 0.8, 0.85)},
            "query": {
                "packing_box.n.02_1": ("tabletop.n.01_1", 0.2, 0.15, 0.8, 0.3),
                "flashlight.n.01_1": ("tabletop.n.01_1", 0.2, 0.45, 0.8, 0.55),
            },
        },
    },
    variant_weights={"left_A": 38, "left_B": 37, "right_A": 37, "right_B": 38},
    init=VARIANT_INIT['left_A'],
    goal=VARIANT_GOAL['left_A'],
    variant_init=VARIANT_INIT,
    variant_goal=VARIANT_GOAL,
    initial_states={variant: conditions["key_1"] for variant, conditions in VARIANT_INIT.items()},
)
