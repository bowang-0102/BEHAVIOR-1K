"""Download selected tasks from the BEHAVIOR challenge raw-data dataset on Hugging Face.

Run from the repository root, for example:
    python -m memory_bench.tools.download_rawdata \
        --config memory_bench/configs/mb_retrieve_chocolate_from_top_cabinet.yaml
    python -m memory_bench.tools.download_rawdata --task-ids 0 22 --local-dir /path/to/rawdata
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml
from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.hf_api import RepoFile


REPO_ID = "behavior-1k/2026-challenge-rawdata"


def task_id(value):
    """Reject invalid IDs before constructing remote or local paths."""
    if isinstance(value, bool) or not str(value).isdecimal() or not 0 <= int(value) <= 9999:
        raise ValueError(f"Expected a task ID between 0 and 9999, got {value!r}")
    return int(value)


def resolve_selection(config_path=None, task_ids=None, local_dir=None):
    if config_path is not None:
        config = yaml.safe_load(Path(config_path).read_text())
        ids = []
        for segment_id, segment in config["segments"].items():
            if segment["role"] != "intervent_task":
                continue
            source = segment.get("source", {})
            if "task_id" not in source:
                raise ValueError(f"segments.{segment_id}.source.task_id is required for downloading a task")
            ids.append(task_id(source["task_id"]))
        if local_dir is None:
            local_dir = config["paths"]["intervent_rawdata_root"]
    else:
        ids = [task_id(value) for value in (task_ids or [])]
    if not ids:
        raise ValueError("No tasks selected; provide --task-ids or a config with intervent_task sources")
    if local_dir is None:
        raise ValueError("--local-dir is required when using --task-ids")
    return sorted(set(ids)), Path(local_dir).expanduser().resolve()


def plan_downloads(api, task_ids, revision, limit_per_task=None):
    """List only selected task directories and pin all downloads to one commit."""
    commit = api.dataset_info(REPO_ID, revision=revision).sha
    files = []
    for selected_id in task_ids:
        directory = f"task-{selected_id:04d}"
        entries = sorted(
            (
                entry
                for entry in api.list_repo_tree(
                    REPO_ID, path_in_repo=directory, repo_type="dataset", revision=commit
                )
                if isinstance(entry, RepoFile)
                and Path(entry.path).parent.as_posix() == directory
                and Path(entry.path).match("episode_*.hdf5")
            ),
            key=lambda entry: entry.path,
        )
        if not entries:
            raise ValueError(f"No episode HDF5 files found in {REPO_ID}/{directory}")
        if limit_per_task is not None:
            entries = entries[:limit_per_task]
        files.extend(entries)
        size = sum(entry.size for entry in entries)
        print(f"{directory}: {len(entries)} recordings, {size / 1024**3:.3f} GiB", flush=True)
    return commit, files


def download_files(files, revision, local_dir, workers):
    failures = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                hf_hub_download,
                repo_id=REPO_ID,
                filename=entry.path,
                repo_type="dataset",
                revision=revision,
                local_dir=str(local_dir),
            ): entry.path
            for entry in files
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            filename = futures[future]
            try:
                future.result()
                print(f"[{completed}/{len(files)}] ready: {filename}", flush=True)
            except Exception as error:
                failures.append(filename)
                print(f"[{completed}/{len(files)}] FAILED: {filename}: {error}", flush=True)
    if failures:
        raise RuntimeError(f"{len(failures)} downloads failed; rerun the same command to retry")


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--config", type=Path, help="Read intervent_task source.task_id values from session YAML")
    selection.add_argument("--task-ids", nargs="+", help="Numeric task IDs, e.g. 0 22")
    parser.add_argument("--local-dir", type=Path, help="Destination; defaults to config paths.intervent_rawdata_root")
    parser.add_argument("--revision", default="main", help="Dataset branch, tag, or commit (default: main)")
    parser.add_argument("--workers", type=positive_int, default=4, help="Concurrent file downloads (default: 4)")
    parser.add_argument(
        "--limit-per-task", type=positive_int, help="Download only the first N recordings per task, sorted by filename"
    )
    parser.add_argument("--dry-run", action="store_true", help="List task counts and sizes without downloading files")
    args = parser.parse_args()
    try:
        task_ids, local_dir = resolve_selection(args.config, args.task_ids, args.local_dir)
    except (KeyError, ValueError) as error:
        parser.error(str(error))

    print(f"Dataset: {REPO_ID}\nDestination: {local_dir}", flush=True)
    revision, files = plan_downloads(HfApi(), task_ids, args.revision, args.limit_per_task)
    print(
        f"Total: {len(files)} recordings, {sum(entry.size for entry in files) / 1024**3:.3f} GiB\n"
        f"Revision: {revision}",
        flush=True,
    )
    if args.dry_run:
        return
    download_files(files, revision, local_dir, args.workers)
    print(f"Done: {local_dir}", flush=True)


if __name__ == "__main__":
    main()
