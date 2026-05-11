from unittest import mock

import pytest

from mini_docker.container import Container, ContainerError


def _make_config(status: str = "running"):
    cfg = mock.Mock()
    cfg.status = status
    cfg.network = mock.Mock()
    cfg.network.ip = "10.0.0.2"
    cfg.network.veth_host = "veth123"
    cfg.network.veth_container = "eth0"
    return cfg


@mock.patch("mini_docker.container.save_container_config")
@mock.patch("mini_docker.container.load_container_config")
def test_restart_raises_when_container_missing(mock_load, mock_save):
    mock_load.return_value = None
    manager = Container()

    with pytest.raises(ContainerError, match="Container not found"):
        manager.restart("missing")

    mock_save.assert_not_called()


@mock.patch("mini_docker.container.save_container_config")
@mock.patch("mini_docker.container.load_container_config")
@mock.patch.object(Container, "start")
@mock.patch.object(Container, "stop")
def test_restart_stops_running_container_and_resets_network_metadata(
    mock_stop, mock_start, mock_load, mock_save
):
    initial = _make_config(status="running")
    refreshed = _make_config(status="stopped")
    mock_load.side_effect = [initial, refreshed]
    mock_start.return_value = 4242

    manager = Container()
    pid = manager.restart("abc123", timeout=7)

    assert pid == 4242
    mock_stop.assert_called_once_with("abc123", timeout=7)
    mock_start.assert_called_once_with("abc123", attach=False)
    assert refreshed.network.ip is None
    assert refreshed.network.veth_host is None
    assert refreshed.network.veth_container is None
    mock_save.assert_called_once_with(refreshed)


@mock.patch("mini_docker.container.save_container_config")
@mock.patch("mini_docker.container.load_container_config")
@mock.patch.object(Container, "start")
@mock.patch.object(Container, "stop")
def test_restart_skips_stop_for_non_running_container(
    mock_stop, mock_start, mock_load, mock_save
):
    initial = _make_config(status="stopped")
    refreshed = _make_config(status="stopped")
    mock_load.side_effect = [initial, refreshed]
    mock_start.return_value = 3001

    manager = Container()
    pid = manager.restart("abc123")

    assert pid == 3001
    mock_stop.assert_not_called()
    mock_start.assert_called_once_with("abc123", attach=False)
    mock_save.assert_called_once_with(refreshed)
