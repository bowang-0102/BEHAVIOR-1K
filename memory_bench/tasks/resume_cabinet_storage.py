from memory_bench.tasks.base_task import MemoryTask


# Conditions use fixed physical roles; only the remembered facts vary.
VARIANT_INIT = {
    "A": {
        "key_1": [
            ["ontop", "flashlight.n.01_1", "tabletop.n.01_1"],
            ["open", "cabinet.n.01_1"],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "query": [
            ["ontop", "flashlight.n.01_1", "tabletop.n.01_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
    },
    "B": {
        "key_1": [
            ["ontop", "flashlight.n.01_1", "tabletop.n.01_1"],
            ["open", "cabinet.n.01_2"],
            ["not", ["open", "cabinet.n.01_1"]],
        ],
        "query": [
            ["ontop", "flashlight.n.01_1", "tabletop.n.01_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
    },
}

VARIANT_GOAL = {
    "A": {
        "key_1": [
            ["ontop", "flashlight.n.01_1", "tabletop.n.01_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "query": [
            ["inside", "flashlight.n.01_1", "cabinet.n.01_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
    },
    "B": {
        "key_1": [
            ["ontop", "flashlight.n.01_1", "tabletop.n.01_1"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
        "query": [
            ["inside", "flashlight.n.01_1", "cabinet.n.01_2"],
            ["not", ["open", "cabinet.n.01_1"]],
            ["not", ["open", "cabinet.n.01_2"]],
        ],
    },
}


TASK = MemoryTask(
    task_name="mb_resume_cabinet_storage",
    scene_model="house_single_floor",
    room_types=("utility_room",),
    whitelist={
        "cabinet.n.01": {"bottom_cabinet_no_top": {"gjrero": None}},
        "tabletop.n.01": {"countertop": {"fjkase": None}},
        "flashlight.n.01": {"flashlight": {"jtrbkc": None}},
    },
    key_activities=("mb_pause_cabinet_storage",),
    intervent_activities=("collecting_childrens_toys", "clean_up_your_desk"),
    query_activity="mb_resume_cabinet_storage",
    query_instruction=(
        "Finish putting away the flashlight: place it inside the cabinet that was open before the break, "
        "and close both cabinets."
    ),
    key_instructions={
        "A": {
            "mb_pause_cabinet_storage": (
                "Pause the storage work by closing the open cabinet. Leave the flashlight on the worktop "
                "for later."
            ),
        },
        "B": {
            "mb_pause_cabinet_storage": (
                "Pause the storage work by closing the open cabinet. Leave the flashlight on the worktop "
                "for later."
            ),
        },
    },
    variants={"A": {}, "B": {}},
    object_usage={
        "mb_pause_cabinet_storage": {
            "used": ("cabinet.n.01_1", "cabinet.n.01_2", "tabletop.n.01_1", "flashlight.n.01_1"),
            "read": ("cabinet.n.01_1", "cabinet.n.01_2", "tabletop.n.01_1", "flashlight.n.01_1"),
            "write": ("cabinet.n.01_1", "cabinet.n.01_2"),
            "protected": ("cabinet.n.01_1", "cabinet.n.01_2", "tabletop.n.01_1", "flashlight.n.01_1"),
        },
        "mb_resume_cabinet_storage": {
            "used": ("cabinet.n.01_1", "cabinet.n.01_2", "tabletop.n.01_1", "flashlight.n.01_1"),
            "read": ("cabinet.n.01_1", "cabinet.n.01_2", "tabletop.n.01_1", "flashlight.n.01_1"),
            "write": ("flashlight.n.01_1", "cabinet.n.01_1", "cabinet.n.01_2"),
            "protected": ("cabinet.n.01_1", "cabinet.n.01_2", "tabletop.n.01_1", "flashlight.n.01_1"),
        },
    },
    scene_bindings={
        "cabinet.n.01_1": "bottom_cabinet_no_top_gjrero_0",
        "cabinet.n.01_2": "bottom_cabinet_no_top_gjrero_1",
        "tabletop.n.01_1": "countertop_fjkase_0",
    },
    top_surfaces=("tabletop.n.01_1",),
    initial_regions={
        "A": {"flashlight.n.01_1": ("tabletop.n.01_1", 0.2, 0.4, 0.8, 0.6)},
        "B": {"flashlight.n.01_1": ("tabletop.n.01_1", 0.2, 0.4, 0.8, 0.6)},
    },
    init=VARIANT_INIT['A'],
    goal=VARIANT_GOAL['A'],
    variant_init=VARIANT_INIT,
    variant_goal=VARIANT_GOAL,
    initial_states={variant: conditions["key_1"] for variant, conditions in VARIANT_INIT.items()},
)
