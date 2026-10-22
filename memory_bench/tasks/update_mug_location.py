"""Level 2 update task: remember a mug identity, then update both mug locations."""

from memory_bench.tasks.base_task import MemoryTask


_MUGS = ("tall_mug", "short_mug")
_CABINETS = tuple(f"top_cabinet_lkxmne_{index}" for index in range(3))
_IDENTITIES = ("tall_narrow", "short_wide")
_PERMUTATIONS = ("right_shift", "swap")
_VARIANTS = {
    f"{identity}_{permutation}": (identity, permutation) for identity in _IDENTITIES for permutation in _PERMUTATIONS
}


def _variant_mappings():
    mappings = {}
    for variant_id, (identity, permutation) in _VARIANTS.items():
        target = 0 if identity == "tall_narrow" else 1
        other = 1 - target
        if permutation == "right_shift":
            # Physical mug in cabinet 0 moves to cabinet 1; cabinet 1 moves to 2.
            final_cabinets = {0: 1, 1: 2}
        else:
            # Physical mug in cabinet 0 moves to cabinet 2; cabinet 1 moves to 0.
            final_cabinets = {0: 2, 1: 0}

        query_cabinet_indices = [None, None, None]
        query_cabinet_indices[0] = final_cabinets[target]
        query_cabinet_indices[1] = final_cabinets[other]
        query_cabinet_indices[2] = next(index for index in range(3) if index not in query_cabinet_indices[:2])
        mappings[variant_id] = {
            "mb_identify_target_mug": {
                "mug.n.04_1": _MUGS[target],
                "mug.n.04_2": _MUGS[other],
                "cabinet.n.01_1": _CABINETS[target],
                "cabinet.n.01_2": _CABINETS[other],
                "cabinet.n.01_3": _CABINETS[2],
            },
            "mb_relocate_two_mugs": {
                "mug.n.04_1": _MUGS[0],
                "mug.n.04_2": _MUGS[1],
                "cabinet.n.01_1": _CABINETS[0],
                "cabinet.n.01_2": _CABINETS[1],
                "cabinet.n.01_3": _CABINETS[2],
            },
            "mb_retrieve_target_mug_after_relocation": {
                "mug.n.04_1": _MUGS[target],
                "mug.n.04_2": _MUGS[other],
                **{
                    f"cabinet.n.01_{index + 1}": _CABINETS[cabinet_index]
                    for index, cabinet_index in enumerate(query_cabinet_indices)
                },
            },
        }
    return mappings


_ACTIVITY_VARIANTS = _variant_mappings()

