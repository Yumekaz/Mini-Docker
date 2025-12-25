#!/usr/bin/env python3
"""
Linux Namespace Management for Mini-Docker.

Namespaces provide isolation for various system resources:
- PID: Process ID isolation (container has its own PID 1)
- UTS: Hostname and domain name isolation
- Mount: Filesystem mount point isolation
- IPC: Inter-process communication isolation
- Network: Network stack isolation
- User: User and group ID isolation (for rootless containers)

Uses unshare(2) to create new namespaces and setns(2) to enter existing ones.
"""

import ctypes
import os
import sys
from typing import List, Optional

from mini_docker.utils import libc

# Namespace flags from <linux/sched.h>
CLONE_NEWNS = 0x00020000  # Mount namespace
CLONE_NEWUTS = 0x04000000  # UTS namespace (hostname)
CLONE_NEWIPC = 0x08000000  # IPC namespace
CLONE_NEWUSER = 0x10000000  # User namespace
CLONE_NEWPID = 0x20000000  # PID namespace
CLONE_NEWNET = 0x40000000  # Network namespace
CLONE_NEWCGROUP = 0x02000000  # Cgroup namespace

# Namespace type to flag mapping
NAMESPACE_FLAGS = {
    "mnt": CLONE_NEWNS,
    "uts": CLONE_NEWUTS,
    "ipc": CLONE_NEWIPC,
    "user": CLONE_NEWUSER,
    "pid": CLONE_NEWPID,
    "net": CLONE_NEWNET,
    "cgroup": CLONE_NEWCGROUP,
}

# Namespace type to /proc path mapping
NAMESPACE_PATHS = {
    "mnt": "ns/mnt",
    "uts": "ns/uts",
    "ipc": "ns/ipc",
    "user": "ns/user",
    "pid": "ns/pid",
    "net": "ns/net",
    "cgroup": "ns/cgroup",
}


class NamespaceError(Exception):
    """Exception raised for namespace operations."""

    pass


def unshare(flags: int) -> int:
    """
    Create new namespaces and disassociate from parent namespaces.

    This is a wrapper around the unshare(2) system call.

    Args:
        flags: Combination of CLONE_NEW* flags

    Returns:
        0 on success

    Raises:
        NamespaceError: If unshare fails

    Example:
        # Create new PID and UTS namespaces
        unshare(CLONE_NEWPID | CLONE_NEWUTS)
    """
    # int unshare(int flags);
    ret = libc.unshare(flags)
    if ret != 0:
        errno = ctypes.get_errno()
        raise NamespaceError(f"unshare failed with errno {errno}")
    return ret


def setns(fd: int, nstype: int) -> int:
    """
    Enter an existing namespace.

    This is a wrapper around the setns(2) system call.

    Args:
        fd: File descriptor referring to a namespace
        nstype: Namespace type (0 for any, or specific CLONE_NEW* flag)

    Returns:
        0 on success

    Raises:
        NamespaceError: If setns fails
    """
    # int setns(int fd, int nstype);
    ret = libc.setns(fd, nstype)
    if ret != 0:
        errno = ctypes.get_errno()
        raise NamespaceError(f"setns failed with errno {errno}")
    return ret


def sethostname(name: str) -> int:
    """
    Set the system hostname.

    This affects the UTS namespace the process is in.

    Args:
        name: New hostname

    Returns:
        0 on success

    Raises:
        NamespaceError: If sethostname fails
    """
    # int sethostname(const char *name, size_t len);
    name_bytes = name.encode("utf-8")
    ret = libc.sethostname(name_bytes, len(name_bytes))
    if ret != 0:
        errno = ctypes.get_errno()
        raise NamespaceError(f"sethostname failed with errno {errno}")
    return ret


def create_namespaces(
    namespaces: List[str], hostname: Optional[str] = None, rootless: bool = False
) -> int:
    """
    Create multiple namespaces at once.

    Args:
        namespaces: List of namespace types to create
                   (e.g., ["pid", "uts", "mnt", "ipc", "net"])
        hostname: Optional hostname to set in UTS namespace
        rootless: If True, create user namespace first

    Returns:
        Combined flags that were used

    Raises:
        NamespaceError: If any namespace creation fails
    """
    flags = 0

    # For rootless mode, we need user namespace first
    if rootless:
        flags |= CLONE_NEWUSER

    # Build combined flags
    for ns in namespaces:
        if ns in NAMESPACE_FLAGS:
            flags |= NAMESPACE_FLAGS[ns]

    # Perform unshare
    unshare(flags)

    # Set hostname if UTS namespace was created
    if hostname and "uts" in namespaces:
        try:
            sethostname(hostname)
        except Exception as e:
            if not rootless:
                raise
            # In rootless mode, we might not be able to set hostname
            # depending on the setup, so we just warn
            print(f"Warning: Failed to set hostname: {e}", file=sys.stderr)

    return flags


