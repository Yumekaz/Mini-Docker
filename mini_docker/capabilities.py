#!/usr/bin/env python3
"""
Linux Capabilities Management for Mini-Docker.

Capabilities break up root privileges into distinct units that can be
independently enabled or disabled. This allows containers to have
fine-grained control over what operations they can perform.

Common Capabilities:
- CAP_CHOWN: Change file ownership
- CAP_DAC_OVERRIDE: Bypass file permission checks
- CAP_NET_BIND_SERVICE: Bind to ports < 1024
- CAP_NET_RAW: Use raw sockets
- CAP_SYS_ADMIN: Broad system admin rights
- CAP_SYS_PTRACE: Trace processes
- CAP_SETUID/CAP_SETGID: Change user/group IDs

For containers, we drop most capabilities to reduce attack surface.
"""

import os
import ctypes
import struct
from typing import Set, List, Optional
from mini_docker.utils import libc

# Capability constants from <linux/capability.h>
_LINUX_CAPABILITY_VERSION_3 = 0x20080522
_LINUX_CAPABILITY_U32S_3 = 2

# Capability numbers
CAP_CHOWN = 0
CAP_DAC_OVERRIDE = 1
CAP_DAC_READ_SEARCH = 2
CAP_FOWNER = 3
CAP_FSETID = 4
CAP_KILL = 5
CAP_SETGID = 6
CAP_SETUID = 7
CAP_SETPCAP = 8
CAP_LINUX_IMMUTABLE = 9
CAP_NET_BIND_SERVICE = 10
CAP_NET_BROADCAST = 11
CAP_NET_ADMIN = 12
CAP_NET_RAW = 13
CAP_IPC_LOCK = 14
CAP_IPC_OWNER = 15
CAP_SYS_MODULE = 16
CAP_SYS_RAWIO = 17
CAP_SYS_CHROOT = 18
CAP_SYS_PTRACE = 19
CAP_SYS_PACCT = 20
CAP_SYS_ADMIN = 21
CAP_SYS_BOOT = 22
CAP_SYS_NICE = 23
CAP_SYS_RESOURCE = 24
CAP_SYS_TIME = 25
CAP_SYS_TTY_CONFIG = 26
CAP_MKNOD = 27
CAP_LEASE = 28
CAP_AUDIT_WRITE = 29
CAP_AUDIT_CONTROL = 30
CAP_SETFCAP = 31
CAP_MAC_OVERRIDE = 32
CAP_MAC_ADMIN = 33
CAP_SYSLOG = 34
CAP_WAKE_ALARM = 35
CAP_BLOCK_SUSPEND = 36
CAP_AUDIT_READ = 37
CAP_PERFMON = 38
CAP_BPF = 39
CAP_CHECKPOINT_RESTORE = 40

# Maximum capability number
CAP_LAST_CAP = 40

# Capability name mapping
CAPABILITIES = {
    "CAP_CHOWN": CAP_CHOWN,
    "CAP_DAC_OVERRIDE": CAP_DAC_OVERRIDE,
    "CAP_DAC_READ_SEARCH": CAP_DAC_READ_SEARCH,
    "CAP_FOWNER": CAP_FOWNER,
    "CAP_FSETID": CAP_FSETID,
    "CAP_KILL": CAP_KILL,
    "CAP_SETGID": CAP_SETGID,
    "CAP_SETUID": CAP_SETUID,
    "CAP_SETPCAP": CAP_SETPCAP,
    "CAP_LINUX_IMMUTABLE": CAP_LINUX_IMMUTABLE,
    "CAP_NET_BIND_SERVICE": CAP_NET_BIND_SERVICE,
    "CAP_NET_BROADCAST": CAP_NET_BROADCAST,
    "CAP_NET_ADMIN": CAP_NET_ADMIN,
    "CAP_NET_RAW": CAP_NET_RAW,
    "CAP_IPC_LOCK": CAP_IPC_LOCK,
    "CAP_IPC_OWNER": CAP_IPC_OWNER,
    "CAP_SYS_MODULE": CAP_SYS_MODULE,
    "CAP_SYS_RAWIO": CAP_SYS_RAWIO,
    "CAP_SYS_CHROOT": CAP_SYS_CHROOT,
    "CAP_SYS_PTRACE": CAP_SYS_PTRACE,
    "CAP_SYS_PACCT": CAP_SYS_PACCT,
    "CAP_SYS_ADMIN": CAP_SYS_ADMIN,
    "CAP_SYS_BOOT": CAP_SYS_BOOT,
    "CAP_SYS_NICE": CAP_SYS_NICE,
    "CAP_SYS_RESOURCE": CAP_SYS_RESOURCE,
    "CAP_SYS_TIME": CAP_SYS_TIME,
    "CAP_SYS_TTY_CONFIG": CAP_SYS_TTY_CONFIG,
    "CAP_MKNOD": CAP_MKNOD,
    "CAP_LEASE": CAP_LEASE,
    "CAP_AUDIT_WRITE": CAP_AUDIT_WRITE,
    "CAP_AUDIT_CONTROL": CAP_AUDIT_CONTROL,
    "CAP_SETFCAP": CAP_SETFCAP,
    "CAP_MAC_OVERRIDE": CAP_MAC_OVERRIDE,
    "CAP_MAC_ADMIN": CAP_MAC_ADMIN,
    "CAP_SYSLOG": CAP_SYSLOG,
    "CAP_WAKE_ALARM": CAP_WAKE_ALARM,
    "CAP_BLOCK_SUSPEND": CAP_BLOCK_SUSPEND,
    "CAP_AUDIT_READ": CAP_AUDIT_READ,
    "CAP_PERFMON": CAP_PERFMON,
    "CAP_BPF": CAP_BPF,
    "CAP_CHECKPOINT_RESTORE": CAP_CHECKPOINT_RESTORE,
}

