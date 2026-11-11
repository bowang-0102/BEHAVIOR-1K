from memory_bench.tasks.base_task import MemoryTask

TASK = MemoryTask(
    # Task and scene.
    task_name="mb000_retrieve_bottle_of_milk",
    scene_model="house_double_floor_lower",
    room_types=("kitchen",),

    # Sampling constraints.
    whitelist={
        "bottle__of__milk.n.01": {"bottle_of_milk": {"mrejrs": None}},
        "electric_refrigerator.n.01": {"fridge": {"petcxr": None}},
        "microwave.n.02": {"microwave": {"hjjxmi": None}},
        "breakfast_table.n.01": {"breakfast_table": {"xftrki": None}},
    },

    # Activities and instructions.
    key_activities=("mb_store_bottle_of_milk",),
    intervent_activities=("turning_on_radio", "putting_shoes_on_rack"),
    query_activity="mb_retrieve_bottle_of_milk",
    query_instruction=(
        "Retrieve the bottle of milk you stored earlier and place it on the dining table, "
        "then close the door without turning on the appliance."
    ),
    key_instructions={
        "A": {
            "mb_store_bottle_of_milk": (
                "Put the bottle of milk from the dining table into the refrigerator and close the door."
            ),
        },
        "B": {
            "mb_store_bottle_of_milk": (
                "Put the bottle of milk from the dining table into the microwave and close the door, "
                "leaving it off."
            ),
        },
    },

    # Variant bindings and sampling.
    variants={
        "A": {
            "electric_refrigerator.n.01_1": "fridge_petcxr_0",
            "microwave.n.02_1": "microwave_hjjxmi_0",
            "breakfast_table.n.01_1": "breakfast_table_xftrki_0",
        },
        "B": {
            "electric_refrigerator.n.01_1": "fridge_petcxr_0",
            "microwave.n.02_1": "microwave_hjjxmi_0",
            "breakfast_table.n.01_1": "breakfast_table_xftrki_0",
        },
    },

    # Object access.
    object_usage={
        "mb_store_bottle_of_milk": {
            "used": (
                "bottle__of__milk.n.01_1",
                "electric_refrigerator.n.01_1",
                "microwave.n.02_1",
                "breakfast_table.n.01_1",
            ),
            "read": (
                "bottle__of__milk.n.01_1",
                "electric_refrigerator.n.01_1",
                "microwave.n.02_1",
                "breakfast_table.n.01_1",
            ),
            "write": ("bottle__of__milk.n.01_1", "electric_refrigerator.n.01_1", "microwave.n.02_1"),
            "protected": (
                "bottle__of__milk.n.01_1",
                "electric_refrigerator.n.01_1",
                "microwave.n.02_1",
                "breakfast_table.n.01_1",
            ),
        },
        "mb_retrieve_bottle_of_milk": {
            "used": (
                "bottle__of__milk.n.01_1",
                "electric_refrigerator.n.01_1",
                "microwave.n.02_1",
                "breakfast_table.n.01_1",
            ),
            "read": (
                "bottle__of__milk.n.01_1",
                "electric_refrigerator.n.01_1",
                "microwave.n.02_1",
                "breakfast_table.n.01_1",
            ),
            "write": ("bottle__of__milk.n.01_1", "electric_refrigerator.n.01_1", "microwave.n.02_1"),
            "protected": (
                "bottle__of__milk.n.01_1",
                "electric_refrigerator.n.01_1",
                "microwave.n.02_1",
                "breakfast_table.n.01_1",
            ),
        },
    },

    # BDDL initial and goal conditions.
    init={
        "A": {
            "key_1": [
                ["ontop", "bottle__of__milk.n.01_1", "breakfast_table.n.01_1"],
                ["not", ["open", "electric_refrigerator.n.01_1"]],
                ["not", ["open", "microwave.n.02_1"]],
                ["not", ["toggled_on", "microwave.n.02_1"]],
            ],
            "query": [
                ["inside", "bottle__of__milk.n.01_1", "electric_refrigerator.n.01_1"],
                ["not", ["open", "electric_refrigerator.n.01_1"]],
                ["not", ["open", "microwave.n.02_1"]],
                ["not", ["toggled_on", "microwave.n.02_1"]],
            ],
        },
        "B": {
            "key_1": [
                ["ontop", "bottle__of__milk.n.01_1", "breakfast_table.n.01_1"],
                ["not", ["open", "electric_refrigerator.n.01_1"]],
                ["not", ["open", "microwave.n.02_1"]],
                ["not", ["toggled_on", "microwave.n.02_1"]],
            ],
            "query": [
                ["inside", "bottle__of__milk.n.01_1", "microwave.n.02_1"],
                ["not", ["open", "electric_refrigerator.n.01_1"]],
                ["not", ["open", "microwave.n.02_1"]],
                ["not", ["toggled_on", "microwave.n.02_1"]],
            ],
        },
    },
    goal={
        "A": {
            "key_1": [
                ["inside", "bottle__of__milk.n.01_1", "electric_refrigerator.n.01_1"],
                ["not", ["open", "electric_refrigerator.n.01_1"]],
                ["not", ["open", "microwave.n.02_1"]],
                ["not", ["toggled_on", "microwave.n.02_1"]],
            ],
            "query": [
                ["ontop", "bottle__of__milk.n.01_1", "breakfast_table.n.01_1"],
                ["not", ["open", "electric_refrigerator.n.01_1"]],
                ["not", ["open", "microwave.n.02_1"]],
                ["not", ["toggled_on", "microwave.n.02_1"]],
            ],
        },
        "B": {
            "key_1": [
                ["inside", "bottle__of__milk.n.01_1", "microwave.n.02_1"],
                ["not", ["open", "electric_refrigerator.n.01_1"]],
                ["not", ["open", "microwave.n.02_1"]],
                ["not", ["toggled_on", "microwave.n.02_1"]],
            ],
            "query": [
                ["ontop", "bottle__of__milk.n.01_1", "breakfast_table.n.01_1"],
                ["not", ["open", "electric_refrigerator.n.01_1"]],
                ["not", ["open", "microwave.n.02_1"]],
                ["not", ["toggled_on", "microwave.n.02_1"]],
            ],
        },
    },
)
