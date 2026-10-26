"""Stop ordering: namespace writeback must not strand a live metadata writer.

These hermetic state-machine tests model the observed kernel lifetime boundary;
the privileged suite/CI separately exercise real namespaces and OverlayFS.
"""

import os
import signal

import pytest

from mini_docker import container as runtime
from mini_docker.metadata import ContainerConfig


class StopScenario:
    def __init__(
        self,
        monkeypatch,
        *,
        dead=False,
        missing=False,
        stuck=False,
        denied=False,
        reused=False,
    ):
        self.workload_alive = not dead
        self.missing = missing
        self.stuck = stuck
        self.denied = denied
        self.reused = reused
        self.pinned = False
        self.pin_closes = 0
        self.signals = []
        self.events = []
        self.clock = 0.0
        self.workload = 42003
        self.supervisor = 42002
        self.pin_fd = 99001
        self.config = ContainerConfig(
            id="owned-stop-fixture",
            status="running",
            pid=self.workload,
            supervisor_pid=self.supervisor,
            pid_identity={"boot_id": "owned-boot", "start_ticks": 7},
            supervisor_identity={"boot_id": "owned-boot", "start_ticks": 6},
        )
        scenario = self

        class Process:
            def __init__(self, pid, expected):
                if pid == scenario.workload and scenario.missing:
                    scenario.workload_alive = False
                    raise ProcessLookupError(pid)
                self.pid, self.fd = pid, pid + 100
                assert expected == (
                    scenario.config.pid_identity
                    if pid == scenario.workload
                    else scenario.config.supervisor_identity
                )

            def alive(self):
                if self.pid == scenario.workload:
                    return scenario.workload_alive
                # Last-mount teardown delays supervisor exit unless a trusted
                # outside owner holds its namespace. A genuine live writer
                # still cannot exit merely because a namespace is pinned.
                return scenario.stuck or scenario.workload_alive or not scenario.pinned

            def send(self, signum):
                assert self.pid == scenario.workload
                scenario.signals.append(signum)
                scenario.workload_alive = False

            def close(self):
                scenario.events.append(("process-close", self.pid))

            def __enter__(self):
                return self

            def __exit__(self, *_):
                self.close()

        real_open, real_close = os.open, os.close

        def open_namespace(path, flags, *args, **kwargs):
            if path != f"/proc/{self.supervisor}/ns/mnt":
                return real_open(path, flags, *args, **kwargs)
            assert (
                flags & os.O_CLOEXEC
            ), "namespace authority must not leak through exec"
            if self.denied:
                raise PermissionError("fixture namespace access denied")
            assert not self.signals, "pin before workload termination starts"
            self.pinned = True
            self.events.append(("pin", self.supervisor))
            return self.pin_fd

        def close_namespace(fd):
            if fd != self.pin_fd:
                return real_close(fd)
            assert self.pinned, "namespace descriptor closed twice"
            self.pin_closes += 1
            self.pinned = False
            self.events.append(("pin-close", self.supervisor))

        def identity(pid):
            assert pid == self.supervisor
            return (
                dict(self.config.supervisor_identity, start_ticks=999)
                if self.reused
                else self.config.supervisor_identity
            )

        def now():
            self.clock += 0.25
            return self.clock

        def finalize(*_, **__):
            assert not self.workload_alive
            assert self.pinned, "do not conflate writer exit with last-mount teardown"
            assert not self.stuck, "a live writer is not quiescent"
            self.events.append(("finalized", self.config.id))
            return True

        monkeypatch.setattr(runtime, "VerifiedProcess", Process)
        monkeypatch.setattr(runtime, "capture_identity", identity)
        monkeypatch.setattr(runtime, "load_container_config", lambda _: self.config)
        monkeypatch.setattr(runtime.os, "open", open_namespace)
        monkeypatch.setattr(runtime.os, "close", close_namespace)
        monkeypatch.setattr(runtime.time, "monotonic", now)
        monkeypatch.setattr(runtime.time, "sleep", lambda _: None)
        monkeypatch.setattr(runtime.os, "waitid", lambda *_: None)
        self.manager = runtime.Container.__new__(runtime.Container)
        monkeypatch.setattr(self.manager, "_finalize_stopped_container", finalize)


def test_stop_pins_namespace_before_signal_and_releases_after_writer_fence(monkeypatch):
    scenario = StopScenario(monkeypatch)
    assert scenario.manager.stop(scenario.config.id, timeout=1)
    assert scenario.signals == [signal.SIGTERM]
    assert scenario.pin_closes == 1 and not scenario.pinned
    assert scenario.events.index(
        ("finalized", scenario.config.id)
    ) < scenario.events.index(("pin-close", scenario.supervisor))


@pytest.mark.parametrize("missing", [False, True])
def test_dead_workload_cannot_bypass_supervisor_quiescence(monkeypatch, missing):
    scenario = StopScenario(monkeypatch, dead=True, missing=missing)
    assert scenario.manager.stop(scenario.config.id, timeout=0)
    assert not scenario.signals
    assert scenario.pin_closes == 1


@pytest.mark.parametrize("dead", [False, True])
def test_pin_never_makes_a_still_live_writer_safe(monkeypatch, dead):
    scenario = StopScenario(monkeypatch, stuck=True, dead=dead)
    with pytest.raises(runtime.ContainerInternalError, match="supervisor"):
        scenario.manager.stop(scenario.config.id, timeout=1)
    assert scenario.pin_closes == 1 and not scenario.pinned
    assert not any(event[0] == "finalized" for event in scenario.events)


@pytest.mark.parametrize("denied,reused", [(True, False), (False, True)])
def test_namespace_access_or_identity_uncertainty_never_authorizes_signals(
    monkeypatch, denied, reused
):
    scenario = StopScenario(monkeypatch, denied=denied, reused=reused)
    with pytest.raises(runtime.ContainerError):
        scenario.manager.stop(scenario.config.id, timeout=1)
    assert not scenario.signals
    assert not scenario.pinned
    assert scenario.pin_closes == int(reused)
