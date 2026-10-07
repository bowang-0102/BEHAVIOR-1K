"""Prepare a task's Key data in the Memory Bench task-instance dataset, around the OmniGibson sampling scripts."""

import argparse
import importlib
import json
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
    print(f"Initialized {task.name} in {TASK_INSTANCES_PATH}")


def set_variant(task: MemoryTask, variant: str):
    json_dir = TASK_INSTANCES_PATH / "scenes" / task.scene_model / "json"
    prefix = f"{task.scene_model}_task_{task.key_activity}"
    old = json.loads((json_dir / f"{prefix}_0_0_template.json").read_text())["metadata"]["task"]["inst_to_name"]
    new = task.bind_variant(old, variant)
    changed = [inst for inst in new if new[inst] != old[inst]]

    # Covers both the full template and the partial-rooms template.
    for path in json_dir.glob(f"{prefix}_0_0_template*.json"):
        scene = json.loads(path.read_text())
        scene["metadata"]["task"]["inst_to_name"] = new
        path.write_text(json.dumps(scene, indent=4))

    # TRO states are keyed by BDDL instance, so each swapped state has to follow its scene object.
    for path in (json_dir / f"{prefix}_instances").glob("*-tro_state.json"):
        tro_state = json.loads(path.read_text())
        moved = {old[inst]: tro_state[inst] for inst in changed}
        tro_state.update({inst: moved[new[inst]] for inst in changed})
        path.write_text(json.dumps(tro_state, indent=4))

    print(f"Variant {variant}: " + ", ".join(f"{inst} -> {new[inst]}" for inst in changed or task.variants[variant]))


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
    variant_parser = commands.add_parser("set-variant", help="Rebind the sampled template to one of the task variants")
    variant_parser.add_argument("variant")
    commands.add_parser("register-joylo", help="Add the Key activity and its robot start pose to available_tasks.yaml")
    args = parser.parse_args()

    task = importlib.import_module(f"memory_bench.tasks.{args.task}.task").TASK
    if args.command == "init":
        init(task)
    elif args.command == "set-variant":
        if args.variant not in task.variants:
            parser.error(f"{args.task} variants: {', '.join(task.variants)}")
        set_variant(task, args.variant)
    else:
        register_joylo(task)


if __name__ == "__main__":
    main()
