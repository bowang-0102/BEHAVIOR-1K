from memory_bench.tasks.base_task import MemoryTask


# Conditions use fixed physical roles; only the remembered facts vary.
VARIANT_INIT = {
    "A": {
        "key_1": [
            ["ontop", "toolbox.n.01_1", "desk.n.01_1"],
            ["ontop", "toolbox.n.01_2", "desk.n.01_1"],
            ["ontop", "flashlight.n.01_1", "desk.n.01_1"],
            ["not", ["open", "toolbox.n.01_1"]],
            ["not", ["open", "toolbox.n.01_2"]],
        ],
        "query": [
            ["ontop", "toolbox.n.01_1", "desk.n.01_1"],
            ["ontop", "toolbox.n.01_2", "desk.n.01_1"],
            ["inside", "flashlight.n.01_1", "toolbox.n.01_1"],
            ["not", ["open", "toolbox.n.01_1"]],
            ["not", ["open", "toolbox.n.01_2"]],
        ],
    },
    "B": {
        "key_1": [
            ["ontop", "toolbox.n.01_1", "desk.n.01_1"],
            ["ontop", "toolbox.n.01_2", "desk.n.01_1"],
            ["ontop", "flashlight.n.01_1", "desk.n.01_1"],
            ["not", ["open", "toolbox.n.01_1"]],
            ["not", ["open", "toolbox.n.01_2"]],
        ],
        "query": [
            ["ontop", "toolbox.n.01_1", "desk.n.01_1"],
            ["ontop", "toolbox.n.01_2", "desk.n.01_1"],
            ["inside", "flashlight.n.01_1", "toolbox.n.01_2"],
            ["not", ["open", "toolbox.n.01_1"]],
            ["not", ["open", "toolbox.n.01_2"]],
        ],
    },
}

VARIANT_GOAL = {
    "A": {
        "key_1": [
            ["ontop", "toolbox.n.01_1", "desk.n.01_1"],
            ["ontop", "toolbox.n.01_2", "desk.n.01_1"],
            ["inside", "flashlight.n.01_1", "toolbox.n.01_1"],
            ["not", ["open", "toolbox.n.01_1"]],
            ["not", ["open", "toolbox.n.01_2"]],
        ],
        "query": [
            ["ontop", "toolbox.n.01_1", "desk.n.01_1"],
            ["ontop", "toolbox.n.01_2", "desk.n.01_1"],
            ["ontop", "flashlight.n.01_1", "desk.n.01_1"],
            ["not", ["open", "toolbox.n.01_1"]],
            ["not", ["open", "toolbox.n.01_2"]],
        ],
    },
    "B": {
        "key_1": [
            ["ontop", "toolbox.n.01_1", "desk.n.01_1"],
            ["ontop", "toolbox.n.01_2", "desk.n.01_1"],
            ["inside", "flashlight.n.01_1", "toolbox.n.01_2"],
            ["not", ["open", "toolbox.n.01_1"]],
            ["not", ["open", "toolbox.n.01_2"]],
        ],
        "query": [
            ["ontop", "toolbox.n.01_1", "desk.n.01_1"],
            ["ontop", "toolbox.n.01_2", "desk.n.01_1"],
            ["ontop", "flashlight.n.01_1", "desk.n.01_1"],
            ["not", ["open", "toolbox.n.01_1"]],
            ["not", ["open", "toolbox.n.01_2"]],
        ],
    },
}


