# Mug in the top cabinet

Key (`mb_mug_into_top_cabinet`): move the mug from island `bar_udatjt_0` into
`cabinet.n.01_1` and close all three `top_cabinet_lkxmne` doors. Query
(`mb_retrieve_mug_from_top_cabinet`): bring the mug back to the island and close
all doors. Variant A/B/C binds `cabinet.n.01_1` to `top_cabinet_lkxmne_0/1/2`;
the left/center/right labels still need to be read off the JoyLo camera view.

## Prepare the Key task data

All task data lives in `datasets/memory-bench-task-instances/`, next to the
official `2026-challenge-task-instances/`, which is only read (for the stable
base scene). The directory is generated: `init` writes it from the `TASK` fields in `task.py`.
Setting `OMNIGIBSON_TASK_INSTANCES_DATASET` points the OmniGibson samplers,
`BehaviorTask` and JoyLo at it instead of the official dataset.

Run from the repository root:

```bash
export OMNIGIBSON_TASK_INSTANCES_DATASET=memory-bench-task-instances
export OMNIGIBSON_HEADLESS=1
export PYTHONPATH=memory_bench/src
PREP="conda run -n behavior python memory_bench/scripts/prepare_task.py mug_in_top_cabinet"
SAMPLING=OmniGibson/scripts/sampling

$PREP init
# PYTHONBREAKPOINT=0 skips the inspection breakpoint before the template is saved.
PYTHONBREAKPOINT=0 conda run -n behavior python $SAMPLING/sample_b1k_tasks.py \
  --activity mb_mug_into_top_cabinet --overwrite
conda run -n behavior python $SAMPLING/multiply_b1k_tasks.py \
  --activity mb_mug_into_top_cabinet --start_idx 1 --end_idx 9 --partial_save --headless
conda run -n behavior python $SAMPLING/sample_robot_pose.py --activity mb_mug_into_top_cabinet

$PREP assign-variants --seed 0
$PREP register-joylo
```

The template is sampled once and never rebound. `assign-variants` shuffles the
instances with the seed, gives each variant the same number of them, writes the
variant's full `inst_to_name` into each instance's TRO file, and freezes the
split in `metadata/mb_mug_into_top_cabinet_variants.json`. It refuses to run
again while that manifest exists, so recordings keep matching their instance
files. The instance count must be a multiple of the number of variants.

## Teleoperate

The first JoyLo run needs a display and GELLO hardware. Keep
`OMNIGIBSON_TASK_INSTANCES_DATASET` exported and load the full scene.
`--instance-id` is required: without it JoyLo stays on the template binding and
ignores the instance files. Each reset advances to the next instance, and the
recording stores `instance_id` per demo; look up its variant in the manifest.

```bash
export MB_ROOT=/path/to/memory_bench_data
mkdir -p "$MB_ROOT/recordings"
OMNIGIBSON_HEADLESS=0 conda run -n behavior python joylo/scripts/launch_og.py \
  --task-name mb_mug_into_top_cabinet --partial-load False --instance-id 1 \
  --recording-path "$MB_ROOT/recordings/mug_in_top_cabinet.hdf5"
```

Open only the target cabinet, place and release the mug, close the door; the
JoyLo goal panel highlights each Key goal condition once it holds. Until a separate
Query start state exists, continue in the same run: retrieve the mug and put it
back on the island. Check grasp/release, stability after release, and collisions
with the cabinet and the stove by eye.
