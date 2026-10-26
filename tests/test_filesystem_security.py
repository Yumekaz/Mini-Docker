"""Hermetic fail-closed regressions. All privileged syscalls are intercepted."""

import ast
import ctypes
import errno
import os
import stat
import struct
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from mini_docker import container as runtime
from mini_docker import filesystem as fs
from mini_docker.capabilities import CAP_MKNOD, DEFAULT_CONTAINER_CAPS
from mini_docker.metadata import ContainerConfig
from mini_docker.seccomp import (
    ABSOLUTELY_FORBIDDEN_SYSCALLS,
    ALLOWED_SYSCALLS_WHITELIST,
    Seccomp,
)


def test_privileged_case_scripts_compile_without_executing():
    tree = ast.parse(
        Path(__file__).with_name("test_linux_runtime_integration.py").read_text()
    )
    scripts = [
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        and any(
            isinstance(target, ast.Name) and target.id == "script"
            for target in node.targets
        )
    ]
    assert len(scripts) >= 3
    for script in scripts:
        compile(script, "<required-root-script>", "exec")


@pytest.fixture
def rootfs(tmp_path):
    root = tmp_path / "rootfs"
    root.mkdir()
    return root


@pytest.fixture
def syscalls(monkeypatch):
    """Keep real directory/fd semantics but forbid mount/pivot/security changes."""
    calls = []

    def record(source, target, fstype=None, flags=0, options=None):
        def resolve(path):
            return os.readlink(path) if path.startswith("/proc/self/fd/") else path

        calls.append((resolve(source), resolve(target), fstype, flags, options))
        if fstype == "proc":
            # Fake a kernel-populated procfs only inside the disposable fixture.
            proc = Path(resolve(target))
            for name in ("sysrq-trigger", "kcore", "keys", "timer_list"):
                (proc / name).write_bytes(b"fake kernel control; never a host path")

    monkeypatch.setattr(fs, "mount", mock.Mock(side_effect=record))
    monkeypatch.setattr(fs, "set_mount_attributes", mock.Mock())
    monkeypatch.setattr(fs, "pivot_root", mock.Mock())
    monkeypatch.setattr(fs, "umount", mock.Mock())
    monkeypatch.setattr(
        fs, "chroot", mock.Mock(side_effect=AssertionError("unsafe fallback"))
    )
    monkeypatch.setattr(os, "fchdir", mock.Mock())
    monkeypatch.setattr(os, "chdir", mock.Mock())
    real_rmdir = os.rmdir
    monkeypatch.setattr(
        os,
        "rmdir",
        lambda path, *a, **kw: (
            None if path == "/.pivot_old" else real_rmdir(path, *a, **kw)
        ),
    )
    return calls


def test_procfs_is_readonly_and_global_controls_have_private_inert_masks(
    rootfs, syscalls
):
    fs.setup_pivot_root(str(rootfs))
    proc = next(call for call in syscalls if call[2] == "proc")
    assert (
        proc[3] & fs.MS_RDONLY
    ), "PID namespaces do not virtualize global proc controls"
    mask = rootfs / "dev/.proc-mask"
    assert mask.is_file() and mask.read_bytes() == b""
    assert mask.stat().st_mode & 0o777 == 0o444
    for name in ("sysrq-trigger", "kcore", "keys", "timer_list"):
        assert any(
            call[0] == str(mask)
            and call[1] == str(rootfs / "proc" / name)
            and call[3] & fs.MS_BIND
            for call in syscalls
        )


