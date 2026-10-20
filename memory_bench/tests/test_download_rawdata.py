"""Task selection must never expand into an unfiltered dataset download."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from huggingface_hub.hf_api import RepoFile

from memory_bench.tools.download_rawdata import REPO_ID, download_files, plan_downloads, resolve_selection


def test_chocolate_config_selects_only_intervention_tasks(tmp_path):
    ids, destination = resolve_selection(
        "memory_bench/configs/mb_retrieve_chocolate_from_top_cabinet.yaml", local_dir=tmp_path
    )
    assert ids == [0, 22]
    assert destination == tmp_path
    _, configured = resolve_selection("memory_bench/configs/mb_retrieve_chocolate_from_top_cabinet.yaml")
    config = yaml.safe_load(Path("memory_bench/configs/mb_retrieve_chocolate_from_top_cabinet.yaml").read_text())
    assert configured == Path(config["paths"]["intervent_rawdata_root"])


@pytest.mark.parametrize("ids", [[], [-1], [10000], [True], ["../task-0000"], [1.5]])
def test_empty_or_invalid_selection_is_rejected(ids, tmp_path):
    with pytest.raises(ValueError):
        resolve_selection(task_ids=ids, local_dir=tmp_path)


def test_manual_selection_deduplicates_and_requires_destination(tmp_path):
    assert resolve_selection(task_ids=[22, "0", 22], local_dir=tmp_path)[0] == [0, 22]
    with pytest.raises(ValueError, match="--local-dir"):
        resolve_selection(task_ids=[0])


def repo_file(path):
    return RepoFile(path=path, size=100, oid="a" * 40)


def test_plan_lists_only_selected_directories_and_pins_revision():
    api = Mock()
    api.dataset_info.return_value = SimpleNamespace(sha="pinned-commit")
    api.list_repo_tree.side_effect = [
        [
            repo_file("task-0000/episode_00000020.hdf5"),
            repo_file("task-0000/episode_00000010.hdf5"),
            repo_file("task-0000/README.md"),
            repo_file("task-0001/episode_00010010.hdf5"),
        ],
        [repo_file("task-0022/episode_00220010.hdf5")],
    ]
    revision, files = plan_downloads(api, [0, 22], "main", limit_per_task=1)
    assert revision == "pinned-commit"
    assert [entry.path for entry in files] == [
        "task-0000/episode_00000010.hdf5",
        "task-0022/episode_00220010.hdf5",
    ]
    assert [call.kwargs["path_in_repo"] for call in api.list_repo_tree.call_args_list] == ["task-0000", "task-0022"]
    assert all(call.kwargs["revision"] == revision for call in api.list_repo_tree.call_args_list)


def test_missing_task_does_not_silently_download_partial_selection():
    api = Mock()
    api.dataset_info.return_value = SimpleNamespace(sha="pinned-commit")
    api.list_repo_tree.return_value = []
    with pytest.raises(ValueError, match="No episode HDF5"):
        plan_downloads(api, [0], "main")


def test_download_uses_exact_files_and_reports_failure(monkeypatch, tmp_path):
    download = Mock(side_effect=[str(tmp_path / "ready.hdf5"), OSError("interrupted")])
    monkeypatch.setattr("memory_bench.tools.download_rawdata.hf_hub_download", download)
    files = [repo_file("task-0000/episode_00000010.hdf5"), repo_file("task-0022/episode_00220010.hdf5")]
    with pytest.raises(RuntimeError, match="1 downloads failed"):
        download_files(files, "pinned-commit", tmp_path, workers=1)
    assert {call.kwargs["filename"] for call in download.call_args_list} == {entry.path for entry in files}
    for call in download.call_args_list:
        assert call.kwargs["repo_id"] == REPO_ID
        assert call.kwargs["revision"] == "pinned-commit"
        assert call.kwargs["local_dir"] == str(tmp_path)