# Reverse mapping
CAP_NUMBERS = {v: k for k, v in CAPABILITIES.items()}

# Default capabilities to keep for containers (Docker's default set)
DEFAULT_CONTAINER_CAPS = {
    CAP_CHOWN,
    CAP_DAC_OVERRIDE,
    CAP_FOWNER,
    CAP_FSETID,
    CAP_KILL,
    CAP_SETGID,
    CAP_SETUID,
    CAP_SETPCAP,
    CAP_NET_BIND_SERVICE,
    CAP_SYS_CHROOT,
    CAP_MKNOD,
    CAP_AUDIT_WRITE,
    CAP_SETFCAP,
}

# Minimal capabilities (most secure)
MINIMAL_CAPS = {
    CAP_CHOWN,
    CAP_SETGID,
    CAP_SETUID,
}


class CapabilityError(Exception):
    """Exception raised for capability operations."""

    pass


# Structure for capability header
class CapHeader(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_uint32),
        ("pid", ctypes.c_int),
    ]


# Structure for capability data (we need 2 for v3)
class CapData(ctypes.Structure):
    _fields_ = [
        ("effective", ctypes.c_uint32),
        ("permitted", ctypes.c_uint32),
        ("inheritable", ctypes.c_uint32),
    ]


def cap_to_mask(cap: int) -> tuple:
    """
    Convert a capability number to (index, bit_mask).

    Capabilities are stored in 32-bit words, so caps 0-31 are in
    the first word, 32-63 in the second, etc.

    Args:
        cap: Capability number

    Returns:
        (word_index, bit_mask)
    """
    return (cap >> 5, 1 << (cap & 31))


def get_capabilities() -> dict:
    """
    Get current process capabilities.

    Returns:
        Dictionary with 'effective', 'permitted', 'inheritable' sets
    """
    header = CapHeader()
    header.version = _LINUX_CAPABILITY_VERSION_3
    header.pid = 0  # Current process

    data = (CapData * _LINUX_CAPABILITY_U32S_3)()

    # int capget(cap_user_header_t header, cap_user_data_t data);
    ret = libc.capget(ctypes.byref(header), ctypes.byref(data))
    if ret != 0:
        errno = ctypes.get_errno()
        raise CapabilityError(f"capget failed: errno {errno}")

    result = {
        "effective": set(),
        "permitted": set(),
        "inheritable": set(),
    }

    for cap in range(CAP_LAST_CAP + 1):
        idx, mask = cap_to_mask(cap)
        if data[idx].effective & mask:
            result["effective"].add(cap)
        if data[idx].permitted & mask:
            result["permitted"].add(cap)
        if data[idx].inheritable & mask:
            result["inheritable"].add(cap)

    return result


