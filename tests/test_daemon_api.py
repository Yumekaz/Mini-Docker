from unittest import mock

from mini_docker.daemon import DockerAPIHandler


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
