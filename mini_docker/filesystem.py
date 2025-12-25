#!/usr/bin/env python3
"""
Filesystem Isolation for Mini-Docker.

Implements two isolation methods:
1. chroot-based: Simple filesystem root change
2. OverlayFS: Copy-on-write layered filesystem

OverlayFS Layout:
    lower/  → Read-only base image (from rootfs)
    upper/  → Writable layer (container changes)
    work/   → Working directory (OverlayFS internal)
    merged/ → Unified view mounted for container

System Calls Used:
- chroot(2): Change root directory
- mount(2): Mount filesystems
- umount2(2): Unmount filesystems
- pivot_root(2): Change root filesystem
"""

import os
import ctypes
import shutil
import subprocess
from typing import Optional, Tuple
from mini_docker.utils import libc, get_overlay_paths

# Mount flags from <sys/mount.h>
MS_RDONLY = 1
MS_NOSUID = 2
MS_NODEV = 4
MS_NOEXEC = 8
MS_SYNCHRONOUS = 16
MS_REMOUNT = 32
MS_MANDLOCK = 64
MS_DIRSYNC = 128
MS_NOATIME = 1024
MS_NODIRATIME = 2048
MS_BIND = 4096
MS_MOVE = 8192
MS_REC = 16384
MS_SILENT = 32768
MS_PRIVATE = 1 << 18
MS_SLAVE = 1 << 19
MS_SHARED = 1 << 20
MS_RELATIME = 1 << 21
MS_STRICTATIME = 1 << 24

# Unmount flags
MNT_FORCE = 1
MNT_DETACH = 2
MNT_EXPIRE = 4
UMOUNT_NOFOLLOW = 8


class FilesystemError(Exception):
    """Exception raised for filesystem operations."""

    pass


def mount(
    source: str,
    target: str,
    fstype: Optional[str] = None,
    flags: int = 0,
    options: Optional[str] = None,
) -> int:
    """
    Mount a filesystem.

    This is a wrapper around the mount(2) system call.

    Args:
        source: Device or source directory
        target: Mount point
        fstype: Filesystem type (e.g., "proc", "sysfs", "overlay")
        flags: Mount flags (MS_* constants)
        options: Filesystem-specific options

    Returns:
        0 on success

    Raises:
        FilesystemError: If mount fails
    """
    source_bytes = source.encode("utf-8") if source else None
    target_bytes = target.encode("utf-8")
    fstype_bytes = fstype.encode("utf-8") if fstype else None
    options_bytes = options.encode("utf-8") if options else None

    ret = libc.mount(source_bytes, target_bytes, fstype_bytes, flags, options_bytes)

    if ret != 0:
        errno = ctypes.get_errno()
        raise FilesystemError(
            f"mount({source} -> {target}, {fstype}) failed: errno {errno}"
        )
    return ret


def umount(target: str, flags: int = 0) -> int:
    """
    Unmount a filesystem.

    Args:
        target: Mount point to unmount
        flags: Unmount flags (MNT_* constants)

    Returns:
        0 on success

    Raises:
        FilesystemError: If unmount fails
    """
    target_bytes = target.encode("utf-8")

    # Use umount2 for flags support
    ret = libc.umount2(target_bytes, flags)

    if ret != 0:
        errno = ctypes.get_errno()
        raise FilesystemError(f"umount({target}) failed: errno {errno}")
    return ret


def chroot(path: str) -> int:
    """
    Change root directory.

    After chroot, the process sees 'path' as the new root (/).
    This provides basic filesystem isolation.

    Args:
        path: New root directory

    Returns:
        0 on success

    Raises:
        FilesystemError: If chroot fails
    """
    path_bytes = path.encode("utf-8")
    ret = libc.chroot(path_bytes)

    if ret != 0:
        errno = ctypes.get_errno()
        raise FilesystemError(f"chroot({path}) failed: errno {errno}")
    return ret


