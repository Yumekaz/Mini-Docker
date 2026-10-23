"""Boot/start-time identity and pidfds: numeric PIDs never authorize signals."""

import ctypes
import errno
import os
import select
import signal


class ProcessIdentityError(RuntimeError):
    pass


def capture_identity(pid):
    if not isinstance(pid, int) or pid <= 1:
        raise ProcessIdentityError("invalid process identity PID")
    try:
        with open("/proc/sys/kernel/random/boot_id") as stream:
            boot = stream.read().strip()
        with open("/proc/{}/stat".format(pid)) as stream:
            fields = stream.read().rsplit(") ", 1)[1].split()
        start = int(fields[19])
    except FileNotFoundError as exc:
        raise ProcessLookupError(pid) from exc
    except (OSError, ValueError, IndexError) as exc:
        raise ProcessIdentityError("cannot verify process identity") from exc
    if not boot or start <= 0:
        raise ProcessIdentityError("invalid process identity")
    return {"boot_id": boot, "start_ticks": start}


def _pidfd_open(pid):
    if hasattr(os, "pidfd_open"):
        return os.pidfd_open(pid, 0)
    # Python 3.8 has no os.pidfd_open. Use libc where provided; never
    # silently fall back to a racy numeric kill on unsupported hosts.
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "pidfd_open", None)
    if function is None:
        raise OSError(errno.ENOSYS, "safe process handles require pidfd support")
    function.argtypes = [ctypes.c_int, ctypes.c_uint]
    function.restype = ctypes.c_int
    result = function(pid, 0)
    if result < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    return result


def _pidfd_signal(fd, signum):
    if hasattr(signal, "pidfd_send_signal"):
        return signal.pidfd_send_signal(fd, signum, None, 0)
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "pidfd_send_signal", None)
    if function is None:
        raise OSError(errno.ENOSYS, "safe process signalling requires pidfd support")
    function.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(fd, signum, None, 0) < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


class VerifiedProcess:
    def __init__(self, pid, expected):
        self.fd = None
        if not isinstance(pid, int) or pid <= 1 or not isinstance(expected, dict):
            raise ProcessIdentityError("unverified process identity; no signal sent")
        self.fd = _pidfd_open(pid)
        try:
            if capture_identity(pid) != expected:
                raise ProcessIdentityError("process identity changed; no signal sent")
        except BaseException:
            self.close()
            raise

    def alive(self):
        poller = select.poll()
        poller.register(self.fd, select.POLLIN)
        return not poller.poll(0)

    def send(self, signum):
        _pidfd_signal(self.fd, signum)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