def test_proc_control_mask_mount_failure_aborts_before_pivot(
    rootfs, syscalls, monkeypatch
):
    original = fs.mount.side_effect

    def fail(source, target, fstype=None, flags=0, options=None):
        if target.startswith("/proc/self/fd/") and os.readlink(target) == str(
            rootfs / "proc/sysrq-trigger"
        ):
            raise fs.FilesystemError("injected proc control mask failure")
        return original(source, target, fstype, flags, options)

    monkeypatch.setattr(fs, "mount", mock.Mock(side_effect=fail))
    with pytest.raises(fs.FilesystemError, match="proc control mask failure"):
        fs.setup_pivot_root(str(rootfs))
    fs.pivot_root.assert_not_called()
    fs.chroot.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        "valid",
        "rw-proc",
        "missing-mask",
        "rw-submount",
        "wrong-source",
        "not-empty",
        "writable-inode",
    ],
)
def test_exec_proc_guard_rejects_legacy_and_unprotected_mounts(monkeypatch, change):
    proc_flags = "rw" if change == "rw-proc" else "ro"
    text = "10 1 0:1 / /proc " + proc_flags + ",nosuid,nodev,noexec - proc proc rw\n"
    if change != "missing-mask":
        source = "/wrong" if change == "wrong-source" else "/.proc-mask"
        text += (
            "11 10 0:2 "
            + source
            + " /proc/sysrq-trigger ro,nodev,nosuid,noexec - tmpfs tmpfs rw\n"
        )
    if change == "rw-submount":
        text += "12 10 0:1 /sys /proc/sys rw - proc proc rw\n"
    monkeypatch.setattr(fs, "open", mock.mock_open(read_data=text), raising=False)

    def metadata(path):
        if path != "/proc/sysrq-trigger":
            raise FileNotFoundError(path)
        return SimpleNamespace(
            st_mode=stat.S_IFREG | (0o644 if change == "writable-inode" else 0o444),
            st_size=1 if change == "not-empty" else 0,
        )

    monkeypatch.setattr(fs.os, "lstat", metadata)
    if change == "valid":
        fs.verify_proc_policy()
    else:
        with pytest.raises(fs.FilesystemError):
            fs.verify_proc_policy()


def test_exec_proc_guard_does_not_treat_permission_failure_as_kernel_feature_absence(
    monkeypatch,
):
    text = "10 1 0:1 / /proc ro,nosuid,nodev,noexec - proc proc rw\n"
    monkeypatch.setattr(fs, "open", mock.mock_open(read_data=text), raising=False)
    monkeypatch.setattr(
        fs.os, "lstat", mock.Mock(side_effect=PermissionError("denied"))
    )
    with pytest.raises(PermissionError):
        fs.verify_proc_policy()


def test_exec_must_verify_entered_proc_policy_before_running_user_code():
    # Check the production exec hook's ordering without entering namespaces;
    # semantic policy failures are exercised above.
    tree = ast.parse(Path(runtime.__file__).read_text())
    container = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Container"
    )
    execute = next(
        node
        for node in container.body
        if isinstance(node, ast.FunctionDef) and node.name == "exec"
    )
    calls = [node for node in ast.walk(execute) if isinstance(node, ast.Call)]
    guards = [
        node.lineno
        for node in calls
        if isinstance(node.func, ast.Name) and node.func.id == "verify_proc_policy"
    ]
    roots = [
        node.lineno
        for node in calls
        if isinstance(node.func, ast.Attribute) and node.func.attr == "chroot"
    ]
    workloads = [
        node.lineno
        for node in calls
        if isinstance(node.func, ast.Attribute) and node.func.attr == "execvp"
    ]
    assert len(guards) == len(roots) == len(workloads) == 1
    assert roots[0] < guards[0] < workloads[0]


@pytest.mark.parametrize("path", ["dev", "proc", "sys", ".pivot_old"])
@pytest.mark.parametrize("kind", ["symlink", "file"])
def test_unsafe_reserved_staging_rejected_before_mount(
    rootfs, tmp_path, syscalls, path, kind
):
    outside = tmp_path / "outside"
    outside.mkdir()
    if kind == "symlink":
        (rootfs / path).symlink_to(outside, target_is_directory=True)
    else:
        (rootfs / path).write_text("not a directory")
    with pytest.raises((OSError, fs.FilesystemError)):
        fs.setup_pivot_root(str(rootfs))
    assert not syscalls
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("target", ["/../outside/newleaf", "/app/newleaf"])
def test_volume_escape_is_rejected_without_host_side_effects(
    rootfs, tmp_path, syscalls, target
):
    outside, source = tmp_path / "outside", tmp_path / "source"
    outside.mkdir()
    source.mkdir()
    (rootfs / "app").symlink_to(outside, target_is_directory=True)
    with pytest.raises((OSError, fs.FilesystemError)):
        fs.setup_pivot_root(
            str(rootfs),
            volumes=[{"host": str(source), "container": target, "mode": "rw"}],
        )
    assert not syscalls
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize(
    "target",
    ["/", "/dev", "/dev/disk", "/proc/x", "/sys/x", "/.pivot_old/x", "relative"],
)
def test_volumes_cannot_override_security_mounts(rootfs, tmp_path, syscalls, target):
    with pytest.raises(fs.FilesystemError):
        fs.setup_pivot_root(
            str(rootfs), volumes=[{"host": str(tmp_path), "container": target}]
        )
    assert not syscalls


