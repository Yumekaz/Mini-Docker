"""Deterministic ordinary-file races; no processes, namespaces or live runtime."""

import copy
import fcntl
import json
import os
from dataclasses import asdict

import pytest

from mini_docker import metadata
from mini_docker.container import _write_config_to_fd


@pytest.fixture
def stored(monkeypatch, tmp_path):
    root = tmp_path / "containers"
    root.mkdir()
    monkeypatch.setattr(metadata, "CONTAINERS_PATH", str(root))
    monkeypatch.setattr(metadata, "get_container_path", lambda cid: str(root / cid))
    monkeypatch.setattr(metadata, "ensure_directories", lambda: None)
    config = metadata.ContainerConfig(
        id="refresh-owned",
        name="refresh-owned",
        status="running",
        pid=42003,
        supervisor_pid=42002,
        pid_identity={"boot_id": "fixture-boot", "start_ticks": 3},
        supervisor_identity={"boot_id": "fixture-boot", "start_ticks": 2},
        started_at=10.0,
        exec_ready=True,
    )
    path = metadata.save_container_config(config)
    descriptor = os.open(path, os.O_WRONLY | os.O_CLOEXEC)
    try:
        yield config, path, descriptor
    finally:
        os.close(descriptor)


def terminal(config, code):
    result = copy.deepcopy(config)
    result.status = "stopped"
    result.pid = None
    result.exec_ready = False
    result.finished_at = 12.5
    result.exit_code = code
    return result


def dead_process(pid):
    raise ProcessLookupError(pid)


@pytest.mark.parametrize("code", [0, 7, 137])
def test_supervisor_terminal_publish_during_stale_refresh_preserves_exact_exit(
    monkeypatch, stored, code
):
    config, path, descriptor = stored
    completed = terminal(config, code)
    published = []

    def publish_then_exit(pid):
        if not published:
            _write_config_to_fd(descriptor, completed)
            published.append(True)
        raise ProcessLookupError(pid)

    monkeypatch.setattr(metadata, "capture_identity", publish_then_exit)
    loaded = metadata.load_container_config(config.id)
    assert published == [True]
    assert loaded.status == "stopped"
    assert loaded.exit_code == code
    assert loaded.finished_at == 12.5
    assert asdict(loaded) == asdict(completed)
    with open(path) as stream:
        assert json.load(stream) == asdict(completed)


def test_publish_after_liveness_checks_before_write_is_not_lost(monkeypatch, stored):
    config, path, descriptor = stored
    completed = terminal(config, 0)
    original_open = os.open
    published = []

    def publish_at_write_open(target, flags, *args, **kwargs):
        if target == path and flags & os.O_RDWR and not published:
            _write_config_to_fd(descriptor, completed)
            published.append(True)
        return original_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(metadata, "capture_identity", dead_process)
    monkeypatch.setattr(metadata.os, "open", publish_at_write_open)
    loaded = metadata.load_container_config(config.id)
    assert published == [True]
    assert asdict(loaded) == asdict(completed)
    with open(path) as stream:
        assert json.load(stream) == asdict(completed)


@pytest.mark.parametrize("reuse_pid", [False, True])
def test_stale_refresh_cannot_overwrite_a_new_running_generation(
    monkeypatch, stored, reuse_pid
):
    config, path, descriptor = stored
    current = copy.deepcopy(config)
    if not reuse_pid:
        current.pid = 43003
        current.supervisor_pid = 43002
    current.pid_identity["start_ticks"] = 103
    current.supervisor_identity["start_ticks"] = 102
    current.started_at = 20.0
    current.env = {"GENERATION": "new"}
    published = []

    def publish_new_generation(pid):
        if not published:
            _write_config_to_fd(descriptor, current)
            published.append(True)
        raise ProcessLookupError(pid)

    monkeypatch.setattr(metadata, "capture_identity", publish_new_generation)
    loaded = metadata.load_container_config(config.id)
    assert asdict(loaded) == asdict(current)
    with open(path) as stream:
        assert json.load(stream) == asdict(current)


def test_dead_refresh_does_not_invent_a_success_exit_code(monkeypatch, stored):
    config, path, _ = stored
    monkeypatch.setattr(metadata, "capture_identity", dead_process)
    loaded = metadata.load_container_config(config.id)
    assert loaded.status == "stopped"
    assert loaded.exit_code is None
    assert loaded.pid is None and loaded.supervisor_pid is None
    assert loaded.pid_identity is None and loaded.supervisor_identity is None
    assert loaded.exec_ready is False
    with open(path) as stream:
        assert json.load(stream) == asdict(loaded)


def test_compare_reload_holds_exclusive_lock_until_commit(monkeypatch, stored):
    config, _, writer = stored
    original_read = os.read
    protected_reads = []

    def read_under_lock(descriptor, size):
        if fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDWR:
            with pytest.raises(BlockingIOError):
                fcntl.flock(writer, fcntl.LOCK_SH | fcntl.LOCK_NB)
            protected_reads.append(True)
        return original_read(descriptor, size)

    monkeypatch.setattr(metadata, "capture_identity", dead_process)
    monkeypatch.setattr(metadata.os, "read", read_under_lock)
    assert metadata.load_container_config(config.id).status == "stopped"
    assert protected_reads, "refresh must re-read under the same exclusive write lock"
    fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
    fcntl.flock(writer, fcntl.LOCK_UN)


def test_refresh_does_not_recreate_deleted_metadata(monkeypatch, stored):
    config, path, _ = stored
    removed = []

    def remove_then_exit(pid):
        if not removed:
            os.unlink(path)
            removed.append(True)
        raise ProcessLookupError(pid)

    monkeypatch.setattr(metadata, "capture_identity", remove_then_exit)
    assert metadata.load_container_config(config.id) is None
    assert not os.path.exists(path)


def test_corrupt_concurrent_reload_is_not_replaced_with_stale_state(
    monkeypatch, stored
):
    config, path, descriptor = stored
    published = []

    def corrupt_then_exit(pid):
        if not published:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                os.ftruncate(descriptor, 0)
                os.write(descriptor, b"{")
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            published.append(True)
        raise ProcessLookupError(pid)

    monkeypatch.setattr(metadata, "capture_identity", corrupt_then_exit)
    assert metadata.load_container_config(config.id) is None
    with open(path, "rb") as stream:
        assert stream.read() == b"{"
    # An invalid reload must still release the CAS file lock and descriptor.
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    fcntl.flock(descriptor, fcntl.LOCK_UN)


@pytest.mark.parametrize("uncertain", [False, True])
def test_live_or_uncertain_identity_keeps_metadata_and_authority(
    monkeypatch, stored, uncertain
):
    config, path, _ = stored
    original = asdict(config)

    def identity(pid):
        if uncertain:
            raise metadata.ProcessIdentityError("fixture boot identity unavailable")
        return (
            config.supervisor_identity
            if pid == config.supervisor_pid
            else config.pid_identity
        )

    monkeypatch.setattr(metadata, "capture_identity", identity)
    monkeypatch.setattr(metadata, "is_process_alive", lambda pid: True)
    loaded = metadata.load_container_config(config.id)
    assert loaded.status == ("unknown" if uncertain else "running")
    assert loaded.pid_identity == config.pid_identity
    assert loaded.supervisor_identity == config.supervisor_identity
    assert loaded.exit_code is None
    with open(path) as stream:
        assert json.load(stream) == original
