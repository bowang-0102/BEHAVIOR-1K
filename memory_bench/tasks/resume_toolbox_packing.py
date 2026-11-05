from memory_bench.tasks.base_task import MemoryTask


# Conditions use fixed physical roles; only the remembered facts vary.
VARIANT_INIT = {
    "A": {
        "key_1": [
            ["ontop", "toolbox.n.01_1", "breakfast_table.n.01_1"],
            ["ontop", "toolbox.n.01_2", "breakfast_table.n.01_1"],
            ["ontop", "drill.n.01_1", "breakfast_table.n.01_1"],
            ["open", "toolbox.n.01_1"],
            ["not", ["open", "toolbox.n.01_2"]],
        ],
        "query": [
            ["ontop", "toolbox.n.01_1", "breakfast_table.n.01_1"],
            ["ontop", "toolbox.n.01_2", "breakfast_table.n.01_1"],
            ["ontop", "drill.n.01_1", "breakfast_table.n.01_1"],
            ["not", ["open", "toolbox.n.01_1"]],
            ["not", ["open", "toolbox.n.01_2"]],
        ],
    },
    "B": {
        "key_1": [
            ["ontop", "toolbox.n.01_1", "breakfast_table.n.01_1"],
            ["ontop", "toolbox.n.01_2", "breakfast_table.n.01_1"],
            ["ontop", "drill.n.01_1", "breakfast_table.n.01_1"],
            ["open", "toolbox.n.01_2"],
            ["not", ["open", "toolbox.n.01_1"]],
        ],
        "query": [
            ["ontop", "toolbox.n.01_1", "breakfast_table.n.01_1"],
            ["ontop", "toolbox.n.01_2", "breakfast_table.n.01_1"],
            ["ontop", "drill.n.01_1", "breakfast_table.n.01_1"],
            ["not", ["open", "toolbox.n.01_1"]],
            ["not", ["open", "toolbox.n.01_2"]],
        ],
    },
}

VARIANT_GOAL = {
    "A": {
        "key_1": [
            ["ontop", "toolbox.n.01_1", "breakfast_table.n.01_1"],
            ["ontop", "toolbox.n.01_2", "breakfast_table.n.01_1"],
            ["ontop", "drill.n.01_1", "breakfast_table.n.01_1"],
            ["not", ["open", "toolbox.n.01_1"]],
            ["not", ["open", "toolbox.n.01_2"]],
        ],
        "query": [
            ["ontop", "toolbox.n.01_1", "breakfast_table.n.01_1"],
            ["ontop", "toolbox.n.01_2", "breakfast_table.n.01_1"],
            ["inside", "drill.n.01_1", "toolbox.n.01_1"],
            ["not", ["open", "toolbox.n.01_1"]],
            ["not", ["open", "toolbox.n.01_2"]],
        ],
    },
    "B": {
        "key_1": [
            ["ontop", "toolbox.n.01_1", "breakfast_table.n.01_1"],
            ["ontop", "toolbox.n.01_2", "breakfast_table.n.01_1"],
            ["ontop", "drill.n.01_1", "breakfast_table.n.01_1"],
            ["not", ["open", "toolbox.n.01_1"]],
            ["not", ["open", "toolbox.n.01_2"]],
        ],
        "query": [
            ["ontop", "toolbox.n.01_1", "breakfast_table.n.01_1"],
            ["ontop", "toolbox.n.01_2", "breakfast_table.n.01_1"],
            ["inside", "drill.n.01_1", "toolbox.n.01_2"],
            ["not", ["open", "toolbox.n.01_1"]],
            ["not", ["open", "toolbox.n.01_2"]],
        ],
    },
}


