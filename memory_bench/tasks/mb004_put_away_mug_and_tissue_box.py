from memory_bench.tasks.base_task import MemoryTask

_VARIANTS = ("A1B1", "A1B2", "A2B1", "A2B2")
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
    task_name="mb004_put_away_mug_and_tissue_box",
    scene_model="house_double_floor_upper",
    room_types=("living_room",),
    whitelist={
        "mug.n.04": {"mug": {"ppdqbj": None}},
        "box__of__tissue.n.01": {"box_of_tissues": {"uglbjc": 0.65}},
        "bookcase.n.01": {"bookcase": {"otwukr": None}},
        "coffee_table.n.01": {"coffee_table": {"osroux": None}},
    },
    key_activities=("mb_place_mug_on_coffee_table", "mb_place_tissue_box_on_coffee_table"),
    intervent_activities=("organizing_art_supplies", "getting_organized_for_work"),
    query_activity="mb_put_away_mug_and_tissue_box",
    query_instruction=(
        "Return the mug and the tissue box from the coffee table to the respective places you took them from."
    ),
    key_instructions={
        variant: {
            "mb_place_mug_on_coffee_table": "Place the mug from the top of the bookcase on the coffee table.",
            "mb_place_tissue_box_on_coffee_table": (
                "Place the tissue box from the top of the bookcase on the coffee table beside the mug "
                "without stacking them."
            ),
        }
        for variant in _VARIANTS
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
            ("mb_place_mug_on_coffee_table", ("mug.n.04_1",)),
            ("mb_place_tissue_box_on_coffee_table", ("box__of__tissue.n.01_1",)),
            ("mb_put_away_mug_and_tissue_box", ("mug.n.04_1", "box__of__tissue.n.01_1")),
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
        for variant in _VARIANTS
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
        for variant in _VARIANTS
    },
)
