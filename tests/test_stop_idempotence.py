"""Canonical Stop acknowledgement requires writer exit and successful cleanup.

Only private ordinary files and mocked process/network/cgroup operations are
used. Real metadata/finalization and VerifiedProcess identity checks remain.
"""

import copy
import os
import signal
from types import SimpleNamespace

import pytest

from mini_docker import cgroups
from mini_docker import container as runtime
from mini_docker import metadata, network, process_identity


@pytest.fixture
def stopped(monkeypatch, tmp_path):
    root = tmp_path / "containers"
    root.mkdir()
    anchor = tmp_path / "inert-handle"
    anchor.touch()
    monkeypatch.setattr(metadata, "CONTAINERS_PATH", str(root))
    monkeypatch.setattr(metadata, "get_container_path", lambda cid: str(root / cid))
    monkeypatch.setattr(metadata, "ensure_directories", lambda: None)
    monkeypatch.setattr(cgroups, "MINI_DOCKER_CGROUP", str(tmp_path / "cgroups"))
    scenario = SimpleNamespace(
        manager=runtime.Container.__new__(runtime.Container),
        config=metadata.ContainerConfig(
            id="owned-stop-idempotence",
            name="owned-stop-idempotence",
            status="stopped",
            exit_code=7,
            network_enabled=True,
            network=metadata.NetworkConfig(
                ip="10.0.0.2",
                veth_host="fixture-host",
                veth_container="fixture-guest",
                ports=["18080:80"],
            ),
        ),
        identities={
            42002: {"boot_id": "fixture-boot", "start_ticks": 2},
            42003: {"boot_id": "fixture-boot", "start_ticks": 3},
        },
        alive={42002: False, 42003: False},
        missing=set(),
        uncertain=set(),
        signals=[],
        handles={},
        pins=[],
        cleanup=[],
        failure=None,
        stuck_writer=False,
        clock=0.0,
    )

    def identity(pid):
        if pid in scenario.uncertain:
            raise process_identity.ProcessIdentityError("fixture identity unreadable")
        if pid in scenario.missing:
            raise ProcessLookupError(pid)
        return copy.deepcopy(scenario.identities[pid])

    def pidfd_open(pid):
        if pid in scenario.missing:
            raise ProcessLookupError(pid)
        descriptor = os.open(anchor, os.O_RDONLY | os.O_CLOEXEC)
        scenario.handles[descriptor] = pid
        return descriptor

    def send(descriptor, signum):
        pid = scenario.handles[descriptor]
        assert pid == 42003, "never signal the metadata writer"
        scenario.signals.append((pid, signum))
        scenario.alive[pid] = False
        if not scenario.stuck_writer:
            scenario.alive[42002] = False

    def pin(owner, pid, expected):
        assert not scenario.signals, "namespace must be pinned before any signal"
        assert owner.fd in scenario.handles
        if not owner.alive():
            return None
        assert identity(pid) == expected
        descriptor = os.open(anchor, os.O_RDONLY | os.O_CLOEXEC)
        scenario.pins.append(descriptor)
        return descriptor

    def cleanup(kind):
        assert not any(scenario.alive.values()), "never clean up a live writer/workload"
        for descriptor in scenario.pins:
            os.fstat(descriptor)  # The namespace pin spans finalization too.
        scenario.cleanup.append(kind)
        if scenario.failure == kind:
            raise OSError("fixture cleanup denied")

    class Network:
        def __init__(self, container_id):
            assert container_id == scenario.config.id

        def cleanup(self):
            cleanup("network")

    def now():
        scenario.clock += 0.25
        return scenario.clock

    monkeypatch.setattr(process_identity, "capture_identity", identity)
    monkeypatch.setattr(process_identity, "_pidfd_open", pidfd_open)
    monkeypatch.setattr(process_identity, "_pidfd_signal", send)
    monkeypatch.setattr(
        process_identity.VerifiedProcess,
        "alive",
        lambda process: scenario.alive[scenario.handles[process.fd]],
    )
    monkeypatch.setattr(metadata, "capture_identity", identity)
    monkeypatch.setattr(metadata, "is_process_alive", lambda pid: scenario.alive[pid])
    monkeypatch.setattr(runtime, "_pin_stop_mount_namespace", pin)
    monkeypatch.setattr(runtime, "Network", Network)
    monkeypatch.setattr(
        network, "remove_port_forwarding", lambda *args: cleanup("ports")
    )
    monkeypatch.setattr(runtime, "delete_cgroup", lambda *args: cleanup("cgroup"))
    monkeypatch.setattr(runtime.os, "waitid", lambda *args: None)
    monkeypatch.setattr(runtime.time, "monotonic", now)
    monkeypatch.setattr(runtime.time, "sleep", lambda delay: None)
    yield scenario
    # Verify all acquired handles are closed on both success and refusal.
    for descriptor in set(scenario.handles) | set(scenario.pins):
        with pytest.raises(OSError):
            os.fstat(descriptor)


