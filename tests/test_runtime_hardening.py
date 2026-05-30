from unittest import mock

import pytest

from mini_docker.container import (
    Container,
    ContainerInternalError,
    _exit_code_from_wait_status,
    _try_reap_process,
)
from mini_docker.metadata import ContainerConfig


class DummyLogger:
    def __init__(self):
        self.messages = []

    def write(self, message):
        self.messages.append(message)


def test_cgroup_setup_fails_closed(monkeypatch):
    class BrokenCgroup:
        def __init__(self, _container_id):
            raise RuntimeError("no cgroup")

    monkeypatch.setattr("mini_docker.container.Cgroup", BrokenCgroup)

    manager = Container.__new__(Container)
    config = ContainerConfig(rootfs="/rootfs", command=["/bin/sh"])
    logger = DummyLogger()

    with pytest.raises(ContainerInternalError, match="Cgroup setup failed"):
        manager._setup_cgroup(config, logger)

    assert any("Cgroup setup failed" in message for message in logger.messages)


def test_cgroup_setup_skips_rootless(monkeypatch):
    cgroup = mock.Mock()
    monkeypatch.setattr("mini_docker.container.Cgroup", cgroup)

    manager = Container.__new__(Container)
    config = ContainerConfig(rootfs="/rootfs", command=["/bin/sh"], rootless=True)

    assert manager._setup_cgroup(config, DummyLogger()) is None
    cgroup.assert_not_called()


def test_capability_setup_fails_closed(monkeypatch):
    class BrokenCapabilities:
        def apply(self):
            raise RuntimeError("capset failed")

    monkeypatch.setattr("mini_docker.container.Capabilities", BrokenCapabilities)

    manager = Container.__new__(Container)
    config = ContainerConfig(rootfs="/rootfs", command=["/bin/sh"])
    logger = DummyLogger()

    with pytest.raises(ContainerInternalError, match="Capability setup failed"):
        manager._apply_capabilities(config, logger)

    assert any("Capability setup failed" in message for message in logger.messages)


def test_seccomp_setup_fails_closed(monkeypatch):
    class BrokenSeccomp:
        def apply(self):
            raise RuntimeError("filter failed")

    monkeypatch.setattr("mini_docker.container.Seccomp", BrokenSeccomp)

    manager = Container.__new__(Container)
    config = ContainerConfig(rootfs="/rootfs", command=["/bin/sh"])
    logger = DummyLogger()

    with pytest.raises(ContainerInternalError, match="Seccomp setup failed"):
        manager._apply_seccomp(config, logger)

    assert any("Seccomp setup failed" in message for message in logger.messages)


def test_seccomp_setup_respects_disabled_policy(monkeypatch):
    seccomp = mock.Mock()
    monkeypatch.setattr("mini_docker.container.Seccomp", seccomp)

    manager = Container.__new__(Container)
    config = ContainerConfig(
        rootfs="/rootfs",
        command=["/bin/sh"],
        seccomp_enabled=False,
    )

    manager._apply_seccomp(config, DummyLogger())

    seccomp.assert_not_called()


def test_wait_status_exit_code_mapping_for_normal_exit():
    assert _exit_code_from_wait_status(7 << 8) == 7


def test_wait_status_exit_code_mapping_for_signal():
    assert _exit_code_from_wait_status(9) == 137


def test_try_reap_process_returns_none_when_child_still_running(monkeypatch):
    monkeypatch.setattr("mini_docker.container.os.waitpid", lambda pid, flags: (0, 0))

    assert _try_reap_process(1234) is None


def test_try_reap_process_maps_reaped_status(monkeypatch):
    monkeypatch.setattr(
        "mini_docker.container.os.waitpid", lambda pid, flags: (pid, 7 << 8)
    )

    assert _try_reap_process(1234) == 7


def test_requested_cgroup_limit_write_failure_raises(monkeypatch, tmp_path):
    from mini_docker.cgroups import CgroupError, set_memory_limit

    monkeypatch.setattr("mini_docker.cgroups.write_file", lambda path, content: False)

    with pytest.raises(CgroupError, match="Failed to set memory limit"):
        set_memory_limit(str(tmp_path), 128 * 1024 * 1024)


def test_finalize_stopped_container_cleans_runtime_and_network_metadata(monkeypatch):
    update_status = mock.Mock()
    save_config = mock.Mock()

    config = ContainerConfig(rootfs="/rootfs", command=["/bin/sh"])
    config.network.ip = "10.0.0.2"
    config.network.veth_host = "veth1234"
    config.network.veth_container = "eth0"

    refreshed = ContainerConfig(id=config.id, rootfs="/rootfs", command=["/bin/sh"])
    refreshed.network.ip = config.network.ip
    refreshed.network.veth_host = config.network.veth_host
    refreshed.network.veth_container = config.network.veth_container

    monkeypatch.setattr("mini_docker.container.update_container_status", update_status)
    monkeypatch.setattr(
        "mini_docker.container.load_container_config", lambda _container_id: refreshed
    )
    monkeypatch.setattr("mini_docker.container.save_container_config", save_config)

    manager = Container.__new__(Container)
    manager._cleanup_runtime_resources = mock.Mock(return_value=[])

    assert manager._finalize_stopped_container(config.id, config, exit_code=0) is True

    update_status.assert_called_once_with(config.id, "stopped", exit_code=0)
    manager._cleanup_runtime_resources.assert_called_once_with(config)
    assert refreshed.network.ip is None
    assert refreshed.network.veth_host is None
    assert refreshed.network.veth_container is None
    save_config.assert_called_once_with(refreshed)