@pytest.mark.parametrize(
    "volume",
    [
        {},
        {"host": "/tmp", "container": "/data", "mode": "anything"},
        {"host": "relative", "container": "/data"},
    ],
)
def test_invalid_volume_is_not_silently_ignored(rootfs, syscalls, volume):
    with pytest.raises(fs.FilesystemError):
        fs.setup_pivot_root(str(rootfs), volumes=[volume])
    assert not syscalls


def test_pinned_parent_cannot_be_redirected_by_symlink_replacement(tmp_path, syscalls):
    parent, moved, outside = (
        tmp_path / name for name in ("parent", "moved", "outside")
    )
    parent.mkdir()
    outside.mkdir()
    (parent / "rootfs").mkdir()
    with fs.PinnedRoot(str(parent / "rootfs")) as root:
        parent.rename(moved)
        parent.symlink_to(outside, target_is_directory=True)
        fd = root.directory("data/newleaf", create=True)
        os.close(fd)
    assert (moved / "rootfs/data/newleaf").is_dir()
    assert list(outside.iterdir()) == []


def test_lower_bind_reopen_requires_the_exact_pinned_source(tmp_path, monkeypatch):
    source, target, unrelated = (
        tmp_path / name for name in ("source", "target", "unrelated")
    )
    for directory in (source, target, unrelated):
        directory.mkdir()
    with fs.PinnedRoot(str(source)) as image, fs.PinnedRoot(str(target)) as lower:
        monkeypatch.setattr(
            lower, "directory", lambda *args, **kwargs: os.dup(image.fd)
        )
        lower.reopen(image.fd)
        assert os.fstat(lower.fd).st_ino == os.fstat(image.fd).st_ino
    with fs.PinnedRoot(str(source)) as image, fs.PinnedRoot(
        str(target)
    ) as lower, fs.PinnedRoot(str(unrelated)) as wrong:
        monkeypatch.setattr(
            lower, "directory", lambda *args, **kwargs: os.dup(wrong.fd)
        )
        with pytest.raises(fs.FilesystemError, match="root changed"):
            lower.reopen(image.fd)


def test_self_bind_reopen_does_not_accept_an_unrelated_inode(tmp_path, monkeypatch):
    target, unrelated = (tmp_path / name for name in ("target", "unrelated"))
    target.mkdir()
    unrelated.mkdir()
    with fs.PinnedRoot(str(target)) as root:
        root.reopen()
        with fs.PinnedRoot(str(unrelated)) as wrong:
            monkeypatch.setattr(
                root, "directory", lambda *args, **kwargs: os.dup(wrong.fd)
            )
            with pytest.raises(fs.FilesystemError, match="root changed"):
                root.reopen()


def test_bind_destination_cannot_be_redirected_after_it_is_pinned(
    rootfs, tmp_path, syscalls, monkeypatch
):
    source, outside = tmp_path / "source", tmp_path / "outside"
    source.mkdir()
    outside.mkdir()
    (rootfs / "data").mkdir()
    pinned = []

    def replace_after_pin(source_path, target_path, *args):
        (rootfs / "data").rename(rootfs / "saved")
        (rootfs / "data").symlink_to(outside, target_is_directory=True)
        pinned.append(os.readlink(target_path))

    monkeypatch.setattr(fs, "mount", replace_after_pin)
    with fs.PinnedRoot(str(rootfs)) as root:
        with pytest.raises(OSError):
            fs.bind_volume(root, {"host": str(source), "container": "/data"})
    assert pinned == [str(rootfs / "saved")]
    assert list(outside.iterdir()) == []