def save(scenario, *, identities=False):
    if identities:
        scenario.config.pid = 42003
        scenario.config.supervisor_pid = 42002
        scenario.config.pid_identity = copy.deepcopy(scenario.identities[42003])
        scenario.config.supervisor_identity = copy.deepcopy(scenario.identities[42002])
    metadata.save_container_config(scenario.config)


@pytest.mark.parametrize("exit_code", [None, 0, 7, 137])
def test_completed_stop_repeats_cleanup_without_inventing_exit_success(
    stopped, exit_code
):
    stopped.config.exit_code = exit_code
    stopped.config.finished_at = 123.5
    save(stopped, identities=True)
    for _ in range(2):
        assert stopped.manager.stop(stopped.config.id, timeout=1) is True
        current = metadata.load_container_config(stopped.config.id)
        assert current.status == "stopped" and current.exit_code == exit_code
        assert current.finished_at == 123.5
        assert current.pid is None and current.supervisor_pid is None
        assert current.pid_identity is None and current.supervisor_identity is None
    assert stopped.cleanup == ["ports", "network", "cgroup", "network", "cgroup"]
    assert not stopped.signals


@pytest.mark.parametrize("failure", ["ports", "network", "cgroup"])
def test_cleanup_failure_is_not_acknowledged_and_stopped_retry_completes(
    stopped, failure
):
    save(stopped)
    stopped.failure = failure
    with pytest.raises(runtime.ContainerInternalError, match="cleanup failed"):
        stopped.manager.stop(stopped.config.id, timeout=1)
    failed = metadata.load_container_config(stopped.config.id)
    assert failed.status == "stopped" and failed.exit_code == 7
    if failure in {"ports", "network"}:
        assert failed.network.ip == "10.0.0.2"
        assert failed.network.veth_host == "fixture-host"
    first_attempt = list(stopped.cleanup)
    assert first_attempt == ["ports", "network", "cgroup"]
    stopped.failure = None
    assert stopped.manager.stop(stopped.config.id, timeout=1) is True
    assert stopped.cleanup[len(first_attempt) :] == (
        ["ports", "network", "cgroup"] if failure != "cgroup" else ["network", "cgroup"]
    )
    current = metadata.load_container_config(stopped.config.id)
    assert current.exit_code == 7 and current.network.ip is None
    assert not stopped.signals


@pytest.mark.parametrize("workload_state", ["absent", "dead", "missing", "live"])
def test_stopped_record_cannot_bypass_a_live_supervisor(stopped, workload_state):
    if workload_state == "absent":
        # The supervisor's terminal publish clears only the workload PID;
        # stopped does not mean that the publishing writer has exited yet.
        stopped.config.supervisor_pid = 42002
        stopped.config.supervisor_identity = copy.deepcopy(stopped.identities[42002])
        save(stopped)
    else:
        save(stopped, identities=True)
    stopped.alive[42002] = True
    stopped.stuck_writer = True
    if workload_state == "missing":
        stopped.missing.add(42003)
    elif workload_state == "live":
        stopped.alive[42003] = True
    with pytest.raises(runtime.ContainerInternalError, match="supervisor"):
        stopped.manager.stop(stopped.config.id, timeout=0)
    assert not stopped.cleanup
    assert stopped.signals == (
        [(42003, signal.SIGTERM)] if workload_state == "live" else []
    )
    assert len(stopped.pins) == 1
    current = metadata.load_container_config(stopped.config.id)
    assert current.supervisor_pid == 42002 and current.exit_code == 7


def test_stopped_record_with_live_workload_uses_verified_signal_and_writer_fence(
    stopped,
):
    save(stopped, identities=True)
    stopped.alive.update({42002: True, 42003: True})
    assert stopped.manager.stop(stopped.config.id, timeout=1) is True
    assert stopped.signals == [(42003, signal.SIGTERM)]
    assert len(stopped.pins) == 1
    assert stopped.cleanup == ["ports", "network", "cgroup"]
    assert metadata.load_container_config(stopped.config.id).exit_code == 7


@pytest.mark.parametrize("pid", [42002, 42003])
@pytest.mark.parametrize("problem", ["mismatch", "missing", "unreadable"])
def test_stopped_record_identity_uncertainty_refuses_without_signal(
    stopped, pid, problem
):
    save(stopped, identities=True)
    if problem == "mismatch":
        stopped.identities[pid]["start_ticks"] += 100
    elif problem == "missing":
        field = "supervisor_identity" if pid == 42002 else "pid_identity"
        setattr(stopped.config, field, None)
        metadata.save_container_config(stopped.config)
    else:
        stopped.uncertain.add(pid)
    with pytest.raises(runtime.ContainerInvalidStateError, match="identity"):
        stopped.manager.stop(stopped.config.id, timeout=1)
    assert not stopped.signals and not stopped.cleanup and not stopped.pins
    current = metadata.load_container_config(stopped.config.id)
    assert current.exit_code == 7 and current.supervisor_pid == 42002


@pytest.mark.parametrize("status", ["unknown", "created", "unrecognized"])
def test_nonterminal_unverified_status_is_never_a_stop_acknowledgement(stopped, status):
    stopped.config.status = status
    save(stopped, identities=True)
    with pytest.raises(runtime.ContainerInvalidStateError):
        stopped.manager.stop(stopped.config.id, timeout=1)
    assert not stopped.signals and not stopped.cleanup and not stopped.handles


