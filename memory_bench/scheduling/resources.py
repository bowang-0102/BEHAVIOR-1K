"""Local Linux resource discovery and isolated simulation worker environments."""

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ResourceSnapshot:
    gpus: dict[int, float]
    ram_gib: float
    cpus: float


def _cgroup_directories():
    """Yield controller directories from the current process up to each mount root."""
    memberships = {}
    for line in Path("/proc/self/cgroup").read_text().splitlines():
        _, controllers, relative = line.split(":", 2)
        for controller in controllers.split(","):
            memberships[controller] = relative
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        mount, filesystem = line.split(" - ", 1)
        fields = mount.split()
        fs_fields = filesystem.split()
        if fs_fields[0] not in {"cgroup", "cgroup2"}:
            continue
        controllers = [""] if fs_fields[0] == "cgroup2" else fs_fields[2].split(",")
        for controller in controllers:
            if controller not in memberships:
                continue
            root = Path(fields[3])
            membership = Path(memberships[controller])
            if not membership.is_relative_to(root):
                continue
            mountpoint = Path(fields[4])
            directory = mountpoint / membership.relative_to(root)
            while directory.is_relative_to(mountpoint):
                yield controller, directory
                if directory == mountpoint:
                    break
                directory = directory.parent


def resource_snapshot() -> ResourceSnapshot:
    """Return free physical GPU/RAM GiB and the process's effective CPU capacity.

    GPU keys are physical NVIDIA indices, filtered by CUDA_VISIBLE_DEVICES. GPU
    UUIDs (including unambiguous prefixes) are supported; MIG devices are not.
    Resource values are observations, so callers must reserve for pending starts.
    """
    visible = os.environ.get("CUDA_VISIBLE_DEVICES")
    gpus = {}
    if visible is None or visible.strip() not in {"", "-1"}:
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=index,memory.free,uuid", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, check=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError(
                "Cannot query NVIDIA GPUs. Check that nvidia-smi is installed and the NVIDIA driver is working."
            ) from error
        devices = {}
        for line in result.stdout.splitlines():
            index, free_mib, uuid = (field.strip() for field in line.split(","))
            devices[int(index)] = (float(free_mib) / 1024, uuid)
        if visible is None:
            gpus = {index: data[0] for index, data in devices.items()}
        else:
            for token in visible.split(","):
                token = token.strip()
                if token == "-1":
                    break
                if token.startswith("GPU-"):
                    matches = [index for index, (_, uuid) in devices.items() if uuid.startswith(token)]
                else:
                    matches = [int(token)] if token.isdecimal() else []
                if len(matches) != 1 or matches[0] not in devices:
                    raise ValueError(f"CUDA_VISIBLE_DEVICES contains an unavailable or unsupported GPU: {token!r}")
                gpus[matches[0]] = devices[matches[0]][0]
        if not devices:
            raise RuntimeError("No NVIDIA GPUs found. Check nvidia-smi and the NVIDIA driver.")

    memory = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    ram_bytes = int(memory["MemAvailable"].split()[0]) * 1024
    cpus = float(len(os.sched_getaffinity(0)))
    for controller, directory in _cgroup_directories():
        limit_path = directory / ("memory.max" if controller == "" else "memory.limit_in_bytes")
        usage_path = directory / ("memory.current" if controller == "" else "memory.usage_in_bytes")
        if controller in {"", "memory"} and limit_path.exists() and usage_path.exists():
            limit = limit_path.read_text().strip()
            if limit != "max":
                ram_bytes = min(ram_bytes, max(0, int(limit) - int(usage_path.read_text())))
        if controller == "" and (directory / "cpu.max").exists():
            quota, period = (directory / "cpu.max").read_text().split()
            if quota != "max":
                cpus = min(cpus, int(quota) / int(period))
        elif controller == "cpu" and (directory / "cpu.cfs_quota_us").exists():
            quota = int((directory / "cpu.cfs_quota_us").read_text())
            if quota > 0:
                cpus = min(cpus, quota / int((directory / "cpu.cfs_period_us").read_text()))
    return ResourceSnapshot(gpus=gpus, ram_gib=ram_bytes / 1024**3, cpus=cpus)


def worker_environment(gpu: int, runtime_root: Path) -> dict[str, str]:
    """Create isolated worker caches/scratch; the caller removes runtime_root.

    Uses the same application/cache environment names as runtime_cache.sh, with
    private cache directories to avoid concurrent Kit and compiler cache writes.
    The returned environment is suitable for subprocess.Popen(env=...).
    """
    runtime_root = Path(runtime_root).resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    worker_dir = Path(tempfile.mkdtemp(prefix="worker_", dir=runtime_root))
    environment = os.environ.copy()
    for variable, relative in {
        "OMNIGIBSON_APPDATA_PATH": "appdata",
        "TMPDIR": "tmp", "TMP": "tmp", "TEMP": "tmp",
        "XDG_CACHE_HOME": "xdg", "CUDA_CACHE_PATH": "cuda",
        "TORCH_EXTENSIONS_DIR": "torch", "TRITON_CACHE_DIR": "triton", "NUMBA_CACHE_DIR": "numba",
    }.items():
        path = worker_dir / relative
        path.mkdir(parents=True, exist_ok=True)
        environment[variable] = str(path)
    environment.update(
        CUDA_DEVICE_ORDER="PCI_BUS_ID", CUDA_VISIBLE_DEVICES=str(gpu),
        OMNIGIBSON_GPU_ID=str(gpu), OMNIGIBSON_PHYSICS_GPU_ID="0",
        OMNIGIBSON_HEADLESS="1", PYTHONBREAKPOINT="0", PYTHONUNBUFFERED="1",
    )
    return environment