def test_private_dev_contains_only_allowlisted_binds_and_private_pts(rootfs, syscalls):
    fs.setup_pivot_root(str(rootfs))
    assert all(call[0] != "/dev" and call[2] != "devtmpfs" for call in syscalls)
    device_binds = [call for call in syscalls if call[0].startswith("/dev/")]
    assert {call[0] for call in device_binds} == {
        "/dev/null",
        "/dev/zero",
        "/dev/random",
        "/dev/urandom",
        "/dev/tty",
    }
    assert all(call[3] == fs.MS_BIND for call in device_binds)
    dev_mount = next(call for call in syscalls if call[1] == str(rootfs / "dev"))
    assert dev_mount[2] == "tmpfs"
    assert dev_mount[3] & (fs.MS_NOSUID | fs.MS_NOEXEC) == fs.MS_NOSUID | fs.MS_NOEXEC
    pts = next(call for call in syscalls if call[2] == "devpts")
    assert "newinstance" in pts[4]
    shm = next(call for call in syscalls if call[1] == str(rootfs / "dev/shm"))
    assert shm[0] == shm[2] == "tmpfs"
    assert shm[3] == fs.MS_NODEV | fs.MS_NOSUID | fs.MS_NOEXEC
    assert "size=64m" in shm[4] and "mode=1777" in shm[4]
    assert os.readlink(rootfs / "dev/ptmx") == "pts/ptmx"
    assert not (rootfs / "dev/console").exists()
    fs.set_mount_attributes.assert_any_call(mock.ANY, fs.MS_NODEV | fs.MS_NOSUID)
    protected_devices = [
        call
        for call in fs.set_mount_attributes.call_args_list
        if call.args[1] == fs.MS_RDONLY | fs.MS_NOSUID | fs.MS_NOEXEC
    ]
    assert len(protected_devices) == 6  # five device binds plus private /dev
    assert all(call.kwargs == {"recursive": False} for call in protected_devices)


def test_device_creation_is_forbidden_in_module_defaults_and_custom_whitelists():
    assert CAP_MKNOD not in DEFAULT_CONTAINER_CAPS
    for syscall in ("mknod", "mknodat"):
        assert syscall not in ALLOWED_SYSCALLS_WHITELIST
        assert syscall in ABSOLUTELY_FORBIDDEN_SYSCALLS
        assert not Seccomp().add_allowed(syscall)


@pytest.mark.parametrize("failure_number", [1, 6])
def test_readonly_device_bind_or_dev_seal_failure_aborts(
    rootfs, syscalls, monkeypatch, failure_number
):
    calls = 0

    def enforce(fd, flags, recursive=True):
        nonlocal calls
        if flags == fs.MS_RDONLY | fs.MS_NOSUID | fs.MS_NOEXEC:
            assert recursive is False
            calls += 1
            if calls == failure_number:
                raise fs.FilesystemError("readonly device enforcement denied")

    monkeypatch.setattr(fs, "set_mount_attributes", enforce)
    with pytest.raises(fs.FilesystemError, match="readonly device"):
        fs.setup_pivot_root(str(rootfs))
    fs.pivot_root.assert_not_called()


def test_hermetic_device_setup_never_changes_host_device_metadata(rootfs, syscalls):
    def metadata():
        info = os.stat("/dev/null")
        return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid

    before = metadata()
    fs.setup_pivot_root(str(rootfs))
    assert metadata() == before


def test_ro_volume_restricts_nested_mounts_as_well_as_top(rootfs, tmp_path, syscalls):
    source = tmp_path / "source"
    source.mkdir()
    fs.setup_pivot_root(
        str(rootfs), volumes=[{"host": str(source), "container": "/data", "mode": "ro"}]
    )
    fs.set_mount_attributes.assert_any_call(
        mock.ANY, fs.MS_NODEV | fs.MS_NOSUID | fs.MS_RDONLY
    )
    # default recursive=True is part of the production function contract
    assert fs.set_mount_attributes.call_args.kwargs.get("recursive", True)