@pytest.fixture
def removable(stopped, monkeypatch):
    stopped.removal = []
    stopped.remove_failure = None
    stopped.config.pod_id = "owned-pod"
    stop = stopped.manager.stop
    delete = metadata.delete_container_config

    def checked_stop(*args, **kwargs):
        stopped.removal.append("stop")
        return stop(*args, **kwargs)

    def cleanup(kind):
        stopped.removal.append(kind)
        if stopped.remove_failure == kind:
            raise OSError("fixture removal cleanup denied")

    def delete_metadata(container_id):
        stopped.removal.append("delete")
        return delete(container_id)  # Only the private ordinary-file fixture.

    monkeypatch.setattr(stopped.manager, "stop", checked_stop)
    monkeypatch.setattr(runtime, "cleanup_overlay", lambda cid: cleanup("overlay"))
    stopped.manager.pods = SimpleNamespace(
        remove_container=lambda *args: cleanup("pod")
    )
    monkeypatch.setattr(metadata, "delete_container_config", delete_metadata)
    return stopped


@pytest.mark.parametrize("force", [False, True])
def test_unknown_remove_refuses_without_cleanup_deletion_or_signal(removable, force):
    removable.config.status = "unknown"
    removable.alive.update({42002: True, 42003: True})
    save(removable, identities=True)
    before = metadata._read_container_data(removable.config.id)
    with pytest.raises(runtime.ContainerInvalidStateError, match="identity"):
        removable.manager.remove(removable.config.id, force=force)
    assert metadata._read_container_data(removable.config.id) == before
    assert not removable.removal and not removable.cleanup and not removable.signals
    assert not removable.handles


@pytest.mark.parametrize("force", [False, True])
def test_stopped_remove_requires_the_authoritative_live_writer_fence(removable, force):
    removable.config.supervisor_pid = 42002
    removable.config.supervisor_identity = copy.deepcopy(removable.identities[42002])
    removable.alive[42002] = True
    save(removable)
    before = metadata._read_container_data(removable.config.id)
    with pytest.raises(runtime.ContainerInternalError, match="supervisor"):
        removable.manager.remove(removable.config.id, force=force)
    assert metadata._read_container_data(removable.config.id) == before
    assert removable.removal == ["stop"]
    assert not removable.cleanup and not removable.signals


def test_stopped_remove_stops_and_cleans_before_deleting_metadata(removable):
    save(removable)
    assert removable.manager.remove(removable.config.id) is True
    assert removable.removal == ["stop", "overlay", "pod", "delete"]
    assert metadata.load_container_config(removable.config.id) is None
    assert not removable.signals


@pytest.mark.parametrize("failure", ["ports", "network", "cgroup", "overlay", "pod"])
def test_remove_cleanup_failure_is_visible_and_metadata_retained_for_retry(
    removable, failure
):
    save(removable)
    if failure in {"overlay", "pod"}:
        removable.remove_failure = failure
    else:
        removable.failure = failure
    with pytest.raises(runtime.ContainerInternalError, match="cleanup failed"):
        removable.manager.remove(removable.config.id)
    assert "delete" not in removable.removal
    assert metadata.load_container_config(removable.config.id).status == "stopped"
    if failure not in {"overlay", "pod"}:
        assert removable.removal == ["stop"]
    removable.remove_failure = removable.failure = None
    assert removable.manager.remove(removable.config.id) is True
    assert metadata.load_container_config(removable.config.id) is None
    assert not removable.signals


def test_stopped_finalization_preserves_recorded_exit_over_a_conflicting_waitid(
    stopped, monkeypatch
):
    stopped.config.finished_at = 123.5
    save(stopped, identities=True)
    monkeypatch.setattr(
        runtime.os,
        "waitid",
        lambda *args: SimpleNamespace(si_status=0, si_code=os.CLD_EXITED),
    )
    assert stopped.manager.stop(stopped.config.id, timeout=1) is True
    current = metadata.load_container_config(stopped.config.id)
    assert current.finished_at == 123.5 and current.exit_code == 7


def test_stopped_finalization_cannot_overwrite_a_changed_generation(
    stopped, monkeypatch
):
    save(stopped)
    commit = metadata._commit_refreshed_state
    changed = copy.deepcopy(stopped.config)
    changed.status = "running"
    changed.started_at = 200.0
    changed.finished_at = None
    changed.exit_code = None

    def publish_then_commit(container_id, expected, refreshed):
        metadata.save_container_config(changed)
        return commit(container_id, expected, refreshed)

    monkeypatch.setattr(metadata, "_commit_refreshed_state", publish_then_commit)
    with pytest.raises(runtime.ContainerInvalidStateError, match="changed"):
        stopped.manager.stop(stopped.config.id, timeout=1)
    assert metadata._read_container_data(changed.id)["started_at"] == 200.0
    assert metadata._read_container_data(changed.id)["status"] == "running"
    assert not stopped.cleanup and not stopped.signals
