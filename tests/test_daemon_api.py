from unittest import mock

from mini_docker.daemon import DockerAPIHandler
from mini_docker.daemon import run_daemon


def _make_handler(path: str):
    handler = DockerAPIHandler.__new__(DockerAPIHandler)
    handler.path = path
    handler.headers = {}
    handler.container_manager = mock.Mock()
    handler.send_empty_response = mock.Mock()
    handler.send_json_response = mock.Mock()
    handler.send_error_response = mock.Mock()
    handler.parse_body = mock.Mock(return_value={})
    return handler


def test_start_endpoint_uses_detached_mode_and_204_empty_response():
    handler = _make_handler("/containers/abc123/start")

    handler.do_POST()

    handler.container_manager.start.assert_called_once_with("abc123", attach=False)
    handler.send_empty_response.assert_called_once_with(204)
    handler.send_json_response.assert_not_called()


def test_restart_endpoint_returns_204_empty_response():
    handler = _make_handler("/containers/abc123/restart")

    handler.do_POST()

    handler.container_manager.restart.assert_called_once_with("abc123")
    handler.send_empty_response.assert_called_once_with(204)
    handler.send_json_response.assert_not_called()


def test_stop_endpoint_returns_204_empty_response():
    handler = _make_handler("/containers/abc123/stop")

    handler.do_POST()

    handler.container_manager.stop.assert_called_once_with("abc123")
    handler.send_empty_response.assert_called_once_with(204)
    handler.send_json_response.assert_not_called()


def test_delete_success_returns_204_empty_response():
    handler = _make_handler("/containers/abc123?force=true")
    handler.container_manager.remove.return_value = True

    handler.do_DELETE()

    handler.container_manager.remove.assert_called_once_with(
        "abc123", force=True, remove_volumes=False
    )
    handler.send_empty_response.assert_called_once_with(204)
    handler.send_json_response.assert_not_called()


def test_run_daemon_secures_socket_permissions(monkeypatch, tmp_path):
    socket_path = str(tmp_path / "mini-docker.sock")
    chmod = mock.Mock()
    monkeypatch.setattr("mini_docker.daemon.os.chmod", chmod)
    monkeypatch.setattr("mini_docker.daemon.ensure_directories", mock.Mock())

    class DummyServer:
        def __init__(self, path, handler):
            self.path = path
            self.handler = handler

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def serve_forever(self):
            return None

    monkeypatch.setattr("mini_docker.daemon.UnixSocketHTTPServer", DummyServer)

    run_daemon(socket_path, socket_mode=0o600)

    chmod.assert_called_once_with(socket_path, 0o600)
