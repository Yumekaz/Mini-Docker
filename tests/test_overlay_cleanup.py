"""Overlay cleanup acknowledgement, using only private files and fake umount2."""

import builtins
import ctypes
import errno
import io
from types import SimpleNamespace

import pytest

from mini_docker import filesystem as fs
from mini_docker import metadata
from mini_docker.container import Container, ContainerInternalError


@pytest.fixture
def overlay(monkeypatch, tmp_path):
    base = tmp_path / "owned overlay"
    paths = [base / name for name in ("lower", "upper", "work", "merged")]
    for path in paths:
        path.mkdir(parents=True)
    for name in ("dev/pts", "sys", "proc"):
        (paths[3] / name).mkdir(parents=True)
    state = SimpleNamespace(
        base=base,
        paths=paths,
        points=[
            paths[3] / "dev/pts",
            paths[3] / "dev",
            paths[3] / "sys",
            paths[3] / "proc",
            paths[3],
            paths[0],
        ],
        calls=[],
        failures={},
        mounted=set(),
        mountinfo_error=None,
        mountinfo_override=None,
        rmtree_error=None,
        removals=[],
    )

    def umount2(target, flags):
        target = target.decode()
        state.calls.append((target, flags))
        assert target in [str(point) for point in state.points]
        assert flags == fs.MNT_DETACH, "do not broaden the existing teardown policy"
        code = state.failures.get(target, 0)
        ctypes.set_errno(code)
        return -1 if code else 0

    real_open = builtins.open

    def open_mountinfo(path, *args, **kwargs):
        if path != "/proc/self/mountinfo":
            return real_open(path, *args, **kwargs)
        if state.mountinfo_error:
            raise state.mountinfo_error
        if state.mountinfo_override is not None:
            return io.StringIO(state.mountinfo_override)
        lines = ["1 0 0:1 / / rw - tmpfs rootfs rw\n"]
        for point in state.mounted:
            escaped = str(point)
            for char, code in (
                ("\\", "134"),
                (" ", "040"),
                ("\t", "011"),
                ("\n", "012"),
            ):
                escaped = escaped.replace(char, "\\" + code)
            lines.append("20 1 0:1 / " + escaped + " rw - tmpfs tmpfs rw\n")
        return io.StringIO("".join(lines))

    real_rmtree = fs.shutil.rmtree

    def rmtree(path, *args, **kwargs):
        if str(path) != str(state.base):
            # Backend acknowledgement test also deletes its private metadata.
            assert str(path) == str(tmp_path / "metadata/owned")
            return real_rmtree(path, *args, **kwargs)
        state.removals.append(str(path))
        if state.rmtree_error:
            raise state.rmtree_error
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(fs, "get_overlay_paths", lambda cid: tuple(map(str, paths)))
    monkeypatch.setattr(fs.libc, "umount2", umount2)
    monkeypatch.setattr(builtins, "open", open_mountinfo)
    monkeypatch.setattr(fs.shutil, "rmtree", rmtree)
    yield state
    ctypes.set_errno(0)


@pytest.mark.parametrize("code", [errno.EBUSY, errno.EPERM, errno.EIO])
def test_unmount_failure_is_visible_and_never_recurses_into_overlay(overlay, code):
    target = str(overlay.points[0])
    overlay.failures[target] = code
    with pytest.raises(fs.FilesystemError, match="errno " + str(code)):
        fs.cleanup_overlay("owned")
    assert overlay.base.is_dir()
    assert not overlay.removals
    assert overlay.calls == [(target, fs.MNT_DETACH)]
    overlay.failures.clear()
    fs.cleanup_overlay("owned")
    assert not overlay.base.exists()


@pytest.mark.parametrize("code", [errno.ENOENT, errno.EINVAL])
def test_missing_or_confirmed_nonmount_targets_are_idempotent(overlay, code):
    overlay.failures = {str(point): code for point in overlay.points}
    fs.cleanup_overlay("owned")
    assert not overlay.base.exists()
    fs.cleanup_overlay("owned")
    assert not overlay.base.exists()


