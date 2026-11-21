from pathlib import Path
from types import SimpleNamespace

import pytest

from memory_bench.scheduling import resources


@pytest.fixture
def host(monkeypatch):
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    monkeypatch.setattr(resources.os, "sched_getaffinity", lambda pid: set(range(8)))
    monkeypatch.setattr(resources, "_cgroup_directories", lambda: [])
    original_read = Path.read_text
    monkeypatch.setattr(
        Path, "read_text",
        lambda path, *args, **kwargs: "MemAvailable: 33554432 kB\n" if str(path) == "/proc/meminfo"
        else original_read(path, *args, **kwargs),
    )
    monkeypatch.setattr(
        resources.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="0, 24576, GPU-aaaa\n4, 12288, GPU-bbbb\n"),
    )


@pytest.mark.parametrize(
    "visible, expected",
    [(None, {0: 24, 4: 12}), ("4", {4: 12}), ("GPU-aaa", {0: 24}),
     ("", {}), ("-1", {}), ("4,-1", {4: 12})],
)
def test_gpu_visibility_and_physical_indices(host, monkeypatch, visible, expected):
    if visible is not None:
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", visible)
    snapshot = resources.resource_snapshot()
    assert snapshot.gpus == expected
    assert snapshot.ram_gib == 32
    assert snapshot.cpus == 8


def test_unavailable_gpu_has_actionable_error(host, monkeypatch):
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(resources.subprocess, "run", unavailable)
    with pytest.raises(RuntimeError, match="nvidia-smi.*NVIDIA driver"):
        resources.resource_snapshot()
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    assert resources.resource_snapshot().gpus == {}


@pytest.mark.parametrize("visible", ["9", "MIG-foo", "GPU-z", "garbage"])
def test_invalid_gpu_visibility_rejected(host, monkeypatch, visible):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", visible)
    with pytest.raises(ValueError, match="CUDA_VISIBLE_DEVICES"):
        resources.resource_snapshot()


@pytest.mark.parametrize("version", [1, 2])
def test_cgroup_limits_and_affinity_are_respected(host, monkeypatch, tmp_path, version):
    child = tmp_path / "worker"
    child.mkdir()
    if version == 2:
        files = {"memory.max": str(20 * 1024**3), "memory.current": str(8 * 1024**3), "cpu.max": "250000 100000"}
        directories = [("", child), ("", tmp_path)]
        (tmp_path / "memory.max").write_text(str(10 * 1024**3))
        (tmp_path / "memory.current").write_text(str(6 * 1024**3))
        (tmp_path / "cpu.max").write_text("max 100000")
    else:
        files = {
            "memory.limit_in_bytes": str(20 * 1024**3), "memory.usage_in_bytes": str(8 * 1024**3),
            "cpu.cfs_quota_us": "250000", "cpu.cfs_period_us": "100000",
        }
        directories = [("memory", child), ("cpu", child)]
    for filename, contents in files.items():
        (child / filename).write_text(contents)
    monkeypatch.setattr(resources, "_cgroup_directories", lambda: directories)
    snapshot = resources.resource_snapshot()
    assert snapshot.ram_gib == (4 if version == 2 else 12)
    assert snapshot.cpus == 2.5


def test_worker_environment_isolated_and_binds_gpu(tmp_path, monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,4")
    first = resources.worker_environment(4, tmp_path)
    second = resources.worker_environment(0, tmp_path)
    assert first["CUDA_VISIBLE_DEVICES"] == "4"
    assert first["CUDA_DEVICE_ORDER"] == "PCI_BUS_ID"
    assert first["OMNIGIBSON_GPU_ID"] == "4"
    assert first["OMNIGIBSON_PHYSICS_GPU_ID"] == "0"
    assert first["OMNIGIBSON_HEADLESS"] == "1"
    assert first["TMPDIR"] == first["TMP"] == first["TEMP"]
    for variable in (
        "OMNIGIBSON_APPDATA_PATH", "TMPDIR", "XDG_CACHE_HOME", "CUDA_CACHE_PATH",
        "TORCH_EXTENSIONS_DIR", "TRITON_CACHE_DIR", "NUMBA_CACHE_DIR",
    ):
        assert first[variable] != second[variable]
        assert Path(first[variable]).is_dir()
        assert Path(first[variable]).is_relative_to(tmp_path)
    assert resources.os.environ["CUDA_VISIBLE_DEVICES"] == "0,4"
