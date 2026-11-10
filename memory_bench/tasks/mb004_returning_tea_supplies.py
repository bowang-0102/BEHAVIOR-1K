from memory_bench.tasks.base_task import MemoryTask

_OBJECTS = (
    "mug.n.04_1",
    "box__of__tissue.n.01_1",
    "bookcase.n.01_1",
    "bookcase.n.01_2",
    "bookcase.n.01_3",
    "bookcase.n.01_4",
    "coffee_table.n.01_1",
)


TASK = MemoryTask(
    task_name="mb004_returning_tea_supplies",
    scene_model="house_double_floor_upper",
    room_types=("living_room",),
    whitelist={
        "mug.n.04": {"mug": {"ppdqbj": None}},
        "box__of__tissue.n.01": {"box_of_tissues": {"uglbjc": None}},
        "bookcase.n.01": {"bookcase": {"otwukr": None}},
        "coffee_table.n.01": {"coffee_table": {"osroux": None}},
    },
    key_activities=("mb_taking_mug_to_tea_table", "mb_taking_tissues_to_tea_table"),
    intervent_activities=("organizing_art_supplies", "getting_organized_for_work"),
    query_activity="mb_returning_tea_supplies",
    query_instruction=(
        "Put away the prepared tea supplies: return the mug and the tissue box "
        "to the respective places you took them from."
    ),
    key_instructions={
        variant: {
            "mb_taking_mug_to_tea_table": "Bring the stored mug to the tea table so it is ready to use.",
            "mb_taking_tissues_to_tea_table": (
                "Bring the stored tissue box to the tea table. Place it separately beside the mug, without stacking."
            ),
        }
        for variant in ("A1B1", "A1B2", "A2B1", "A2B2")
    },
    # Roles 1/2 are the mug sources; roles 3/4 are the independent tissue sources.
    # In every variant, roles 1 and 3 denote the historical return destinations.
    variants={
        variant: {
            "bookcase.n.01_1": mug_source,
            "bookcase.n.01_2": other_mug_source,
            "bookcase.n.01_3": tissue_source,
            "bookcase.n.01_4": other_tissue_source,
            "coffee_table.n.01_1": "coffee_table_osroux_0",
        }
        for variant, mug_source, other_mug_source, tissue_source, other_tissue_source in (
            ("A1B1", "bookcase_otwukr_0", "bookcase_otwukr_1", "bookcase_otwukr_2", "bookcase_otwukr_3"),
            ("A1B2", "bookcase_otwukr_0", "bookcase_otwukr_1", "bookcase_otwukr_3", "bookcase_otwukr_2"),
            ("A2B1", "bookcase_otwukr_1", "bookcase_otwukr_0", "bookcase_otwukr_2", "bookcase_otwukr_3"),
            ("A2B2", "bookcase_otwukr_1", "bookcase_otwukr_0", "bookcase_otwukr_3", "bookcase_otwukr_2"),
        )
    },
    object_usage={
        activity: {
            "used": _OBJECTS,
            "read": _OBJECTS,
            "write": writes,
            "protected": _OBJECTS,
        }
        for activity, writes in (
            ("mb_taking_mug_to_tea_table", ("mug.n.04_1",)),
            ("mb_taking_tissues_to_tea_table", ("box__of__tissue.n.01_1",)),
            ("mb_returning_tea_supplies", ("mug.n.04_1", "box__of__tissue.n.01_1")),
        )
    },
    init={
        variant: {
            "key_1": [
                ["ontop", "mug.n.04_1", "bookcase.n.01_1"],
                ["ontop", "box__of__tissue.n.01_1", "bookcase.n.01_3"],
            ],
            "key_2": [
                ["ontop", "mug.n.04_1", "coffee_table.n.01_1"],
                ["ontop", "box__of__tissue.n.01_1", "bookcase.n.01_3"],
            ],
            "query": [
                ["ontop", "mug.n.04_1", "coffee_table.n.01_1"],
                ["ontop", "box__of__tissue.n.01_1", "coffee_table.n.01_1"],
            ],
        }
        for variant in ("A1B1", "A1B2", "A2B1", "A2B2")
    },
    goal={
        variant: {
            "key_1": [
                ["ontop", "mug.n.04_1", "coffee_table.n.01_1"],
                ["ontop", "box__of__tissue.n.01_1", "bookcase.n.01_3"],
            ],
            "key_2": [
                ["ontop", "mug.n.04_1", "coffee_table.n.01_1"],
                ["ontop", "box__of__tissue.n.01_1", "coffee_table.n.01_1"],
            ],
            "query": [
                ["ontop", "mug.n.04_1", "bookcase.n.01_1"],
                ["ontop", "box__of__tissue.n.01_1", "bookcase.n.01_3"],
            ],
        }
        for variant in ("A1B1", "A1B2", "A2B1", "A2B2")
    },
)
