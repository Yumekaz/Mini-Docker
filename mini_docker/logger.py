#!/usr/bin/env python3
"""
Container Logging for Mini-Docker.

Provides:
- Log file storage at /var/lib/mini-docker/containers/<id>/container.log
- Live log streaming via tail
- Log rotation (optional)
- Timestamp formatting
"""

import os
import select
import sys
import threading
import time
from datetime import datetime
from typing import Generator, Optional, TextIO

from mini_docker.utils import get_container_path


class ContainerLogger:
    """
    Logger for container stdout/stderr.

    Example:
        logger = ContainerLogger(container_id)
        logger.write("Hello from container\\n")
        logger.close()
    """

    def __init__(self, container_id: str, max_size_mb: int = 10):
        self.container_id = container_id
        self.max_size = max_size_mb * 1024 * 1024
        self.log_path = os.path.join(get_container_path(container_id), "container.log")
        self._closed = False

        # Ensure directory exists
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)

        # Open log file
        self.file: Optional[TextIO] = None
        self._open()

    def _open(self) -> None:
        """Open the log file."""
        if self.file and not self._closed:
            try:
                self.file.close()
            except (IOError, OSError):
                pass
        self.file = open(self.log_path, "a", buffering=1)  # Line buffered
        self._closed = False

    def _rotate_if_needed(self) -> None:
        """Rotate log file if it exceeds max size."""
        if not self.file or self._closed:
            return

        try:
            size = os.path.getsize(self.log_path)
            if size > self.max_size:
                self.file.close()

                # Rotate: .log -> .log.1
                rotated = f"{self.log_path}.1"
                if os.path.exists(rotated):
                    os.remove(rotated)
                os.rename(self.log_path, rotated)

                self._open()
        except (OSError, IOError):
            pass

    def write(self, data: str, timestamp: bool = True) -> None:
        """
        Write data to log file.

        Args:
            data: Data to write
            timestamp: Whether to add timestamp
        """
        if not self.file or self._closed:
            return

        self._rotate_if_needed()

        try:
            if timestamp:
                ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
                # Add timestamp to each line
                lines = data.split("\n")
                for line in lines:
                    if line:
                        self.file.write(f"{ts} {line}\n")
            else:
                self.file.write(data)

            self.file.flush()
        except (IOError, OSError):
            pass

    def close(self) -> None:
        """Close the log file."""
        if self._closed:
            return
        self._closed = True
        if self.file:
            try:
                self.file.close()
            except (IOError, OSError):
                pass
            self.file = None

    def __del__(self):
        self.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


def read_logs(
    container_id: str,
    follow: bool = False,
    tail: Optional[int] = None,
    timestamps: bool = True,
) -> Generator[str, None, None]:
    """
    Read container logs.

    Args:
        container_id: Container ID
        follow: If True, follow log output (like tail -f)
        tail: Number of lines to show from end
        timestamps: Whether to show timestamps

    Yields:
        Log lines
    """
    log_path = os.path.join(get_container_path(container_id), "container.log")

    if not os.path.exists(log_path):
        return

    # Read existing content
    with open(log_path, "r") as f:
        lines = f.readlines()

    # Apply tail
    if tail is not None and tail > 0:
        lines = lines[-tail:]

    # Yield existing lines
    for line in lines:
        if not timestamps:
            # Remove timestamp (first 24 chars)
            parts = line.split(" ", 1)
            if len(parts) > 1:
                line = parts[1]
        yield line.rstrip("\n")

    # Follow mode
    if follow:
        with open(log_path, "r") as f:
            # Seek to end
            f.seek(0, 2)

            while True:
                line = f.readline()
                if line:
                    if not timestamps:
                        parts = line.split(" ", 1)
                        if len(parts) > 1:
                            line = parts[1]
                    yield line.rstrip("\n")
                else:
                    time.sleep(0.1)


def get_log_size(container_id: str) -> int:
    """Get size of container log in bytes."""
    log_path = os.path.join(get_container_path(container_id), "container.log")
    try:
        return os.path.getsize(log_path)
    except OSError:
        return 0


class OutputCapture:
    """
    Capture process output and write to logger.

    Example:
        capture = OutputCapture(container_id)
        capture.capture_fd(process.stdout.fileno())
        capture.start()
    """

    def __init__(self, container_id: str):
        self.logger = ContainerLogger(container_id)
        self.fds = []
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def capture_fd(self, fd: int) -> None:
        """Add a file descriptor to capture."""
        with self._lock:
            self.fds.append(fd)

    def start(self) -> None:
        """Start capturing in background thread."""
        self.running = True
        self.thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        """Stop capturing."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=1)
        self.logger.close()

    def _capture_loop(self) -> None:
        """Main capture loop."""
        while self.running:
            with self._lock:
                current_fds = self.fds.copy()

            if not current_fds:
                time.sleep(0.1)
                continue

            try:
                readable, _, _ = select.select(current_fds, [], [], 0.1)
                for fd in readable:
                    try:
                        data = os.read(fd, 4096)
                        if data:
                            self.logger.write(data.decode("utf-8", errors="replace"))
                        else:
                            with self._lock:
                                if fd in self.fds:
                                    self.fds.remove(fd)
                    except OSError:
                        with self._lock:
                            if fd in self.fds:
                                self.fds.remove(fd)
            except (ValueError, OSError):
                break


def print_logs(
    container_id: str,
    follow: bool = False,
    tail: Optional[int] = None,
    timestamps: bool = False,
) -> None:
    """
    Print container logs to stdout.

    Args:
        container_id: Container ID
        follow: Follow log output
        tail: Number of lines from end
        timestamps: Show timestamps for each line
    """
    from datetime import datetime

    try:
        for line in read_logs(container_id, follow=follow, tail=tail):
            if timestamps:
                ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                print(f"{ts} {line}")
            else:
                print(line)
    except KeyboardInterrupt:
        pass
