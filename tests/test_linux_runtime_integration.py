import os
import re
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


def _require_private_mount_namespace():
    if not shutil.which("unshare"):
        pytest.skip("runtime integration requires unshare for private mount cleanup")


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _rootfs_path():
    rootfs = os.path.join(_repo_root(), "rootfs")
    if not os.path.exists(os.path.join(rootfs, "bin", "sh")):
        pytest.skip("rootfs/bin/sh is required; run scripts/setup.sh first")
    return rootfs


def _copy_rootfs(tmp_path):
    rootfs = tmp_path / "rootfs"
    # The bundled rootfs contains character devices under dev/. copytree would
    # read /dev/random and /dev/urandom as ordinary files, filling tmp_path
    # indefinitely. The runtime creates a minimal disposable /dev itself.
    shutil.copytree(
        _rootfs_path(),
        rootfs,
        symlinks=True,
        ignore=shutil.ignore_patterns("dev"),
    )
    (rootfs / "dev").mkdir()
    return str(rootfs)


def _require_rootfs_binary(rootfs, binary):
    if not os.path.exists(os.path.join(rootfs, "bin", binary)):
        pytest.skip(f"rootfs/bin/{binary} is required for this integration test")


def _install_python_memory_hog(rootfs):
    """Install the host Python runtime into the disposable copied rootfs.

    BusyBox has no allocator workload that reliably exercises cgroup memory
    enforcement. This fixture is isolated to the test copy and never changes
    the bundled rootfs.
    """
    executable = os.path.realpath(sys.executable)
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    stdlib = os.path.join(sys.base_prefix, "lib", version)
    if not os.path.isfile(executable) or not os.path.isdir(stdlib):
        pytest.skip("host Python runtime is unavailable for memory-limit test")

    def copy_absolute(source):
        target = os.path.join(rootfs, source.lstrip("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=True)

    copy_absolute(executable)
    shutil.copytree(stdlib, os.path.join(rootfs, "usr", "lib", version), symlinks=True)
    ldd = subprocess.run(
        ["ldd", executable], capture_output=True, text=True, check=False
    )
    if ldd.returncode != 0:
        pytest.skip("ldd is unavailable for memory-limit test")
    libraries = re.findall(r"(?:=>\s+)?(/\S+)", ldd.stdout)
    for library in libraries:
        if os.path.isfile(library):
            copy_absolute(library)
    return executable


def _runtime_env(tmp_path):
    env = os.environ.copy()
    env["MINI_DOCKER_ROOT"] = str(tmp_path / "state")
    env["MINI_DOCKER_RUN"] = str(tmp_path / "run")
    return env


def _run_root_container(tmp_path, args, **kwargs):
    """Run each privileged case inside an expendable mount namespace.

    A runtime regression must not leave proc/sys/dev mounts under pytest's
    copied rootfs. The namespace disappears when the command exits, matching
    the guarded CI root-smoke workflow.
    """
    _require_private_mount_namespace()
    return subprocess.run(
        [
            "unshare",
            "--mount",
            "--pid",
            "--fork",
            "--kill-child",
            "--mount-proc",
            "--propagation",
            "private",
            "--",
            *args,
        ],
        cwd=_repo_root(),
        env=_runtime_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
        **kwargs,
    )


def test_pid_namespace_workload_runs_as_pid_one(tmp_path):
    _require_root()
    _require_cgroups_v2()
    rootfs = _copy_rootfs(tmp_path)

    result = _run_root_container(
        tmp_path,
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
    )

    assert result.returncode == 0, result.stderr
    assert "1" in result.stdout.splitlines()


def test_memory_limit_cgroup_is_enforced(tmp_path):
    _require_root()
    _require_cgroups_v2()
    rootfs = _copy_rootfs(tmp_path)
    python = _install_python_memory_hog(rootfs)

    result = _run_root_container(
        tmp_path,
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
            f"PYTHONHOME=/usr {python} -S -c 'blocks=[]\nwhile True: blocks.append(bytearray(1024 * 1024))'",
        ],
    )

    assert result.returncode != 0


def test_rw_volume_roundtrip_between_host_and_container(tmp_path):
    _require_root()
    _require_cgroups_v2()
    rootfs = _copy_rootfs(tmp_path)

    host_dir = tmp_path / "hostvol"
    host_dir.mkdir()
    (host_dir / "seed.txt").write_text("from-host")

    result = _run_root_container(
        tmp_path,
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
    write_attempt = _run_root_container(
        tmp_path,
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
    )
    assert (
        write_attempt.returncode != 0
    ), "write to ro volume succeeded — read-only remount is not enforced"

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

    result = _run_root_container(
        tmp_path,
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
    )
    assert (
        result.returncode != 0
    ), "container started despite a failed volume mount (fail-open behavior)"
    assert "should-not-run" not in result.stdout
