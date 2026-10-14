"""Normalize and cut the default joint recording for each collected instance."""

import argparse
import subprocess
import sys

import yaml

from memory_bench.session import Session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    parser.add_argument("instance_id", type=int, nargs="?")
    args = parser.parse_args()
    session = Session.load(args.config)
    session.paths.activate()
    count = session.instance_count
    if args.instance_id is not None and not 1 <= args.instance_id <= count:
        parser.error(f"instance_id must be in 1..{count}")
    ids = [args.instance_id] if args.instance_id is not None else range(1, count + 1)
    for instance_id in ids:
        name = f"instance_{instance_id:04d}"
        recording = session.paths.resolve(f"recordings/{session.session_id}/{name}_recording_0001")
        marks_path = recording / "marks.yaml"
        marks = yaml.safe_load(marks_path.read_text())
        if not marks["valid"]:
            raise ValueError(f"Invalid joint recording: {marks_path}")
        normalized = f"segments/{session.session_id}/{name}/joint_normalized.hdf5"
        terminal = recording / marks["segments"][session.query_id]["exit_state"]
        print(f"Normalizing and splitting {name}", flush=True)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "memory_bench.processing.normalize_recording",
                args.config,
                marks["recording"]["hdf5"],
                normalized,
                "--demo",
                str(marks["recording"]["demo"]),
                "--terminal",
                str(terminal),
            ],
            check=True,
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "memory_bench.processing.make_segments",
                args.config,
                "--marks",
                str(marks_path),
                "--normalized",
                normalized,
                "--instance",
                f"instances/{session.session_id}/{name}/instance.json",
            ],
            check=True,
        )


if __name__ == "__main__":
    main()
