import os
import shutil
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


def _copy_rootfs(tmp_path):
    rootfs = tmp_path / "rootfs"
    shutil.copytree(_rootfs_path(), rootfs, symlinks=True)
    return str(rootfs)


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
    rootfs = _copy_rootfs(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mini_docker",
            "run",
            "--no-overlay",
            rootfs,
            "--",
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
    rootfs = _copy_rootfs(tmp_path)
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
            "--",
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


def test_rw_volume_roundtrip_between_host_and_container(tmp_path):
    _require_root()
    _require_cgroups_v2()
    rootfs = _copy_rootfs(tmp_path)

    host_dir = tmp_path / "hostvol"
    host_dir.mkdir()
    (host_dir / "seed.txt").write_text("from-host")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mini_docker",
            "run",
            "--no-overlay",
            "--volume",
            f"{host_dir}:/data:rw",
            rootfs,
            "--",
            "/bin/sh",
            "-c",
            "cat /data/seed.txt && echo from-container > /data/reply.txt",
        ],
        cwd=_repo_root(),
        env=_runtime_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "from-host" in result.stdout
    reply = host_dir / "reply.txt"
    assert reply.exists(), "container write to rw volume did not reach the host"
    assert reply.read_text().strip() == "from-container"


def test_ro_volume_rejects_writes(tmp_path):
    _require_root()
    _require_cgroups_v2()
    rootfs = _copy_rootfs(tmp_path)

    host_dir = tmp_path / "hostvol-ro"
    host_dir.mkdir()
    (host_dir / "keep.txt").write_text("immutable")

    # Write attempt must fail inside the container...
    write_attempt = subprocess.run(
        [
            sys.executable,
            "-m",
            "mini_docker",
            "run",
            "--no-overlay",
            "--volume",
            f"{host_dir}:/data:ro",
            rootfs,
            "--",
            "/bin/sh",
            "-c",
            "echo tampered > /data/keep.txt",
        ],
        cwd=_repo_root(),
        env=_runtime_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert write_attempt.returncode != 0, (
        "write to ro volume succeeded — read-only remount is not enforced"
    )

    # ...and the file content must be untouched on the host.
    assert (host_dir / "keep.txt").read_text() == "immutable"


def test_failed_volume_mount_fails_container_start(tmp_path):
    _require_root()
    _require_cgroups_v2()
    rootfs = _copy_rootfs(tmp_path)

    # A bind source that cannot be created/mounted must abort startup instead
    # of silently starting the container without its declared volume.
    bad_host = tmp_path / "not-a-dir" / "file"  # parent is a regular file
    (tmp_path / "not-a-dir").write_text("blocker")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mini_docker",
            "run",
            "--no-overlay",
            "--volume",
            f"{bad_host}:/data:ro",
            rootfs,
            "--",
            "/bin/sh",
            "-c",
            "echo should-not-run",
        ],
        cwd=_repo_root(),
        env=_runtime_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0, (
        "container started despite a failed volume mount (fail-open behavior)"
    )
    assert "should-not-run" not in result.stdout
