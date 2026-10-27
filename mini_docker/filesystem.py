#!/usr/bin/env python3
"""
Filesystem Isolation for Mini-Docker.

Mandatory pivot_root isolation, optionally backed by OverlayFS.
Requires Linux mount_setattr (5.12+) and a usable host /proc/self/fd during
setup. No mount, device, or old-root failure downgrades to a weaker isolation.

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

import ctypes
import os
import platform
import shutil
import stat
from typing import Optional, Tuple

from mini_docker.utils import get_overlay_paths, libc

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
PROC_MASKED_FILES = ("sysrq-trigger", "kcore", "keys", "timer_list")
PROC_MASK_FILE = ".proc-mask"


class FilesystemError(Exception):
    """Exception raised for filesystem operations."""

    pass


def _components(path: str, absolute: bool = False):
    if not isinstance(path, str) or "\0" in path or ".." in path.split("/"):
        raise FilesystemError("unsafe filesystem path (NUL or '..')")
    if absolute and not path.startswith("/"):
        raise FilesystemError("filesystem path must be absolute")
    return [part for part in path.split("/") if part not in ("", ".")]


def _walk_directory(start_fd: int, parts, create: bool = False) -> int:
    """Walk relative to pinned directory handles, never following symlinks."""
    current = os.dup(start_fd)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, 0o755, dir_fd=current)
                except FileExistsError:
                    pass
            following = os.open(
                part,
                os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=current,
            )
            os.close(current)
            current = following
        return current
    except BaseException:
        os.close(current)
        raise


class PinnedRoot:
    """A no-follow root and parent anchor; all staging uses relative dirfds.

    The controller must own/freeze its image and runtime-state directories
    during setup. Bind sources remain privileged, trusted-controller inputs.
    Unlike realpath checks, later ancestor symlink replacement cannot redirect
    operations performed through these already-open directory handles.
    """

    def __init__(self, path: str):
        parts = _components(path, absolute=True)
        if not parts:
            raise FilesystemError("refusing the host root as a container root")
        self.fd = None
        self.parent_fd = None
        self.name = parts[-1]
        anchor = os.open("/", os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            self.parent_fd = _walk_directory(anchor, parts[:-1])
            self.fd = self.directory(self.name, parent_fd=self.parent_fd)
        except BaseException:
            self.close()
            raise
        finally:
            os.close(anchor)

    def directory(self, path: str, create: bool = False, parent_fd=None) -> int:
        return _walk_directory(
            self.fd if parent_fd is None else parent_fd,
            _components(path),
            create,
        )

    def reopen(self, expected_fd=None):
        """After a mount, acquire its NEW vfsmount, not the covered old fd."""
        following = self.directory(self.name, parent_fd=self.parent_fd)
        # Self-binds retain the old root identity; a lower-layer bind must
        # instead expose the specifically pinned source image's identity.
        before = os.fstat(self.fd if expected_fd is None else expected_fd)
        after = os.fstat(following)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            os.close(following)
            raise FilesystemError("container root changed during bind mount")
        os.close(self.fd)
        self.fd = following

    def close(self):
        for attribute in ("fd", "parent_fd"):
            descriptor = getattr(self, attribute)
            if descriptor is not None:
                os.close(descriptor)
                setattr(self, attribute, None)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _proc_fd(fd: int) -> str:
    return f"/proc/self/fd/{fd}"


def set_mount_attributes(fd: int, flags: int, recursive: bool = True) -> None:
    """Enforce nodev/nosuid and recursive ro, including nested bind mounts.

    Legacy MS_BIND|MS_REMOUNT|MS_REC does not recursively protect submounts.
    Linux 5.12+ mount_setattr is required; ENOSYS/EPERM are fatal, not warnings.
    The supported syscall ABI matches the existing x86_64 seccomp/pivot policy.
    """
    if platform.machine() != "x86_64":
        raise FilesystemError("secure mount setup currently requires Linux x86_64")

    class MountAttr(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_uint64)
            for name in ("attr_set", "attr_clr", "propagation", "userns_fd")
        ]

    attributes = MountAttr(flags, 0, 0, 0)
    # __NR_mount_setattr=442; AT_EMPTY_PATH=0x1000; AT_RECURSIVE=0x8000.
    result = libc.syscall(
        442,
        fd,
        ctypes.c_char_p(b""),
        0x1000 | (0x8000 if recursive else 0),
        ctypes.byref(attributes),
        ctypes.sizeof(attributes),
    )
    if result != 0:
        raise FilesystemError(
            "mount_setattr enforcement failed (Linux 5.12+ and mount "
            f"permissions required): errno {ctypes.get_errno()}"
        )


def _mount_directory(root, path, source, fstype, flags, options=None):
    target = root.directory(path, create=True)
    try:
        mount(source, _proc_fd(target), fstype, flags, options)
    finally:
        os.close(target)


def validate_volume(volume):
    host, target, mode = (volume.get(key) for key in ("host", "container", "mode"))
    mode = "rw" if mode is None else mode
    _components(host, absolute=True)
    parts = _components(target, absolute=True)
    if not parts or parts[0] in ("dev", "proc", "sys", ".pivot_old"):
        raise FilesystemError("volume target overlaps a protected runtime mount")
    if mode not in ("rw", "ro"):
        raise FilesystemError("volume mode must be exactly 'rw' or 'ro'")
    return host, "/".join(parts), mode


def bind_volume(root, volume):
    host, target, mode = validate_volume(volume)
    with PinnedRoot(host) as source:
        destination = root.directory(target, create=True)
        try:
            mount(_proc_fd(source.fd), _proc_fd(destination), None, MS_BIND | MS_REC)
        finally:
            os.close(destination)
        # Reopen through the pinned root to get the newly mounted tree.
        mounted = root.directory(target)
        try:
            set_mount_attributes(
                mounted, MS_NODEV | MS_NOSUID | (MS_RDONLY if mode == "ro" else 0)
            )
        finally:
            os.close(mounted)


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
    """Compatibility entry point; isolation may no longer downgrade to chroot."""
    setup_pivot_root(rootfs_path)


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

    # Pin all paths. Do not copy through image symlinks if a bind is denied.
    with PinnedRoot(rootfs_path) as source, PinnedRoot(lower) as base, PinnedRoot(
        upper
    ) as changes, PinnedRoot(work) as scratch, PinnedRoot(merged) as target:
        mount(_proc_fd(source.fd), _proc_fd(base.fd), None, MS_BIND)
        base.reopen(source.fd)
        set_mount_attributes(base.fd, MS_RDONLY | MS_NODEV | MS_NOSUID)
        overlay_options = (
            f"lowerdir={_proc_fd(base.fd)},upperdir={_proc_fd(changes.fd)},"
            f"workdir={_proc_fd(scratch.fd)}"
        )
        mount(
            "overlay",
            _proc_fd(target.fd),
            "overlay",
            MS_NODEV | MS_NOSUID,
            overlay_options,
        )

    return lower, upper, work, merged


def setup_pivot_root(merged_path: str, volumes=None, rootless: bool = False) -> None:
    """Stage through pinned no-follow handles; pivot and detach or abort.

    Rootless uses a read-only empty /sys instead of borrowing host sysfs. It
    still requires user/PID/mount namespaces, private tmpfs/devpts/proc mounts,
    per-device binds, pivot_root and recursive mount_setattr permissions.
    """
    with PinnedRoot(merged_path) as root:
        # Reject unsafe image staging paths BEFORE the first mount/device work.
        for path in ("dev", "proc", "sys", ".pivot_old"):
            os.close(root.directory(path, create=True))
        for volume in volumes or ():
            _, path, _ = validate_volume(volume)
            os.close(root.directory(path, create=True))

        mount(_proc_fd(root.fd), _proc_fd(root.fd), None, MS_BIND | MS_REC)
        root.reopen()
        # Covers preexisting image nodes and ALL nested mounts, before /dev's
        # explicit device exception. No legacy non-recursive fallback is safe.
        set_mount_attributes(root.fd, MS_NODEV | MS_NOSUID)
        safe = MS_NOSUID | MS_NOEXEC | MS_NODEV
        # A PID namespace does not virtualize all proc controls. UID 0 can
        # otherwise reach global SysRq/sysctl handlers despite reduced caps.
        _mount_directory(root, "proc", "proc", "proc", safe | MS_RDONLY)
        _mount_directory(
            root,
            "sys",
            "tmpfs" if rootless else "sysfs",
            "tmpfs" if rootless else "sysfs",
            safe | MS_RDONLY,
            "size=64k,mode=0555" if rootless else None,
        )
        _setup_minimal_dev(root)
        _mask_proc_controls(root)
        for volume in volumes or ():
            bind_volume(root, volume)

        os.fchdir(root.fd)
        pivot_root(".", ".pivot_old")
        os.chdir("/")
        # Nothing catches a post-pivot failure or tries chroot afterwards.
        umount("/.pivot_old", MNT_DETACH | UMOUNT_NOFOLLOW)
        os.rmdir("/.pivot_old")


def cleanup_overlay(container_id: str) -> None:
    """
    Clean up OverlayFS mounts and directories for a container.

    Args:
        container_id: Container ID whose overlay to clean up
    """
    import errno

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
        try:
            os.lstat(path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise FilesystemError(
                f"overlay target inspection({path}) failed: {exc}"
            ) from exc
        try:
            # The existing wrapper records umount2 errno in ctypes' local
            # slot. Clear stale values so an unclassified error fails closed.
            ctypes.set_errno(0)
            umount(path, MNT_DETACH)
        except FilesystemError as exc:
            code = ctypes.get_errno()
            if code in (errno.ENOENT, errno.EINVAL):
                # EINVAL also describes locked mounts. Only a verified
                # nonmount is an expected already-cleaned target.
                escaped = os.path.abspath(path)
                for char, value in (
                    ("\\", "134"),
                    (" ", "040"),
                    ("\t", "011"),
                    ("\n", "012"),
                ):
                    escaped = escaped.replace(char, "\\" + value)
                mounted = False
                records = 0
                try:
                    with open("/proc/self/mountinfo", encoding="utf-8") as stream:
                        for line in stream:
                            fields = line.split()
                            if len(fields) < 10 or "-" not in fields[6:]:
                                raise FilesystemError(
                                    "cannot verify overlay nonmount: malformed mountinfo"
                                )
                            records += 1
                            mounted |= fields[4] == escaped
                except OSError as info_error:
                    raise FilesystemError(
                        f"cannot verify overlay nonmount: {info_error}"
                    ) from info_error
                if records and not mounted:
                    continue
            # Do not detach parents or recurse into a mount after failure.
            raise FilesystemError(f"overlay unmount({path}) failed: {exc}") from exc

    # Remove directories only after all target detachments were acknowledged.
    base = os.path.dirname(lower)
    try:
        shutil.rmtree(base)
    except FileNotFoundError as exc:
        # Only an absent top-level overlay is an idempotent success. A nested
        # ENOENT can leave a partial tree and must not authorize metadata loss.
        try:
            os.lstat(base)
        except FileNotFoundError:
            return
        except OSError as inspection_error:
            raise FilesystemError(
                f"cannot verify removed overlay({base}): {inspection_error}"
            ) from inspection_error
        raise FilesystemError(f"overlay removal({base}) failed: {exc}") from exc
    except OSError as exc:
        raise FilesystemError(f"overlay removal({base}) failed: {exc}") from exc


def setup_minimal_dev(rootfs_path: str) -> None:
    """Public helper with the same mandatory no-follow/private-device policy."""
    with PinnedRoot(rootfs_path) as root:
        _setup_minimal_dev(root)


def _setup_minimal_dev(root) -> None:
    # No devtmpfs, host /dev subtree, host console, or host ptmx/pts is imported.
    _mount_directory(
        root,
        "dev",
        "tmpfs",
        "tmpfs",
        MS_NOSUID | MS_NOEXEC,
        "mode=0755,size=1m,nr_inodes=1024",
    )
    dev = root.directory("dev")
    try:
        # Private regular-file masks, never a host device or host-file bind.
        # The source is sealed with /dev before any workload can run.
        empty = os.open(
            PROC_MASK_FILE,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o444,
            dir_fd=dev,
        )
        try:
            os.fchmod(empty, 0o444)
        finally:
            os.close(empty)
        for name, major, minor in (
            ("null", 1, 3),
            ("zero", 1, 5),
            ("random", 1, 8),
            ("urandom", 1, 9),
            ("tty", 5, 0),
        ):
            source = os.open(f"/dev/{name}", os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                info = os.fstat(source)
                if not stat.S_ISCHR(info.st_mode) or info.st_rdev != os.makedev(
                    major, minor
                ):
                    raise FilesystemError(
                        f"host /dev/{name} is not the allowlisted device"
                    )
                target = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=dev,
                )
                try:
                    mount(_proc_fd(source), _proc_fd(target), None, MS_BIND)
                finally:
                    os.close(target)
                # A bind references the host inode: forbid chmod/chown through
                # this mount while preserving normal character-device I/O.
                mounted = os.open(
                    name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dev
                )
                try:
                    set_mount_attributes(
                        mounted, MS_RDONLY | MS_NOSUID | MS_NOEXEC, recursive=False
                    )
                finally:
                    os.close(mounted)
            finally:
                os.close(source)
        _mount_directory(
            root,
            "dev/pts",
            "devpts",
            "devpts",
            MS_NOSUID | MS_NOEXEC,
            "newinstance,ptmxmode=0666,mode=0620",
        )
        # POSIX shared memory stays private, writable and bounded; never
        # inherit the host's /dev/shm when sealing the parent /dev mount.
        _mount_directory(
            root,
            "dev/shm",
            "tmpfs",
            "tmpfs",
            MS_NOSUID | MS_NOEXEC | MS_NODEV,
            "mode=1777,size=64m,nr_inodes=4096",
        )
        for name, target in (
            ("ptmx", "pts/ptmx"),
            ("fd", "/proc/self/fd"),
            ("stdin", "/proc/self/fd/0"),
            ("stdout", "/proc/self/fd/1"),
            ("stderr", "/proc/self/fd/2"),
        ):
            os.symlink(target, name, dir_fd=dev)
        # Seal only the private tmpfs itself. The newinstance devpts child must
        # remain writable for PTY allocation; it never contains host PTYs.
        set_mount_attributes(dev, MS_RDONLY | MS_NOSUID | MS_NOEXEC, recursive=False)
    finally:
        os.close(dev)


def _mask_proc_controls(root) -> None:
    """Mask global/sensitive proc files with the sealed private empty inode.

    Some kernel configurations omit individual interfaces. Only genuine
    absence is accepted; permission, type, mount and enforcement errors abort.
    No kernel-control file is ever opened for writing or written by setup.
    """
    dev = root.directory("dev")
    try:
        source = os.open(
            PROC_MASK_FILE, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dev
        )
    finally:
        os.close(dev)
    try:
        info = os.fstat(source)
        if not stat.S_ISREG(info.st_mode) or info.st_size != 0:
            raise FilesystemError(
                "proc mask source must be a private empty regular file"
            )
        proc = root.directory("proc")
        try:
            for name in PROC_MASKED_FILES:
                try:
                    target = os.open(
                        name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=proc
                    )
                except FileNotFoundError:
                    continue  # Kernel feature genuinely absent, not an ignored failure.
                try:
                    kind = os.fstat(target).st_mode
                    if not (stat.S_ISREG(kind) or stat.S_ISCHR(kind)):
                        raise FilesystemError(
                            "unexpected proc control target type: " + name
                        )
                    mount(_proc_fd(source), _proc_fd(target), None, MS_BIND)
                finally:
                    os.close(target)
                mounted = os.open(
                    name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=proc
                )
                try:
                    set_mount_attributes(
                        mounted,
                        MS_RDONLY | MS_NODEV | MS_NOSUID | MS_NOEXEC,
                        recursive=False,
                    )
                finally:
                    os.close(mounted)
        finally:
            os.close(proc)
    finally:
        os.close(source)


def verify_proc_policy() -> None:
    """Inspect the entered workload root before start/exec, never host controls.

    Upgrading the daemon cannot retrofit a running container's mount namespace.
    Refuse exec into legacy/unprotected namespaces instead of inheriting their
    kernel-control exposure. Operators must explicitly restart those workloads.
    """
    mounts = {}
    with open("/proc/self/mountinfo", encoding="utf-8") as stream:
        for line in stream:
            fields = line.split()
            if len(fields) < 10 or "-" not in fields:
                raise FilesystemError("cannot verify workload proc mount policy")
            separator = fields.index("-")
            if separator + 1 >= len(fields):
                raise FilesystemError("cannot verify workload proc mount policy")
            if fields[4] in mounts and (
                fields[4] == "/proc" or fields[4].startswith("/proc/")
            ):
                raise FilesystemError("ambiguous stacked workload proc mount")
            mounts[fields[4]] = (
                fields[3],
                set(fields[5].split(",")),
                fields[separator + 1],
            )
    proc = mounts.get("/proc")
    if (
        not proc
        or proc[2] != "proc"
        or not {"ro", "nodev", "nosuid", "noexec"} <= proc[1]
    ):
        raise FilesystemError(
            "read-only procfs required; explicitly restart legacy containers"
        )
    if any(
        "ro" not in attributes[1]
        for name, attributes in mounts.items()
        if name.startswith("/proc/")
    ):
        raise FilesystemError("writable workload proc submount is forbidden")
    for name in PROC_MASKED_FILES:
        target = "/proc/" + name
        try:
            info = os.lstat(target)
        except FileNotFoundError:
            continue
        masked = mounts.get(target)
        if (
            not masked
            or masked[0] != "/" + PROC_MASK_FILE
            or masked[2] != "tmpfs"
            or not stat.S_ISREG(info.st_mode)
            or info.st_size != 0
            or info.st_mode & 0o222
        ):
            raise FilesystemError("private read-only proc mask required: " + target)
