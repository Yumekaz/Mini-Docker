import os
import socket
import tempfile

import pytest

from mini_docker.container import _close_inherited_descriptors


def test_supervisor_cannot_keep_parent_listener_alive():
    with tempfile.TemporaryDirectory(prefix="md-fd-") as directory:
        path = os.path.join(directory, "socket")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(path)
        listener.listen()
        ready_r, ready_w = os.pipe()
        release_r, release_w = os.pipe()
        child = os.fork()
        if child == 0:
            try:
                _close_inherited_descriptors({ready_w, release_r})
                os.write(ready_w, b"R")
                os.read(release_r, 1)
                os._exit(0)
            except BaseException:
                os._exit(1)
        os.close(ready_w)
        os.close(release_r)
        try:
            assert os.read(ready_r, 1) == b"R"
            listener.close()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                with pytest.raises(ConnectionRefusedError):
                    client.connect(path)
            # The supervisor is still alive; preserved startup pipes work.
            os.write(release_w, b"X")
            _, result = os.waitpid(child, 0)
            assert os.WIFEXITED(result) and os.WEXITSTATUS(result) == 0
            child = None
        finally:
            listener.close()
            os.close(ready_r)
            os.close(release_w)
            if child is not None:
                os.kill(child, 9)
                os.waitpid(child, 0)
