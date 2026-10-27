"""Detached supervision must not retain a CLI caller's capture pipes."""

import json
import os
import selectors
import subprocess
import sys
import time
from pathlib import Path

import pytest

_SUPERVISOR = r"""
import os, sys
from types import SimpleNamespace
from mini_docker.container import Container
from mini_docker.metadata import ContainerConfig

root, release_fd, attach = sys.argv[1], int(sys.argv[2]), sys.argv[3] == 'yes'
metadata_fd = os.open(root + '/metadata.json', os.O_RDWR | os.O_CREAT, 0o600)
log_fd = os.open(root + '/container.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
manager = Container.__new__(Container)
def workload(config, logger, attached, output_fd):
    if not attached:
        private_input = os.open('/dev/null', os.O_RDONLY)
        os.dup2(private_input, 0)
        os.close(private_input)
        os.dup2(output_fd, 1)
        os.dup2(output_fd, 2)
    # A real exec closes the supervisor's CLOEXEC readiness pipe. Keep only
    # this test's explicit release capability across that exec.
    os.set_inheritable(release_fd, True)
    os.execv(sys.executable, [sys.executable, '-c',
        'import os,sys; os.read(int(sys.argv[1]),1); os._exit(7)', str(release_fd)])
manager._prepare_and_exec_workload = workload
child = os.fork()
if child:
    print('detached-parent-exited', flush=True)
    os._exit(0)
manager._supervise_pid_namespace_workload(
    ContainerConfig(id='stdio-proof'), metadata_fd,
    SimpleNamespace(close=lambda: None), attach, log_fd,
)
"""


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
@pytest.mark.parametrize("attach", [False, True])
def test_supervisor_stdio_matches_attachment_mode(tmp_path, attach):
    release_read, release_write = os.pipe()
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _SUPERVISOR,
            str(tmp_path),
            str(release_read),
            "yes" if attach else "no",
        ],
        cwd=Path(__file__).resolve().parents[1],
        pass_fds=(release_read,),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
    )
    os.close(release_read)
    received = b""
    eof = False
    try:
        assert child.wait(timeout=5) == 0
        os.set_blocking(child.stdout.fileno(), False)
        with selectors.DefaultSelector() as ready:
            ready.register(child.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if ready.select(max(0, deadline - time.monotonic())):
                    data = os.read(child.stdout.fileno(), 4096)
                    if not data:
                        eof = True
                        break
                    received += data
        assert b"detached-parent-exited" in received
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                record = json.loads((tmp_path / "metadata.json").read_text())
                if record["status"] == "running" and record["exec_ready"]:
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.01)
        else:
            pytest.fail("owned workload never acknowledged exec readiness")
        # EOF must be available while the detached workload is STILL alive.
        # Attached mode deliberately keeps the caller's stdio until exit.
        assert eof == (not attach), "detached supervisor retained the caller's pipe"
    finally:
        os.write(release_write, b"X")
        os.close(release_write)
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        child.stdout.close()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                record = json.loads((tmp_path / "metadata.json").read_text())
                if record.get("status") == "stopped":
                    assert record["exit_code"] == 7
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.01)
        else:
            pytest.fail(
                "owned supervisor did not retain its terminal completion record"
            )