def set_capabilities(
    effective: Optional[Set[int]] = None,
    permitted: Optional[Set[int]] = None,
    inheritable: Optional[Set[int]] = None,
) -> None:
    """
    Set process capabilities.

    Args:
        effective: Set of capabilities for effective set
        permitted: Set of capabilities for permitted set
        inheritable: Set of capabilities for inheritable set
    """
    header = CapHeader()
    header.version = _LINUX_CAPABILITY_VERSION_3
    header.pid = 0

    data = (CapData * _LINUX_CAPABILITY_U32S_3)()

    # Initialize all to zero
    for i in range(_LINUX_CAPABILITY_U32S_3):
        data[i].effective = 0
        data[i].permitted = 0
        data[i].inheritable = 0

    # Set capabilities
    if effective:
        for cap in effective:
            if cap <= CAP_LAST_CAP:
                idx, mask = cap_to_mask(cap)
                data[idx].effective |= mask

    if permitted:
        for cap in permitted:
            if cap <= CAP_LAST_CAP:
                idx, mask = cap_to_mask(cap)
                data[idx].permitted |= mask

    if inheritable:
        for cap in inheritable:
            if cap <= CAP_LAST_CAP:
                idx, mask = cap_to_mask(cap)
                data[idx].inheritable |= mask

    # int capset(cap_user_header_t header, cap_user_data_t data);
    ret = libc.capset(ctypes.byref(header), ctypes.byref(data))
    if ret != 0:
        errno = ctypes.get_errno()
        raise CapabilityError(f"capset failed: errno {errno}")


def drop_all_capabilities() -> None:
    """Drop all capabilities."""
    set_capabilities(effective=set(), permitted=set(), inheritable=set())


def drop_capabilities_except(keep: Set[int]) -> None:
    """
    Drop all capabilities except the specified ones.

    Args:
        keep: Set of capability numbers to keep
    """
    set_capabilities(
        effective=keep, permitted=keep, inheritable=set()  # Don't inherit any
    )


def apply_default_container_caps() -> None:
    """Apply Docker's default capability set for containers."""
    drop_capabilities_except(DEFAULT_CONTAINER_CAPS)


def apply_minimal_caps() -> None:
    """Apply minimal capability set for maximum security."""
    drop_capabilities_except(MINIMAL_CAPS)


def cap_name_to_number(name: str) -> Optional[int]:
    """
    Convert capability name to number.

    Args:
        name: Capability name (e.g., "CAP_NET_ADMIN" or "NET_ADMIN")

    Returns:
        Capability number or None if not found
    """
    # Normalize name
    name = name.upper()
    if not name.startswith("CAP_"):
        name = "CAP_" + name

    return CAPABILITIES.get(name)


def cap_number_to_name(cap: int) -> Optional[str]:
    """
    Convert capability number to name.

    Args:
        cap: Capability number

    Returns:
        Capability name or None if not found
    """
    return CAP_NUMBERS.get(cap)


def parse_capability_list(caps: List[str]) -> Set[int]:
    """
    Parse a list of capability names to numbers.

    Args:
        caps: List of capability names

    Returns:
        Set of capability numbers
    """
    result = set()
    for name in caps:
        num = cap_name_to_number(name)
        if num is not None:
            result.add(num)
    return result


class Capabilities:
    """
    Capability manager for containers.

    Example:
        caps = Capabilities()
        caps.add("NET_ADMIN")
        caps.remove("SYS_ADMIN")
        caps.apply()
    """

    def __init__(self, use_default: bool = True):
        if use_default:
            self.caps = DEFAULT_CONTAINER_CAPS.copy()
        else:
            self.caps = set()

    def add(self, cap: str) -> None:
        """Add a capability by name."""
        num = cap_name_to_number(cap)
        if num is not None:
            self.caps.add(num)

    def remove(self, cap: str) -> None:
        """Remove a capability by name."""
        num = cap_name_to_number(cap)
        if num is not None:
            self.caps.discard(num)

    def add_all(self) -> None:
        """Add all capabilities."""
        self.caps = set(range(CAP_LAST_CAP + 1))

    def remove_all(self) -> None:
        """Remove all capabilities."""
        self.caps = set()

    def apply(self) -> None:
        """Apply the capability set."""
        drop_capabilities_except(self.caps)

    def get_names(self) -> List[str]:
        """Get list of capability names."""
        return [
            cap_number_to_name(c) for c in sorted(self.caps) if cap_number_to_name(c)
        ]

    def __contains__(self, cap: str) -> bool:
        """Check if capability is in set."""
        num = cap_name_to_number(cap)
        return num in self.caps if num is not None else False