@pytest.mark.parametrize(
    "operation",
    [
        "device",
        "proc",
        "sysfs",
        "devpts",
        "tmpfs",
        "attributes",
        "pivot",
        "detach",
        "rmdir",
    ],
)
def test_any_required_mount_stage_failure_aborts(
    rootfs, syscalls, monkeypatch, operation
):
    mount = fs.mount.side_effect

    def fail_mount(source, target, fstype=None, flags=0, options=None):
        if fstype == operation or (
            operation == "device"
            and source.startswith("/proc/self/fd/")
            and os.readlink(source).startswith("/dev/")
        ):
            raise fs.FilesystemError("injected " + operation)
        return mount(source, target, fstype, flags, options)

    monkeypatch.setattr(fs, "mount", mock.Mock(side_effect=fail_mount))
    selected = {
        "attributes": "set_mount_attributes",
        "pivot": "pivot_root",
        "detach": "umount",
    }
    if operation in selected:
        getattr(fs, selected[operation]).side_effect = fs.FilesystemError(
            "injected " + operation
        )
    if operation == "rmdir":
        monkeypatch.setattr(
            os, "rmdir", mock.Mock(side_effect=OSError("injected rmdir"))
        )
    with pytest.raises((OSError, fs.FilesystemError), match="injected"):
        fs.setup_pivot_root(str(rootfs))
    fs.chroot.assert_not_called()
    if operation not in ("detach", "rmdir"):
        fs.umount.assert_not_called()


def test_wrong_host_device_type_is_fatal(rootfs, tmp_path, syscalls, monkeypatch):
    fake = tmp_path / "not-a-device"
    fake.write_text("inert")
    real_open = os.open

    def redirect(path, *args, **kwargs):
        return real_open(str(fake) if path == "/dev/null" else path, *args, **kwargs)

    monkeypatch.setattr(os, "open", redirect)
    with pytest.raises(fs.FilesystemError, match="allowlisted"):
        fs.setup_pivot_root(str(rootfs))
    fs.pivot_root.assert_not_called()


def test_rootless_has_private_readonly_empty_sys_not_host_sysfs(rootfs, syscalls):
    fs.setup_pivot_root(str(rootfs), rootless=True)
    sys_mount = next(call for call in syscalls if call[1] == str(rootfs / "sys"))
    assert sys_mount[0] == sys_mount[2] == "tmpfs"
    assert sys_mount[3] & fs.MS_RDONLY
    assert all(call[2] != "sysfs" for call in syscalls)


def test_caller_aborts_before_other_mounts_if_private_propagation_fails(
    rootfs, monkeypatch
):
    manager = runtime.Container.__new__(runtime.Container)
    config = ContainerConfig(rootfs=str(rootfs), use_overlay=True)
    monkeypatch.setattr(
        runtime, "mount", mock.Mock(side_effect=fs.FilesystemError("private denied"))
    )
    for name in ("setup_overlay_filesystem", "setup_pivot_root"):
        monkeypatch.setattr(
            runtime,
            name,
            mock.Mock(side_effect=AssertionError("continued after private failure")),
        )
    with pytest.raises(runtime.ContainerInternalError, match="make mounts private"):
        manager._prepare_and_exec_workload(config, mock.Mock(), True, None)


def test_caller_never_falls_back_or_execs_after_pivot_failure(rootfs, monkeypatch):
    manager = runtime.Container.__new__(runtime.Container)
    config = ContainerConfig(rootfs=str(rootfs), use_overlay=False)
    monkeypatch.setattr(runtime, "mount", mock.Mock())
    monkeypatch.setattr(
        runtime,
        "setup_pivot_root",
        mock.Mock(side_effect=fs.FilesystemError("detach failed")),
    )
    manager._exec_workload = mock.Mock(
        side_effect=AssertionError("exec after failed detach")
    )
    with pytest.raises(fs.FilesystemError, match="detach failed"):
        manager._prepare_and_exec_workload(config, mock.Mock(), True, None)
    manager._exec_workload.assert_not_called()


def test_legacy_chroot_entry_cannot_downgrade(monkeypatch):
    pivot = mock.Mock(side_effect=fs.FilesystemError("no downgrade"))
    monkeypatch.setattr(fs, "setup_pivot_root", pivot)
    monkeypatch.setattr(fs, "chroot", mock.Mock())
    with pytest.raises(fs.FilesystemError, match="no downgrade"):
        fs.setup_chroot_filesystem("/unused")
    fs.chroot.assert_not_called()