def pivot_root(new_root: str, put_old: str) -> int:
    """
    Change the root filesystem.

    More secure than chroot - completely swaps root filesystems.
    After pivot_root:
    - new_root becomes /
    - old root is moved to put_old

    Args:
        new_root: New root filesystem
        put_old: Where to put old root

    Returns:
        0 on success

    Raises:
        FilesystemError: If pivot_root fails
    """
    # pivot_root is typically called via syscall
    # int pivot_root(const char *new_root, const char *put_old);
    SYS_pivot_root = 155  # x86_64 syscall number

    new_root_bytes = new_root.encode("utf-8")
    put_old_bytes = put_old.encode("utf-8")

    ret = libc.syscall(
        SYS_pivot_root, ctypes.c_char_p(new_root_bytes), ctypes.c_char_p(put_old_bytes)
    )

    if ret != 0:
        errno = ctypes.get_errno()
        raise FilesystemError(
            f"pivot_root({new_root}, {put_old}) failed: errno {errno}"
        )
    return ret


def setup_chroot_filesystem(rootfs_path: str) -> None:
    """
    Set up filesystem isolation using chroot.

    This is the simpler isolation method. It:
    1. Mounts essential filesystems (proc, sys, dev)
    2. Changes root to the container rootfs
    3. Changes to root directory

    Args:
        rootfs_path: Path to container root filesystem
    """
    # Ensure rootfs exists
    if not os.path.isdir(rootfs_path):
        raise FilesystemError(f"Rootfs not found: {rootfs_path}")

    # Mount proc filesystem
    proc_path = os.path.join(rootfs_path, "proc")
    os.makedirs(proc_path, exist_ok=True)
    try:
        mount("proc", proc_path, "proc", MS_NOSUID | MS_NOEXEC | MS_NODEV)
    except FilesystemError:
        pass  # Might already be mounted

    # Mount sys filesystem
    sys_path = os.path.join(rootfs_path, "sys")
    os.makedirs(sys_path, exist_ok=True)
    try:
        mount("sysfs", sys_path, "sysfs", MS_NOSUID | MS_NOEXEC | MS_NODEV)
    except FilesystemError:
        pass

    # Mount dev filesystem (or bind mount from host)
    dev_path = os.path.join(rootfs_path, "dev")
    os.makedirs(dev_path, exist_ok=True)
    try:
        # Use devtmpfs for /dev
        mount("devtmpfs", dev_path, "devtmpfs", MS_NOSUID)
    except FilesystemError:
        try:
            # Fallback: bind mount /dev
            mount("/dev", dev_path, None, MS_BIND | MS_REC)
        except FilesystemError:
            pass

    # Create /dev/pts
    pts_path = os.path.join(rootfs_path, "dev", "pts")
    os.makedirs(pts_path, exist_ok=True)
    try:
        mount("devpts", pts_path, "devpts", MS_NOSUID | MS_NOEXEC)
    except FilesystemError:
        pass

    # Change root
    chroot(rootfs_path)
    os.chdir("/")


