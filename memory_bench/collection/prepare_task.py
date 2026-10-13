"""Prepare a task's Key data in the Memory Bench task-instance dataset, around the OmniGibson sampling scripts."""

import argparse
import importlib
import json
import random
import re
import shutil
from pathlib import Path

import yaml

import memory_bench.tasks
from memory_bench.paths import OFFICIAL_TASK_INSTANCES_PATH, TASK_INSTANCES_PATH
from memory_bench.tasks.base import MemoryTask

TASK_NAMES = sorted(path.parent.name for path in Path(memory_bench.tasks.__file__).parent.glob("*/task.py"))
METADATA_DIR = TASK_INSTANCES_PATH / "metadata"


def init(task: MemoryTask):
    """Add the sampling whitelist and copy the stable base scene that the samplers start from."""
    path = METADATA_DIR / "task_custom_lists.json"
    lists = json.loads(path.read_text()) if path.exists() else {}
    lists[task.key_activity] = {
        "room_types": task.room_types,
        task.scene_model: {"whitelist": task.whitelist, "blacklist": {}},
    }
    METADATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(lists, indent=4))

    stable_name = f"{task.scene_model}_stable.json"
    json_dir = TASK_INSTANCES_PATH / "scenes" / task.scene_model / "json"
    json_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(OFFICIAL_TASK_INSTANCES_PATH / "scenes" / task.scene_model / "json" / stable_name, json_dir / stable_name)
    print(f"Initialized {task.task_name} in {TASK_INSTANCES_PATH}")


def assign_variants(task: MemoryTask, seed: int):
    """Split the sampled instances evenly across variants and write each binding into its TRO file, once."""
    manifest_path = METADATA_DIR / f"{task.key_activity}_variants.json"
    if manifest_path.exists():
        raise SystemExit(f"{manifest_path} already exists; variant assignments are frozen. Delete it to reassign.")

    json_dir = TASK_INSTANCES_PATH / "scenes" / task.scene_model / "json"
    prefix = f"{task.scene_model}_task_{task.key_activity}"
    template = json.loads((json_dir / f"{prefix}_0_0_template.json").read_text())["metadata"]["task"]["inst_to_name"]
    pattern = re.compile(rf"{prefix}_0_(\d+)_template-tro_state\.json")
    paths = {
        int(match.group(1)): path
        for path in (json_dir / f"{prefix}_instances").iterdir()
        if (match := pattern.fullmatch(path.name))
    }
    variant_names = sorted(task.variants)
    if not paths or len(paths) % len(variant_names):
        raise SystemExit(f"Need a positive multiple of {len(variant_names)} instances, found {len(paths)}")

    labels = [name for name in variant_names for _ in range(len(paths) // len(variant_names))]
    random.Random(seed).shuffle(labels)
    assignments = dict(zip(sorted(paths), labels))

    for instance_id, variant in assignments.items():
        tro_state = json.loads(paths[instance_id].read_text())
        old = tro_state.pop("inst_to_name", template)
        new = task.bind_variant(old, variant)
        # TRO states are keyed by BDDL instance, so each state has to follow its scene object.
        by_name = {old[inst]: tro_state[inst] for inst in old if inst in tro_state}
        tro_state.update({inst: by_name[new[inst]] for inst in new if new[inst] in by_name})
        tro_state["inst_to_name"] = new
        paths[instance_id].write_text(json.dumps(tro_state, indent=4))

    manifest = {
        "seed": seed,
        "variants": task.variants,
        "instances": {str(instance_id): variant for instance_id, variant in assignments.items()},
    }
    manifest_path.write_text(json.dumps(manifest, indent=4))
    for variant in variant_names:
        ids = [instance_id for instance_id, label in assignments.items() if label == variant]
        print(f"Variant {variant}: instances {ids}")
    print(f"Wrote {manifest_path}")


def register_joylo(task: MemoryTask):
    json_dir = TASK_INSTANCES_PATH / "scenes" / task.scene_model / "json"
    template = json.loads((json_dir / f"{task.scene_model}_task_{task.key_activity}_0_0_template.json").read_text())
    pose = template["metadata"]["task"]["robot_poses"]["robot"][0]
    path = METADATA_DIR / "available_tasks.yaml"
    tasks = yaml.safe_load(path.read_text()) if path.exists() else {}
    tasks[task.key_activity] = {
        0: {
            "robot_start_orientation": pose["orientation"],
            "robot_start_position": pose["position"],
            "scene_model": task.scene_model,
        }
    }
    path.write_text(yaml.safe_dump(tasks))
    print(f"Registered {task.key_activity} in {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", choices=TASK_NAMES)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Add the sampling whitelist and the stable base scene")
    assign_parser = commands.add_parser(
        "assign-variants", help="Split sampled instances evenly across variants and freeze the assignment"
    )
    assign_parser.add_argument("--seed", type=int, required=True)
    commands.add_parser("register-joylo", help="Add the Key activity and its robot start pose to available_tasks.yaml")
    args = parser.parse_args()

    task = importlib.import_module(f"memory_bench.tasks.{args.task}.task").TASK
    if args.command == "init":
        init(task)
    elif args.command == "assign-variants":
        assign_variants(task, args.seed)
    else:
        register_joylo(task)


if __name__ == "__main__":
    main()
