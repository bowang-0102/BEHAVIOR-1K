from memory_bench.tasks.base import MemoryTask

TASK = MemoryTask(
    task_name="mug_in_top_cabinet",
    scene_model="house_single_floor",
    key_activity="mb_mug_into_top_cabinet",
    query_activity="mb_retrieve_mug_from_top_cabinet",
    query_instruction="Retrieve the mug and place it on the kitchen island.",
    room_types=["kitchen"],
    whitelist={
        "mug.n.04": {"mug": {"kitxam": None}},
        "cabinet.n.01": {"top_cabinet": {"lkxmne": None}},
        "countertop.n.01": {"bar": {"udatjt": None}},
    },
    # The Key target is always cabinet.n.01_1; a variant picks which top cabinet that is.
    variants={
        "A": {"cabinet.n.01_1": "top_cabinet_lkxmne_0"},
        "B": {"cabinet.n.01_1": "top_cabinet_lkxmne_1"},
        "C": {"cabinet.n.01_1": "top_cabinet_lkxmne_2"},
    },
)