TASK = MemoryTask(
    task_name="mb_retrieve_stored_flashlight",
    scene_model="house_double_floor_upper",
    room_types=("bedroom",),
    whitelist={
        "desk.n.01": {"desk": {"uqcmzf": None}},
        "toolbox.n.01": {"toolbox": {"redmtl": None}},
        "flashlight.n.01": {"flashlight": {"jtrbkc": None}},
    },
    key_activities=("mb_store_bedroom_flashlight",),
    intervent_activities=("organizing_art_supplies", "setting_mousetraps"),
    query_activity="mb_retrieve_stored_flashlight",
    query_instruction=(
        "Retrieve the flashlight you stored earlier, leave it on the clear part of the bedroom desk, and "
        "close both toolboxes."
    ),
    key_instructions={
        "A": {
            "mb_store_bedroom_flashlight": (
                "Put the flashlight in the toolbox farther from the bedroom doorway and close both "
                "toolboxes. Leave the boxes in place."
            ),
        },
        "B": {
            "mb_store_bedroom_flashlight": (
                "Put the flashlight in the toolbox nearer the bedroom doorway and close both toolboxes. "
                "Leave the boxes in place."
            ),
        },
    },
    variants={"A": {}, "B": {}},
    object_usage={
        "mb_store_bedroom_flashlight": {
            "used": ("desk.n.01_1", "toolbox.n.01_1", "toolbox.n.01_2", "flashlight.n.01_1"),
            "read": ("desk.n.01_1", "toolbox.n.01_1", "toolbox.n.01_2", "flashlight.n.01_1"),
            "write": ("flashlight.n.01_1", "toolbox.n.01_1", "toolbox.n.01_2"),
            "protected": ("desk.n.01_1", "toolbox.n.01_1", "toolbox.n.01_2", "flashlight.n.01_1"),
        },
        "mb_retrieve_stored_flashlight": {
            "used": ("desk.n.01_1", "toolbox.n.01_1", "toolbox.n.01_2", "flashlight.n.01_1"),
            "read": ("desk.n.01_1", "toolbox.n.01_1", "toolbox.n.01_2", "flashlight.n.01_1"),
            "write": ("flashlight.n.01_1", "toolbox.n.01_1", "toolbox.n.01_2"),
            "protected": ("desk.n.01_1", "toolbox.n.01_1", "toolbox.n.01_2", "flashlight.n.01_1"),
        },
    },
    scene_bindings={"desk.n.01_1": "desk_uqcmzf_0"},
    shared_object_roles=("toolbox.n.01_1", "toolbox.n.01_2"),
    top_surfaces=("desk.n.01_1",),
    initial_regions={
        "A": {
            "toolbox.n.01_1": ("desk.n.01_1", 0.12, 0.15, 0.38, 0.38),
            "toolbox.n.01_2": ("desk.n.01_1", 0.6, 0.6, 0.85, 0.85),
            "flashlight.n.01_1": ("desk.n.01_1", 0.1, 0.6, 0.35, 0.85),
        },
        "B": {
            "toolbox.n.01_1": ("desk.n.01_1", 0.12, 0.15, 0.38, 0.38),
            "toolbox.n.01_2": ("desk.n.01_1", 0.6, 0.6, 0.85, 0.85),
            "flashlight.n.01_1": ("desk.n.01_1", 0.1, 0.6, 0.35, 0.85),
        },
    },
    goal_regions={
        "A": {
            "key_1": {
                "toolbox.n.01_1": ("desk.n.01_1", 0.12, 0.15, 0.38, 0.38),
                "toolbox.n.01_2": ("desk.n.01_1", 0.6, 0.6, 0.85, 0.85),
            },
            "query": {
                "toolbox.n.01_1": ("desk.n.01_1", 0.12, 0.15, 0.38, 0.38),
                "toolbox.n.01_2": ("desk.n.01_1", 0.6, 0.6, 0.85, 0.85),
                "flashlight.n.01_1": ("desk.n.01_1", 0.1, 0.6, 0.35, 0.85),
            },
        },
        "B": {
            "key_1": {
                "toolbox.n.01_1": ("desk.n.01_1", 0.12, 0.15, 0.38, 0.38),
                "toolbox.n.01_2": ("desk.n.01_1", 0.6, 0.6, 0.85, 0.85),
            },
            "query": {
                "toolbox.n.01_1": ("desk.n.01_1", 0.12, 0.15, 0.38, 0.38),
                "toolbox.n.01_2": ("desk.n.01_1", 0.6, 0.6, 0.85, 0.85),
                "flashlight.n.01_1": ("desk.n.01_1", 0.1, 0.6, 0.35, 0.85),
            },
        },
    },
    init=VARIANT_INIT['A'],
    goal=VARIANT_GOAL['A'],
    variant_init=VARIANT_INIT,
    variant_goal=VARIANT_GOAL,
    initial_states={variant: conditions["key_1"] for variant, conditions in VARIANT_INIT.items()},
)
