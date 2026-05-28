from unittest import mock

import pytest

from mini_docker.container import (
    Container,
    ContainerInternalError,
    _exit_code_from_wait_status,
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