def enter_namespace(pid: int, nstype: str) -> None:
    """
    Enter a namespace of another process.

    Args:
        pid: Process ID whose namespace to enter
        nstype: Namespace type to enter

    Raises:
        NamespaceError: If entering namespace fails
    """
    if nstype not in NAMESPACE_PATHS:
        raise NamespaceError(f"Unknown namespace type: {nstype}")

    ns_path = f"/proc/{pid}/ns/{nstype}"

    if not os.path.exists(ns_path):
        raise NamespaceError(f"Namespace path does not exist: {ns_path}")

    # Open the namespace file
    fd = os.open(ns_path, os.O_RDONLY)
    try:
        # Enter the namespace
        flag = NAMESPACE_FLAGS.get(nstype, 0)
        setns(fd, flag)
    finally:
        os.close(fd)


def enter_all_namespaces(pid: int, namespaces: Optional[List[str]] = None) -> None:
    """
    Enter all namespaces of another process.

    Args:
        pid: Process ID whose namespaces to enter
        namespaces: Optional list of specific namespaces to enter
                   (defaults to all available)

    Raises:
        NamespaceError: If entering any namespace fails
    """
    if namespaces is None:
        namespaces = ["mnt", "uts", "ipc", "pid", "net"]

    for nstype in namespaces:
        try:
            enter_namespace(pid, nstype)
        except NamespaceError:
            # Some namespaces might not be available
            pass


def get_namespace_id(pid: int, nstype: str) -> Optional[str]:
    """
    Get the namespace ID for a process.

    Args:
        pid: Process ID
        nstype: Namespace type

    Returns:
        Namespace ID string, or None if not available
    """
    ns_path = f"/proc/{pid}/ns/{nstype}"
    try:
        # The symlink target contains the namespace ID
        target = os.readlink(ns_path)
        # Format: "type:[inode]"
        return target
    except (OSError, IOError):
        return None


def setup_user_namespace(pid: int, uid: int = 0, gid: int = 0) -> None:
    """
    Set up UID/GID mapping for user namespace (rootless mode).

    Maps the current user's UID/GID to root (0) inside the container.

    Args:
        pid: Process ID in user namespace
        uid: UID outside the namespace (current user)
        gid: GID outside the namespace (current user)

    This writes to:
        /proc/<pid>/uid_map
        /proc/<pid>/gid_map
        /proc/<pid>/setgroups
    """
    # Get the real UID/GID if not specified
    if uid == 0:
        uid = os.getuid()
    if gid == 0:
        gid = os.getgid()

    # Deny setgroups to allow writing gid_map as non-root
    setgroups_path = f"/proc/{pid}/setgroups"
    try:
        with open(setgroups_path, "w") as f:
            f.write("deny")
    except (IOError, OSError):
        pass  # Might not exist or might not be writable

    # Write UID mapping: "inside-uid outside-uid count"
    # Map current user to root inside container
    uid_map_path = f"/proc/{pid}/uid_map"
    try:
        with open(uid_map_path, "w") as f:
            f.write(f"0 {uid} 1\n")
    except (IOError, OSError) as e:
        raise NamespaceError(f"Failed to write uid_map: {e}")

    # Write GID mapping
    gid_map_path = f"/proc/{pid}/gid_map"
    try:
        with open(gid_map_path, "w") as f:
            f.write(f"0 {gid} 1\n")
    except (IOError, OSError) as e:
        raise NamespaceError(f"Failed to write gid_map: {e}")


class Namespace:
    """
    Context manager for namespace operations.

    Example:
        with Namespace(["pid", "uts", "mnt"], hostname="container"):
            # Code runs in new namespaces
            pass
    """

    def __init__(
        self,
        namespaces: List[str],
        hostname: Optional[str] = None,
        rootless: bool = False,
    ):
        self.namespaces = namespaces
        self.hostname = hostname
        self.rootless = rootless
        self.original_ns_fds = {}

    def __enter__(self):
        # Save original namespace FDs
        for ns in self.namespaces:
            try:
                path = f"/proc/self/ns/{ns}"
                if os.path.exists(path):
                    self.original_ns_fds[ns] = os.open(path, os.O_RDONLY)
            except OSError:
                pass

        # Create new namespaces
        create_namespaces(
            self.namespaces, hostname=self.hostname, rootless=self.rootless
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Restore original namespaces (if possible)
        for ns, fd in self.original_ns_fds.items():
            try:
                setns(fd, NAMESPACE_FLAGS.get(ns, 0))
            except NamespaceError:
                pass
            finally:
                os.close(fd)
        return False
