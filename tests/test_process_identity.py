import os
import signal
import subprocess
import sys

import pytest

from mini_docker import metadata
from mini_docker.container import Container, ContainerError
from mini_docker.metadata import ContainerConfig


def test_stop_refuses_unverified_legacy_pid_without_signalling(monkeypatch):
    config = ContainerConfig(status="running", pid=os.getpid())
    monkeypatch.setattr("mini_docker.container.load_container_config", lambda _: config)
    sent = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))
    manager = Container.__new__(Container)
    monkeypatch.setattr(manager, "_finalize_stopped_container", lambda *a, **k: True)
    with pytest.raises(ContainerError, match="identity"):
        manager.stop(config.id, timeout=0)
    assert not any(
        sig for _, sig in sent
    ), "legacy numeric PID must never authorize a signal"


def test_legacy_live_pid_is_unknown_not_a_running_container(monkeypatch):
    config = ContainerConfig(
        status="running", pid=os.getpid(), supervisor_pid=os.getpid()
    )
    monkeypatch.setattr(metadata, "save_container_config", lambda _: None)
    assert metadata._refresh_container_state(config).status == "unknown"


def test_restart_refuses_unverified_state_before_starting_duplicate(monkeypatch):
    config = ContainerConfig(status="unknown", pid=os.getpid())
    monkeypatch.setattr("mini_docker.container.load_container_config", lambda _: config)
    manager = Container.__new__(Container)
    monkeypatch.setattr(
        manager, "start", lambda *a, **k: pytest.fail("duplicate started")
    )
    with pytest.raises(ContainerError, match="identity"):
        manager.restart(config.id)


def test_exec_cannot_enter_a_workload_before_its_root_is_ready(monkeypatch):
    config = ContainerConfig(status="running", pid=os.getpid(), exec_ready=False)
    monkeypatch.setattr("mini_docker.container.load_container_config", lambda _: config)
    monkeypatch.setattr(os, "fork", lambda: pytest.fail("exec fork before readiness"))
    manager = Container.__new__(Container)
    with pytest.raises(ContainerError, match="verified exec"):
        manager.exec(config.id, ["/bin/true"])


def test_pidfd_pins_only_the_verified_owned_child():
    from mini_docker.process_identity import VerifiedProcess, capture_identity

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        identity = capture_identity(child.pid)
        assert identity["start_ticks"] > 0
        with VerifiedProcess(child.pid, identity) as process:
            assert process.alive()
            process.send(signal.SIGTERM)
            child.wait(timeout=5)
            assert not process.alive()
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5)


def test_reused_pid_or_old_boot_is_rejected():
    from mini_docker.process_identity import VerifiedProcess, capture_identity

    identity = capture_identity(os.getpid())
    for mismatch in [
        dict(identity, start_ticks=identity["start_ticks"] + 1),
        dict(identity, boot_id="a-different-host-boot"),
    ]:
        with pytest.raises(RuntimeError, match="identity"):
            VerifiedProcess(os.getpid(), mismatch)


def test_mismatched_process_is_not_reported_as_running(monkeypatch):
    from mini_docker.process_identity import capture_identity

    identity = capture_identity(os.getpid())
    identity["start_ticks"] += 1
    config = ContainerConfig(status="running", pid=os.getpid(), pid_identity=identity)
    monkeypatch.setattr(metadata, "save_container_config", lambda _: None)
    assert metadata._refresh_container_state(config).status == "stopped"
    assert config.pid is None
