from memory_bench.tasks.base_task import MemoryTask

TASK = MemoryTask(
    task_name="mb003_putting_radio_on_tv_stand",
    scene_model="house_double_floor_lower",
    room_types=("living_room",),
    # Separate synsets let the sampler fix each radio model independently.
    whitelist={
        "radio_receiver.n.01": {"radio": {"vgioak": None}},
        "receiver.n.01": {"radio": {"wxnicr": None}},
        "coffee_table.n.01": {"coffee_table": {"koagbh": None}},
        "cabinet.n.01": {"bottom_cabinet": {"rhdbzv": None}},
    },
    key_activities=("mb_turning_off_radios",),
    intervent_activities=("hanging_pictures", "putting_shoes_on_rack"),
    query_activity="mb_putting_radio_on_tv_stand",
    query_instruction=(
        "Move the radio you were using earlier from the coffee table to the top of the TV stand. "
        "Keep both radios off and leave the other radio on the coffee table."
    ),
    key_instructions={
        variant: {
            "mb_turning_off_radios": "Before stepping away, turn off both radios on the coffee table."
        }
        for variant in ("A", "B")
    },
    # radio_receiver.n.01_1 always denotes the radio that was on before the Key.
    variants={
        "A": {
            "radio_receiver.n.01_1": "radio_a",
            "receiver.n.01_1": "radio_b",
            "coffee_table.n.01_1": "coffee_table_koagbh_0",
            "cabinet.n.01_1": "bottom_cabinet_rhdbzv_0",
        },
        "B": {
            "radio_receiver.n.01_1": "radio_b",
            "receiver.n.01_1": "radio_a",
            "coffee_table.n.01_1": "coffee_table_koagbh_0",
            "cabinet.n.01_1": "bottom_cabinet_rhdbzv_0",
        },
    },
    variant_object_models={
        "radio_a": ("radio", "vgioak"),
        "radio_b": ("radio", "wxnicr"),
    },
    object_usage={
        activity: {
            "used": ("radio_receiver.n.01_1", "receiver.n.01_1", "coffee_table.n.01_1", "cabinet.n.01_1"),
            "read": ("radio_receiver.n.01_1", "receiver.n.01_1", "coffee_table.n.01_1", "cabinet.n.01_1"),
            "write": (
                ("radio_receiver.n.01_1", "receiver.n.01_1")
                if activity == "mb_turning_off_radios"
                else ("radio_receiver.n.01_1",)
            ),
            "protected": ("radio_receiver.n.01_1", "receiver.n.01_1", "coffee_table.n.01_1", "cabinet.n.01_1"),
        }
        for activity in ("mb_turning_off_radios", "mb_putting_radio_on_tv_stand")
    },
    init={
        variant: {
            "key_1": [
                ["ontop", "radio_receiver.n.01_1", "coffee_table.n.01_1"],
                ["ontop", "receiver.n.01_1", "coffee_table.n.01_1"],
                ["toggled_on", "radio_receiver.n.01_1"],
                ["not", ["toggled_on", "receiver.n.01_1"]],
            ],
            "query": [
                ["ontop", "radio_receiver.n.01_1", "coffee_table.n.01_1"],
                ["ontop", "receiver.n.01_1", "coffee_table.n.01_1"],
                ["not", ["toggled_on", "radio_receiver.n.01_1"]],
                ["not", ["toggled_on", "receiver.n.01_1"]],
            ],
        }
        for variant in ("A", "B")
    },
    goal={
        variant: {
            "key_1": [
                ["ontop", "radio_receiver.n.01_1", "coffee_table.n.01_1"],
                ["ontop", "receiver.n.01_1", "coffee_table.n.01_1"],
                ["not", ["toggled_on", "radio_receiver.n.01_1"]],
                ["not", ["toggled_on", "receiver.n.01_1"]],
            ],
            "query": [
                ["ontop", "radio_receiver.n.01_1", "cabinet.n.01_1"],
                ["ontop", "receiver.n.01_1", "coffee_table.n.01_1"],
                ["not", ["toggled_on", "radio_receiver.n.01_1"]],
                ["not", ["toggled_on", "receiver.n.01_1"]],
            ],
        }
        for variant in ("A", "B")
    },
)
