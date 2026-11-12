from memory_bench.tasks.base_task import MemoryTask

_VARIANTS = ("A", "B")


TASK = MemoryTask(
    task_name="mb003_place_radio_by_previous_state",
    scene_model="house_double_floor_lower",
    room_types=("living_room",),
    whitelist={
        "radio_receiver.n.01": {"radio": {"vgioak": None}},
        "coffee_table.n.01": {"coffee_table": {"koagbh": None}},
        "sofa.n.01": {"sofa": {"frlxgz": None}},
        "cabinet.n.01": {"bottom_cabinet": {"rhdbzv": None}},
    },
    key_activities=("mb_move_radio_to_sofa",),
    intervent_activities=("hanging_pictures", "putting_shoes_on_rack"),
    query_activity="mb_place_radio_by_previous_state",
    query_instruction=(
        "If the radio was on when you first found it on the coffee table, place it on the TV stand and turn it on. "
        "If it was off, return it to the coffee table and leave it off."
    ),
    key_instructions={
        variant: {
            "mb_move_radio_to_sofa": "Move the radio from the coffee table to a sofa cushion, leaving it off."
        }
        for variant in _VARIANTS
    },
    # The physical radio and all supports are independent of the remembered state.
    variants={
        variant: {
            "radio_receiver.n.01_1": "radio",
            "coffee_table.n.01_1": "coffee_table_koagbh_0",
            "sofa.n.01_1": "sofa_frlxgz_0",
            "cabinet.n.01_1": "bottom_cabinet_rhdbzv_0",
        }
        for variant in _VARIANTS
    },
    variant_object_models={"radio": ("radio", "vgioak")},
    object_usage={
        activity: {
            "used": ("radio_receiver.n.01_1", "coffee_table.n.01_1", "sofa.n.01_1", "cabinet.n.01_1"),
            "read": ("radio_receiver.n.01_1", "coffee_table.n.01_1", "sofa.n.01_1", "cabinet.n.01_1"),
            "write": ("radio_receiver.n.01_1",),
            "protected": ("radio_receiver.n.01_1", "coffee_table.n.01_1", "sofa.n.01_1", "cabinet.n.01_1"),
        }
        for activity in ("mb_move_radio_to_sofa", "mb_place_radio_by_previous_state")
    },
    init={
        "A": {
            "key_1": [
                ["ontop", "radio_receiver.n.01_1", "coffee_table.n.01_1"],
                ["toggled_on", "radio_receiver.n.01_1"],
            ],
            "query": [
                ["ontop", "radio_receiver.n.01_1", "sofa.n.01_1"],
                ["not", ["toggled_on", "radio_receiver.n.01_1"]],
            ],
        },
        "B": {
            "key_1": [
                ["ontop", "radio_receiver.n.01_1", "coffee_table.n.01_1"],
                ["not", ["toggled_on", "radio_receiver.n.01_1"]],
            ],
            "query": [
                ["ontop", "radio_receiver.n.01_1", "sofa.n.01_1"],
                ["not", ["toggled_on", "radio_receiver.n.01_1"]],
            ],
        },
    },
    goal={
        "A": {
            "key_1": [
                ["ontop", "radio_receiver.n.01_1", "sofa.n.01_1"],
                ["not", ["toggled_on", "radio_receiver.n.01_1"]],
            ],
            "query": [
                ["ontop", "radio_receiver.n.01_1", "cabinet.n.01_1"],
                ["toggled_on", "radio_receiver.n.01_1"],
            ],
        },
        "B": {
            "key_1": [
                ["ontop", "radio_receiver.n.01_1", "sofa.n.01_1"],
                ["not", ["toggled_on", "radio_receiver.n.01_1"]],
            ],
            "query": [
                ["ontop", "radio_receiver.n.01_1", "coffee_table.n.01_1"],
                ["not", ["toggled_on", "radio_receiver.n.01_1"]],
            ],
        },
    },
)