@pytest.mark.parametrize("code", [errno.ENOENT, errno.EINVAL])
def test_lookup_or_einval_for_a_still_mounted_target_is_not_success(overlay, code):
    target = overlay.points[0]
    overlay.failures[str(target)] = code
    overlay.mounted.add(target)
    with pytest.raises(fs.FilesystemError, match="errno " + str(code)):
        fs.cleanup_overlay("owned")
    assert overlay.base.is_dir() and not overlay.removals


@pytest.mark.parametrize("problem", ["denied", "malformed", "empty"])
def test_nonmount_classification_must_be_verified_before_ignoring_einval(
    overlay, problem
):
    overlay.failures[str(overlay.points[0])] = errno.EINVAL
    if problem == "denied":
        overlay.mountinfo_error = PermissionError(
            errno.EACCES, "fixture mountinfo denied"
        )
    elif problem == "malformed":
        overlay.mountinfo_override = "not a mountinfo record\n"
    else:
        overlay.mountinfo_override = ""
    with pytest.raises(fs.FilesystemError):
        fs.cleanup_overlay("owned")
    assert overlay.base.is_dir() and not overlay.removals


@pytest.mark.parametrize("code", [errno.EACCES, errno.EPERM, errno.EBUSY, errno.EIO])
def test_directory_removal_failure_is_visible_and_retryable(overlay, code):
    overlay.rmtree_error = OSError(code, "fixture rmtree failed", str(overlay.base))
    with pytest.raises(fs.FilesystemError, match="fixture rmtree failed"):
        fs.cleanup_overlay("owned")
    assert overlay.base.is_dir()
    overlay.rmtree_error = None
    fs.cleanup_overlay("owned")
    assert not overlay.base.exists()


def test_nested_enoent_is_not_mistaken_for_absent_overlay(overlay):
    overlay.rmtree_error = FileNotFoundError(
        errno.ENOENT, "fixture nested removal failed", str(overlay.paths[1] / "child")
    )
    with pytest.raises(fs.FilesystemError, match="fixture nested removal failed"):
        fs.cleanup_overlay("owned")
    assert overlay.base.is_dir()


def test_absent_overlay_base_is_success(overlay):
    fs.cleanup_overlay("owned")
    fs.cleanup_overlay("owned")
    assert not overlay.base.exists()


def test_unclassified_unmount_error_cannot_inherit_a_benign_stale_errno(
    overlay, monkeypatch
):
    def fail(*args):
        raise fs.FilesystemError("fixture unclassified unmount failure")

    monkeypatch.setattr(fs, "umount", fail)
    ctypes.set_errno(errno.EINVAL)
    with pytest.raises(fs.FilesystemError, match="unclassified"):
        fs.cleanup_overlay("owned")
    assert overlay.base.is_dir() and not overlay.removals


def test_remove_ack_retains_metadata_on_real_overlay_error_then_retries(
    overlay, monkeypatch, tmp_path
):
    root = tmp_path / "metadata"
    root.mkdir()
    monkeypatch.setattr(metadata, "CONTAINERS_PATH", str(root))
    monkeypatch.setattr(metadata, "get_container_path", lambda cid: str(root / cid))
    monkeypatch.setattr(metadata, "ensure_directories", lambda: None)
    config = metadata.ContainerConfig(
        id="owned",
        name="owned",
        status="stopped",
        rootless=True,
        finished_at=123.5,
        exit_code=7,
    )
    metadata.save_container_config(config)
    manager = Container.__new__(Container)
    overlay.failures[str(overlay.points[0])] = errno.EBUSY
    with pytest.raises(ContainerInternalError, match="Overlay cleanup"):
        manager.remove(config.id)
    retained = metadata.load_container_config(config.id)
    assert retained.status == "stopped"
    assert retained.finished_at == 123.5 and retained.exit_code == 7
    assert overlay.base.is_dir() and not overlay.removals
    overlay.failures.clear()
    assert manager.remove(config.id) is True
    assert metadata.load_container_config(config.id) is None
    assert not overlay.base.exists()
