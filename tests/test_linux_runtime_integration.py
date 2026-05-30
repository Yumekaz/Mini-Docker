import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="Mini-Docker runtime integration requires Linux"
)


def _require_root():
    if os.geteuid() != 0:
        pytest.skip("runtime integration requires root")


def _require_cgroups_v2():
    if not os.path.exists("/sys/fs/cgroup/cgroup.controllers"):
        pytest.skip("runtime integration requires cgroups v2")


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _rootfs_path():
    rootfs = os.path.join(_repo_root(), "rootfs")
    if not os.path.exists(os.path.join(rootfs, "bin", "sh")):
        pytest.skip("rootfs/bin/sh is required; run scripts/setup.sh first")
    return rootfs


def _require_rootfs_binary(rootfs, binary):
    if not os.path.exists(os.path.join(rootfs, "bin", binary)):
        pytest.skip(f"rootfs/bin/{binary} is required for this integration test")


def _runtime_env(tmp_path):
    env = os.environ.copy()
    env["MINI_DOCKER_ROOT"] = str(tmp_path / "state")
    env["MINI_DOCKER_RUN"] = str(tmp_path / "run")
    return env


def test_pid_namespace_workload_runs_as_pid_one(tmp_path):
    _require_root()
    _require_cgroups_v2()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mini_docker",
            "run",
            "--no-overlay",
            _rootfs_path(),
            "/bin/sh",
            "-c",
            "echo $$",
        ],
        cwd=_repo_root(),
        env=_runtime_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "1" in result.stdout.splitlines()


def test_memory_limit_cgroup_is_enforced(tmp_path):
    _require_root()
    _require_cgroups_v2()
    rootfs = _rootfs_path()
    _require_rootfs_binary(rootfs, "python3")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mini_docker",
            "run",
            "--no-overlay",
            "--memory",
            "32M",
            rootfs,
            "/bin/sh",
            "-c",
            "python3 - <<'PY'\nblocks=[]\nwhile True:\n    blocks.append(bytearray(1024 * 1024))\nPY",
        ],
        cwd=_repo_root(),
        env=_runtime_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