@pytest.mark.parametrize("rootless", [False, True])
def test_start_and_exec_capability_policy_removes_current_and_bounding_mknod(
    monkeypatch, rootless
):
    manager = runtime.Container.__new__(runtime.Container)
    caps, drop = mock.Mock(), mock.Mock()
    monkeypatch.setattr(runtime, "Capabilities", mock.Mock(return_value=caps))
    monkeypatch.setattr(runtime, "_drop_device_capability", drop)
    manager._apply_capabilities(ContainerConfig(rootless=rootless), mock.Mock())
    drop.assert_called_once_with()
    if not rootless:
        caps.remove.assert_called_once_with("MKNOD")
        caps.apply.assert_called_once_with()


def test_capability_drop_is_irreversible_and_fails_closed(monkeypatch):
    monkeypatch.setattr(runtime.libc, "prctl", mock.Mock(return_value=1))
    with pytest.raises(runtime.ContainerInternalError, match="bounding"):
        runtime._drop_device_capability()
    runtime.libc.prctl.assert_any_call(24, CAP_MKNOD, 0, 0, 0)


def test_capability_drop_clears_all_sets(monkeypatch):
    monkeypatch.setattr(runtime.libc, "prctl", mock.Mock(side_effect=[1, 0]))
    monkeypatch.setattr(
        runtime,
        "get_capabilities",
        lambda: {
            "effective": {CAP_MKNOD, 1},
            "permitted": {CAP_MKNOD, 1},
            "inheritable": {CAP_MKNOD},
        },
    )
    setter = mock.Mock()
    monkeypatch.setattr(runtime, "set_capabilities", setter)
    runtime._drop_device_capability()
    setter.assert_called_once_with(effective={1}, permitted={1}, inheritable=set())


def test_default_seccomp_removes_both_device_creation_syscalls(monkeypatch):
    manager = runtime.Container.__new__(runtime.Container)
    policy = mock.Mock()
    monkeypatch.setattr(runtime, "Seccomp", mock.Mock(return_value=policy))
    monkeypatch.setattr(runtime, "set_no_new_privs", mock.Mock())
    manager._apply_seccomp(ContainerConfig(), mock.Mock())
    assert policy.remove_allowed.call_args_list == [
        mock.call("mknod"),
        mock.call("mknodat"),
    ]
    policy.apply.assert_called_once_with()


def test_mandatory_device_bpf_denies_mknod_mknodat_x32_and_wrong_abi(monkeypatch):
    capture = mock.Mock()
    monkeypatch.setattr(runtime, "install_seccomp_filter", capture)
    runtime._install_device_creation_filter()
    instructions = list(struct.iter_unpack("HBBI", capture.call_args.args[0]))

    def evaluate(number, arch=0xC000003E):
        pc, value = 0, 0
        while True:
            code, yes, no, constant = instructions[pc]
            if code == 0x20:
                value = arch if constant == 4 else number
            elif code == 0x15:
                pc += yes if value == constant else no
            elif code == 0x45:
                pc += yes if value & constant else no
            elif code == 0x06:
                return constant
            else:
                pytest.fail("unknown BPF instruction")
            pc += 1

    for number in (133, 259, 0x40000000 | 133, 0x40000000 | 259):
        assert evaluate(number) == 0x50000 | errno.EPERM
    assert evaluate(1) == 0x7FFF0000
    assert evaluate(1, arch=0x40000003) == 0x80000000


def test_mount_setattr_is_recursive_and_missing_enforcement_is_fatal(monkeypatch):
    monkeypatch.setattr(fs.platform, "machine", lambda: "x86_64")
    syscall = mock.Mock(return_value=0)
    monkeypatch.setattr(fs.libc, "syscall", syscall)
    fs.set_mount_attributes(55, fs.MS_RDONLY | fs.MS_NODEV | fs.MS_NOSUID)
    args = syscall.call_args.args
    assert args[:2] == (442, 55)
    assert args[3] == 0x1000 | 0x8000
    assert ctypes.cast(args[4], ctypes.POINTER(ctypes.c_uint64))[0] == 7
    syscall.return_value = -1
    with pytest.raises(fs.FilesystemError, match="5.12"):
        fs.set_mount_attributes(55, fs.MS_NODEV)