TASK = MemoryTask(
    task_name="mb_resume_toolbox_packing",
    scene_model="house_double_floor_lower",
    room_types=("kitchen",),
    whitelist={
        "breakfast_table.n.01": {"breakfast_table": {"xftrki": None}},
        "toolbox.n.01": {"toolbox": {"redmtl": None}},
        "drill.n.01": {"drill": {"nzgmza": None}},
    },
    key_activities=("mb_pause_toolbox_packing",),
    intervent_activities=("turning_on_radio", "putting_shoes_on_rack"),
    query_activity="mb_resume_toolbox_packing",
    query_instruction=(
        "Continue packing the tools: put the drill into the toolbox that was open before the break, and "
        "close both lids. Keep the boxes in place."
    ),
    key_instructions={
        "A": {
            "mb_pause_toolbox_packing": (
                "Pause tool packing by closing the open toolbox. Leave the drill on the table and keep "
                "both boxes in place."
            ),
        },
        "B": {
            "mb_pause_toolbox_packing": (
                "Pause tool packing by closing the open toolbox. Leave the drill on the table and keep "
                "both boxes in place."
            ),
        },
    },
    variants={"A": {}, "B": {}},
    object_usage={
        "mb_pause_toolbox_packing": {
            "used": ("breakfast_table.n.01_1", "toolbox.n.01_1", "toolbox.n.01_2", "drill.n.01_1"),
            "read": ("breakfast_table.n.01_1", "toolbox.n.01_1", "toolbox.n.01_2", "drill.n.01_1"),
            "write": ("toolbox.n.01_1", "toolbox.n.01_2"),
            "protected": ("breakfast_table.n.01_1", "toolbox.n.01_1", "toolbox.n.01_2", "drill.n.01_1"),
        },
        "mb_resume_toolbox_packing": {
            "used": ("breakfast_table.n.01_1", "toolbox.n.01_1", "toolbox.n.01_2", "drill.n.01_1"),
            "read": ("breakfast_table.n.01_1", "toolbox.n.01_1", "toolbox.n.01_2", "drill.n.01_1"),
            "write": ("drill.n.01_1", "toolbox.n.01_1", "toolbox.n.01_2"),
            "protected": ("breakfast_table.n.01_1", "toolbox.n.01_1", "toolbox.n.01_2", "drill.n.01_1"),
        },
    },
    scene_bindings={"breakfast_table.n.01_1": "breakfast_table_xftrki_0"},
    shared_object_roles=("toolbox.n.01_1", "toolbox.n.01_2"),
    top_surfaces=("breakfast_table.n.01_1",),
    initial_regions={
        "A": {
            "toolbox.n.01_1": ("breakfast_table.n.01_1", 0.25, 0.2, 0.75, 0.3),
            "toolbox.n.01_2": ("breakfast_table.n.01_1", 0.25, 0.7, 0.75, 0.8),
            "drill.n.01_1": ("breakfast_table.n.01_1", 0.25, 0.45, 0.75, 0.55),
        },
        "B": {
            "toolbox.n.01_1": ("breakfast_table.n.01_1", 0.25, 0.2, 0.75, 0.3),
            "toolbox.n.01_2": ("breakfast_table.n.01_1", 0.25, 0.7, 0.75, 0.8),
            "drill.n.01_1": ("breakfast_table.n.01_1", 0.25, 0.45, 0.75, 0.55),
        },
    },
    goal_regions={
        "A": {
            "key_1": {
                "toolbox.n.01_1": ("breakfast_table.n.01_1", 0.25, 0.2, 0.75, 0.3),
                "toolbox.n.01_2": ("breakfast_table.n.01_1", 0.25, 0.7, 0.75, 0.8),
                "drill.n.01_1": ("breakfast_table.n.01_1", 0.25, 0.45, 0.75, 0.55),
            },
            "query": {
                "toolbox.n.01_1": ("breakfast_table.n.01_1", 0.25, 0.2, 0.75, 0.3),
                "toolbox.n.01_2": ("breakfast_table.n.01_1", 0.25, 0.7, 0.75, 0.8),
            },
        },
        "B": {
            "key_1": {
                "toolbox.n.01_1": ("breakfast_table.n.01_1", 0.25, 0.2, 0.75, 0.3),
                "toolbox.n.01_2": ("breakfast_table.n.01_1", 0.25, 0.7, 0.75, 0.8),
                "drill.n.01_1": ("breakfast_table.n.01_1", 0.25, 0.45, 0.75, 0.55),
            },
            "query": {
                "toolbox.n.01_1": ("breakfast_table.n.01_1", 0.25, 0.2, 0.75, 0.3),
                "toolbox.n.01_2": ("breakfast_table.n.01_1", 0.25, 0.7, 0.75, 0.8),
            },
        },
    },
    init=VARIANT_INIT['A'],
    goal=VARIANT_GOAL['A'],
    variant_init=VARIANT_INIT,
    variant_goal=VARIANT_GOAL,
    initial_states={variant: conditions["key_1"] for variant, conditions in VARIANT_INIT.items()},
)