def setup_overlay_filesystem(
    rootfs_path: str, container_id: str
) -> Tuple[str, str, str, str]:
    """
    Set up filesystem isolation using OverlayFS.

    OverlayFS provides a copy-on-write layered filesystem:
    - lower: Read-only base image (original rootfs)
    - upper: Writable layer (container modifications)
    - work: Working directory for OverlayFS internal use
    - merged: Unified view that combines lower and upper

    Args:
        rootfs_path: Path to base rootfs (becomes lower layer)
        container_id: Container ID for unique paths

    Returns:
        Tuple of (lower, upper, work, merged) paths
    """
    lower, upper, work, merged = get_overlay_paths(container_id)

    # Create directories
    os.makedirs(lower, exist_ok=True)
    os.makedirs(upper, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    os.makedirs(merged, exist_ok=True)

    # Copy rootfs to lower layer (or bind mount for efficiency)
    if not os.listdir(lower):
        # Bind mount the rootfs as lower layer (read-only)
        try:
            mount(rootfs_path, lower, None, MS_BIND | MS_RDONLY)
        except FilesystemError:
            # Fallback: copy the rootfs
            shutil.copytree(rootfs_path, lower, dirs_exist_ok=True)

    # Mount overlay filesystem
    overlay_options = f"lowerdir={lower}," f"upperdir={upper}," f"workdir={work}"

    mount("overlay", merged, "overlay", 0, overlay_options)

    return lower, upper, work, merged


def setup_pivot_root(merged_path: str) -> None:
    """
    Use pivot_root for more secure filesystem isolation.

    This is more secure than chroot because:
    1. It completely replaces the root filesystem
    2. The old root can be unmounted entirely
    3. It's harder to escape than chroot

    Args:
        merged_path: Path to the new root (e.g., OverlayFS merged dir)
    """
    # Mount essential filesystems in the new root
    proc_path = os.path.join(merged_path, "proc")
    sys_path = os.path.join(merged_path, "sys")
    dev_path = os.path.join(merged_path, "dev")

    os.makedirs(proc_path, exist_ok=True)
    os.makedirs(sys_path, exist_ok=True)
    os.makedirs(dev_path, exist_ok=True)

    try:
        mount("proc", proc_path, "proc", MS_NOSUID | MS_NOEXEC | MS_NODEV)
    except FilesystemError:
        pass

    try:
        mount("sysfs", sys_path, "sysfs", MS_NOSUID | MS_NOEXEC | MS_NODEV)
    except FilesystemError:
        pass

    try:
        mount("devtmpfs", dev_path, "devtmpfs", MS_NOSUID)
    except FilesystemError:
        try:
            mount("/dev", dev_path, None, MS_BIND | MS_REC)
        except FilesystemError:
            pass

    # Make new_root a mount point (required for pivot_root)
    try:
        mount(merged_path, merged_path, None, MS_BIND)
    except FilesystemError:
        pass

    # Create put_old directory
    put_old = os.path.join(merged_path, ".pivot_old")
    os.makedirs(put_old, exist_ok=True)

    # Change to new root
    os.chdir(merged_path)

    # Perform pivot_root
    try:
        pivot_root(".", ".pivot_old")
    except FilesystemError:
        # Fallback to chroot if pivot_root fails
        chroot(merged_path)
        os.chdir("/")
        return

    # Change to new root
    os.chdir("/")

    # Unmount and remove old root
    try:
        umount("/.pivot_old", MNT_DETACH)
        os.rmdir("/.pivot_old")
    except (FilesystemError, OSError):
        pass


def cleanup_overlay(container_id: str) -> None:
    """
    Clean up OverlayFS mounts and directories for a container.

    Args:
        container_id: Container ID whose overlay to clean up
    """
    lower, upper, work, merged = get_overlay_paths(container_id)

    # Unmount in reverse order of mounting
    mount_points = [
        os.path.join(merged, "dev", "pts"),
        os.path.join(merged, "dev"),
        os.path.join(merged, "sys"),
        os.path.join(merged, "proc"),
        merged,
        lower,
    ]

    for path in mount_points:
        if os.path.exists(path):
            try:
                umount(path, MNT_DETACH)
            except FilesystemError:
                pass

    # Remove directories
    base = os.path.dirname(lower)
    try:
        shutil.rmtree(base)
    except (OSError, IOError):
        pass


def setup_minimal_dev(rootfs_path: str) -> None:
    """
    Create minimal /dev entries for the container.

    Args:
        rootfs_path: Path to container root filesystem
    """
    dev_path = os.path.join(rootfs_path, "dev")
    os.makedirs(dev_path, exist_ok=True)

    # Create essential device nodes
    devices = [
        ("null", 1, 3, 0o666),
        ("zero", 1, 5, 0o666),
        ("random", 1, 8, 0o666),
        ("urandom", 1, 9, 0o666),
        ("tty", 5, 0, 0o666),
        ("console", 5, 1, 0o620),
    ]

    for name, major, minor, mode in devices:
        path = os.path.join(dev_path, name)
        if not os.path.exists(path):
            try:
                # mknod for character device
                os.mknod(path, mode | 0o020000, os.makedev(major, minor))
            except (OSError, PermissionError):
                pass

    # Create symlinks
    symlinks = [
        ("fd", "/proc/self/fd"),
        ("stdin", "/proc/self/fd/0"),
        ("stdout", "/proc/self/fd/1"),
        ("stderr", "/proc/self/fd/2"),
    ]

    for name, target in symlinks:
        path = os.path.join(dev_path, name)
        if not os.path.exists(path):
            try:
                os.symlink(target, path)
            except OSError:
                pass