TASK = MemoryTask(
    # Task and scene.
    task_name="mb_retrieve_target_mug_after_relocation",
    scene_model="house_single_floor",
    room_types=("kitchen",),

    # Sampling constraints.
    whitelist={
        "mug.n.04": {"mug": {"ntgftr": None, "yxaapv": None}},
        "cabinet.n.01": {"top_cabinet": {"lkxmne": None}},
        "countertop.n.01": {"bar": {"udatjt": None}},
    },

    # Activities and instructions.
    key_activities=("mb_identify_target_mug", "mb_relocate_two_mugs"),
    intervent_activities=("tidying_bedroom", "outfit_a_basic_toolbox"),
    query_activity="mb_retrieve_target_mug_after_relocation",
    query_instruction="Retrieve the mug selected during the first task and place it on the kitchen island.",
    key_instructions={
        variant_id: {
            "mb_identify_target_mug": (
                "Remember the {target} mug as the retrieval target. Put the taller, narrower mug in the "
                "leftmost top cabinet, put the shorter, wider mug in the middle top cabinet, and close both doors."
            ).format(target="taller, narrower" if identity == "tall_narrow" else "shorter, wider"),
            "mb_relocate_two_mugs": (
                "Move the mug from the {source} top cabinet into the {destination} one, then move the mug that "
                "started in the {second_source} cabinet into the {second_destination} one. Close all three doors."
            ).format(
                source="leftmost",
                destination="middle" if permutation == "right_shift" else "rightmost",
                second_source="middle",
                second_destination="rightmost" if permutation == "right_shift" else "leftmost",
            ),
        }
        for variant_id, (identity, permutation) in _VARIANTS.items()
    },

    # Variant bindings and sampling.
    # Key 1 and Query bind mug index 1 to the remembered target. Key 1 keeps the
    # physical arrangement fixed; Key 2 bindings are independent of target identity.
    variants={variant_id: {} for variant_id in _VARIANTS},
    activity_variants=_ACTIVITY_VARIANTS,
    variant_object_models={"tall_mug": ("mug", "ntgftr"), "short_mug": ("mug", "yxaapv")},

    # Object access.
    object_usage={
        "mb_identify_target_mug": {
            "used": (
                "mug.n.04_1",
                "mug.n.04_2",
                "cabinet.n.01_1",
                "cabinet.n.01_2",
                "cabinet.n.01_3",
                "countertop.n.01_1",
            ),
            "read": (
                "mug.n.04_1",
                "mug.n.04_2",
                "cabinet.n.01_1",
                "cabinet.n.01_2",
                "cabinet.n.01_3",
                "countertop.n.01_1",
            ),
            "write": ("mug.n.04_1", "mug.n.04_2", "cabinet.n.01_1", "cabinet.n.01_2"),
            "protected": ("mug.n.04_1", "mug.n.04_2", "cabinet.n.01_1", "cabinet.n.01_2", "cabinet.n.01_3"),
        },
        "mb_relocate_two_mugs": {
            "used": (
                "mug.n.04_1",
                "mug.n.04_2",
                "cabinet.n.01_1",
                "cabinet.n.01_2",
                "cabinet.n.01_3",
            ),
            "read": (
                "mug.n.04_1",
                "mug.n.04_2",
                "cabinet.n.01_1",
                "cabinet.n.01_2",
                "cabinet.n.01_3",
            ),
            "write": (
                "mug.n.04_1",
                "mug.n.04_2",
                "cabinet.n.01_1",
                "cabinet.n.01_2",
                "cabinet.n.01_3",
            ),
            "protected": (
                "mug.n.04_1",
                "mug.n.04_2",
                "cabinet.n.01_1",
                "cabinet.n.01_2",
                "cabinet.n.01_3",
            ),
        },
        "mb_retrieve_target_mug_after_relocation": {
            "used": (
                "mug.n.04_1",
                "mug.n.04_2",
                "cabinet.n.01_1",
                "cabinet.n.01_2",
                "cabinet.n.01_3",
                "countertop.n.01_1",
            ),
            "read": (
                "mug.n.04_1",
                "mug.n.04_2",
                "cabinet.n.01_1",
                "cabinet.n.01_2",
                "cabinet.n.01_3",
            ),
            "write": ("mug.n.04_1", "cabinet.n.01_1", "cabinet.n.01_2", "cabinet.n.01_3"),
            "protected": ("mug.n.04_1", "mug.n.04_2", "cabinet.n.01_1", "cabinet.n.01_2", "cabinet.n.01_3"),
        },
    },

    # BDDL initial and goal conditions.
    init={
        "key_1": [
            ["ontop", "mug.n.04_1", "countertop.n.01_1"],
            ["ontop", "mug.n.04_2", "countertop.n.01_1"],
            ["forall", ["?cabinet", "-", "cabinet.n.01"], ["not", ["open", "?cabinet"]]],
        ],
        "key_2": [
            ["inside", "mug.n.04_1", "cabinet.n.01_1"],
            ["inside", "mug.n.04_2", "cabinet.n.01_2"],
            ["forall", ["?cabinet", "-", "cabinet.n.01"], ["not", ["open", "?cabinet"]]],
        ],
        "query": [
            ["inside", "mug.n.04_1", "cabinet.n.01_1"],
            ["inside", "mug.n.04_2", "cabinet.n.01_2"],
            ["forall", ["?cabinet", "-", "cabinet.n.01"], ["not", ["open", "?cabinet"]]],
        ],
    },
    goal={
        "key_1": [
            ["inside", "mug.n.04_1", "cabinet.n.01_1"],
            ["inside", "mug.n.04_2", "cabinet.n.01_2"],
            ["forall", ["?cabinet", "-", "cabinet.n.01"], ["not", ["open", "?cabinet"]]],
        ],
        "key_2": [
            ["inside", "mug.n.04_1", "cabinet.n.01_2"],
            ["inside", "mug.n.04_2", "cabinet.n.01_3"],
            ["forall", ["?cabinet", "-", "cabinet.n.01"], ["not", ["open", "?cabinet"]]],
        ],
        "query": [
            ["ontop", "mug.n.04_1", "countertop.n.01_1"],
            ["inside", "mug.n.04_2", "cabinet.n.01_2"],
            ["forall", ["?cabinet", "-", "cabinet.n.01"], ["not", ["open", "?cabinet"]]],
        ],
    },
    variant_goal={
        variant_id: {
            "key_2": [
                ["inside", "mug.n.04_1", "cabinet.n.01_2" if permutation == "right_shift" else "cabinet.n.01_3"],
                ["inside", "mug.n.04_2", "cabinet.n.01_3" if permutation == "right_shift" else "cabinet.n.01_1"],
                ["forall", ["?cabinet", "-", "cabinet.n.01"], ["not", ["open", "?cabinet"]]],
            ]
        }
        for variant_id, (_, permutation) in _VARIANTS.items()
    },
)
