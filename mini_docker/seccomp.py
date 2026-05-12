#!/usr/bin/env python3
"""
Seccomp (Secure Computing) Filtering for Mini-Docker.

Seccomp is a Linux kernel feature that restricts the system calls
a process can make. It uses BPF (Berkeley Packet Filter) programs
to filter syscalls.

SECURITY MODEL: STRICT WHITELIST APPROACH
==========================================

This module implements a WHITELIST-ONLY security model:
- Only explicitly allowed syscalls can execute
- ALL unlisted syscalls are IMMEDIATELY TERMINATED (SECCOMP_RET_KILL_PROCESS)
- There is no "blocklist" that can be bypassed
- Default-deny ensures maximum security

Why Whitelist Instead of Blocklist?
-----------------------------------
1. SECURITY: Unknown/new syscalls are automatically blocked
2. DEFENSE IN DEPTH: Attacker cannot use obscure syscalls
3. EXPLICIT CONTROL: Every allowed syscall is documented
4. FUTURE-PROOF: New kernel syscalls don't create vulnerabilities

Filter Behavior:
    - Allowed syscall     → SECCOMP_RET_ALLOW (execute normally)
    - Non-allowed syscall → SECCOMP_RET_KILL_PROCESS (immediate termination)
    - Wrong architecture  → SECCOMP_RET_KILL_PROCESS (prevent exploits)

Allowed Syscalls Table
======================

| Category          | Syscalls                                           | Purpose                    |
|-------------------|----------------------------------------------------|-----------------------------|
| File I/O          | read, write, open, openat, close, lseek            | Basic file operations       |
| File Info         | stat, fstat, lstat, newfstatat, statx              | File metadata               |
| Memory            | mmap, mprotect, munmap, brk, mremap, madvise       | Memory management           |
| Process           | fork, vfork, clone, execve, exit, exit_group       | Process lifecycle           |
| Signals           | rt_sigaction, rt_sigprocmask, rt_sigreturn         | Signal handling             |
| Network           | socket, connect, bind, listen, accept, send, recv  | Network operations          |
| Directory         | getdents, getdents64, mkdir, rmdir, getcwd, chdir  | Directory operations        |
| Time              | clock_gettime, gettimeofday, nanosleep             | Time operations             |
| IDs               | getpid, getuid, getgid, geteuid, getegid           | Process/user identification |
| Sync              | epoll_*, poll, select, futex                       | Synchronization primitives  |

BLOCKED Syscalls (Never Allowed):
- ptrace        : Process debugging/tracing (container escape vector)
- mount/umount  : Filesystem manipulation (privilege escalation)
- reboot        : System control (denial of service)
- kexec_load    : Kernel replacement (full system compromise)
- init_module   : Kernel module loading (rootkit installation)
- bpf           : BPF operations (filter bypass attempts)
- pivot_root    : Root filesystem changes (container escape)

BPF Filter Structure:
    struct sock_filter {
        __u16 code;    // Filter code
        __u8  jt;      // Jump if true
        __u8  jf;      // Jump if false
        __u32 k;       // Generic value
    };
"""

import ctypes
import os
import struct
from typing import List, Set

from mini_docker.utils import libc

# Seccomp constants from <linux/seccomp.h>
SECCOMP_MODE_DISABLED = 0
SECCOMP_MODE_STRICT = 1
SECCOMP_MODE_FILTER = 2

SECCOMP_SET_MODE_STRICT = 0
SECCOMP_SET_MODE_FILTER = 1
SECCOMP_FILTER_FLAG_TSYNC = 1

# prctl constants from <linux/prctl.h>
PR_SET_NO_NEW_PRIVS = 38
PR_GET_NO_NEW_PRIVS = 39
PR_SET_SECCOMP = 22
PR_GET_SECCOMP = 21

# BPF constants from <linux/bpf_common.h>
BPF_LD = 0x00
BPF_LDX = 0x01
BPF_ST = 0x02
BPF_STX = 0x03
BPF_ALU = 0x04
BPF_JMP = 0x05
BPF_RET = 0x06
BPF_MISC = 0x07

BPF_W = 0x00  # Word (4 bytes)
BPF_H = 0x08  # Halfword (2 bytes)
BPF_B = 0x10  # Byte

BPF_ABS = 0x20
BPF_IMM = 0x00
BPF_JEQ = 0x10
BPF_JGE = 0x30
BPF_JGT = 0x20
BPF_JSET = 0x40
BPF_K = 0x00
BPF_X = 0x08

# Seccomp return values from <linux/seccomp.h>
SECCOMP_RET_KILL_PROCESS = 0x80000000
SECCOMP_RET_KILL_THREAD = 0x00000000
SECCOMP_RET_KILL = SECCOMP_RET_KILL_THREAD
SECCOMP_RET_TRAP = 0x00030000
SECCOMP_RET_ERRNO = 0x00050000
SECCOMP_RET_TRACE = 0x7FF00000
SECCOMP_RET_LOG = 0x7FFC0000
SECCOMP_RET_ALLOW = 0x7FFF0000

# Audit architecture for x86_64
AUDIT_ARCH_X86_64 = 0xC000003E

# Struct offsets for seccomp_data
SECCOMP_DATA_NR_OFFSET = 0  # syscall number
SECCOMP_DATA_ARCH_OFFSET = 4  # audit architecture
SECCOMP_DATA_IP_OFFSET = 8  # instruction pointer
SECCOMP_DATA_ARGS_OFFSET = 16  # syscall arguments

# Common syscall numbers for x86_64
SYSCALLS = {
    "read": 0,
    "write": 1,
    "open": 2,
    "close": 3,
    "stat": 4,
    "fstat": 5,
    "lstat": 6,
    "poll": 7,
    "lseek": 8,
    "mmap": 9,
    "mprotect": 10,
    "munmap": 11,
    "brk": 12,
    "rt_sigaction": 13,
    "rt_sigprocmask": 14,
    "rt_sigreturn": 15,
    "ioctl": 16,
    "pread64": 17,
    "pwrite64": 18,
    "readv": 19,
    "writev": 20,
    "access": 21,
    "pipe": 22,
    "select": 23,
    "sched_yield": 24,
    "mremap": 25,
    "msync": 26,
    "mincore": 27,
    "madvise": 28,
    "dup": 32,
    "dup2": 33,
    "pause": 34,
    "nanosleep": 35,
    "getitimer": 36,
    "alarm": 37,
    "setitimer": 38,
    "getpid": 39,
    "socket": 41,
    "connect": 42,
    "accept": 43,
    "sendto": 44,
    "recvfrom": 45,
    "sendmsg": 46,
    "recvmsg": 47,
    "shutdown": 48,
    "bind": 49,
    "listen": 50,
    "getsockname": 51,
    "getpeername": 52,
    "socketpair": 53,
    "setsockopt": 54,
    "getsockopt": 55,
    "clone": 56,
    "fork": 57,
    "vfork": 58,
    "execve": 59,
    "exit": 60,
    "wait4": 61,
    "kill": 62,
    "uname": 63,
    "fcntl": 72,
    "flock": 73,
    "fsync": 74,
    "fdatasync": 75,
    "truncate": 76,
    "ftruncate": 77,
    "getdents": 78,
    "getcwd": 79,
    "chdir": 80,
    "fchdir": 81,
    "rename": 82,
    "mkdir": 83,
    "rmdir": 84,
    "creat": 85,
    "link": 86,
    "unlink": 87,
    "symlink": 88,
    "readlink": 89,
    "chmod": 90,
    "fchmod": 91,
    "chown": 92,
    "fchown": 93,
    "lchown": 94,
    "umask": 95,
    "gettimeofday": 96,
    "getrlimit": 97,
    "getrusage": 98,
    "sysinfo": 99,
    "times": 100,
    "getuid": 102,
    "syslog": 103,
    "getgid": 104,
    "setuid": 105,
    "setgid": 106,
    "geteuid": 107,
    "getegid": 108,
    "setpgid": 109,
    "getppid": 110,
    "getpgrp": 111,
    "setsid": 112,
    "setreuid": 113,
    "setregid": 114,
    "getgroups": 115,
    "setgroups": 116,
    "setresuid": 117,
    "getresuid": 118,
    "setresgid": 119,
    "getresgid": 120,
    "getpgid": 121,
    "setfsuid": 122,
    "setfsgid": 123,
    "getsid": 124,
    "capget": 125,
    "capset": 126,
    "rt_sigpending": 127,
    "rt_sigtimedwait": 128,
    "rt_sigqueueinfo": 129,
    "rt_sigsuspend": 130,
    "sigaltstack": 131,
    "utime": 132,
    "mknod": 133,
    "personality": 135,
    "statfs": 137,
    "fstatfs": 138,
    "getpriority": 140,
    "setpriority": 141,
    "sched_setparam": 142,
    "sched_getparam": 143,
    "sched_setscheduler": 144,
    "sched_getscheduler": 145,
    "sched_get_priority_max": 146,
    "sched_get_priority_min": 147,
    "sched_rr_get_interval": 148,
    "mlock": 149,
    "munlock": 150,
    "mlockall": 151,
    "munlockall": 152,
    "vhangup": 153,
    "prctl": 157,
    "arch_prctl": 158,
    "setrlimit": 160,
    "chroot": 161,
    "sync": 162,
    "acct": 163,
    "settimeofday": 164,
    "mount": 165,
    "umount2": 166,
    "swapon": 167,
    "swapoff": 168,
    "reboot": 169,
    "sethostname": 170,
    "setdomainname": 171,
    "iopl": 172,
    "ioperm": 173,
    "gettid": 186,
    "readahead": 187,
    "setxattr": 188,
    "lsetxattr": 189,
    "fsetxattr": 190,
    "getxattr": 191,
    "lgetxattr": 192,
    "fgetxattr": 193,
    "listxattr": 194,
    "llistxattr": 195,
    "flistxattr": 196,
    "removexattr": 197,
    "lremovexattr": 198,
    "fremovexattr": 199,
    "tkill": 200,
    "time": 201,
    "futex": 202,
    "sched_setaffinity": 203,
    "sched_getaffinity": 204,
    "set_thread_area": 205,
    "get_thread_area": 211,
    "epoll_create": 213,
    "getdents64": 217,
    "set_tid_address": 218,
    "timer_create": 222,
    "timer_settime": 223,
    "timer_gettime": 224,
    "timer_getoverrun": 225,
    "timer_delete": 226,
    "clock_settime": 227,
    "clock_gettime": 228,
    "clock_getres": 229,
    "clock_nanosleep": 230,
    "exit_group": 231,
    "epoll_wait": 232,
    "epoll_ctl": 233,
    "tgkill": 234,
    "utimes": 235,
    "mbind": 237,
    "set_mempolicy": 238,
    "get_mempolicy": 239,
    "openat": 257,
    "mkdirat": 258,
    "mknodat": 259,
    "fchownat": 260,
    "futimesat": 261,
    "newfstatat": 262,
    "unlinkat": 263,
    "renameat": 264,
    "linkat": 265,
    "symlinkat": 266,
    "readlinkat": 267,
    "fchmodat": 268,
    "faccessat": 269,
    "pselect6": 270,
    "ppoll": 271,
    "set_robust_list": 273,
    "get_robust_list": 274,
    "splice": 275,
    "tee": 276,
    "sync_file_range": 277,
    "vmsplice": 278,
    "move_pages": 279,
    "utimensat": 280,
    "epoll_pwait": 281,
    "signalfd": 282,
    "timerfd_create": 283,
    "eventfd": 284,
    "fallocate": 285,
    "timerfd_settime": 286,
    "timerfd_gettime": 287,
    "accept4": 288,
    "signalfd4": 289,
    "eventfd2": 290,
    "epoll_create1": 291,
    "dup3": 292,
    "pipe2": 293,
    "inotify_init1": 294,
    "preadv": 295,
    "pwritev": 296,
    "rt_tgsigqueueinfo": 297,
    "perf_event_open": 298,
    "recvmmsg": 299,
    "fanotify_init": 300,
    "fanotify_mark": 301,
    "prlimit64": 302,
    "name_to_handle_at": 303,
    "open_by_handle_at": 304,
    "clock_adjtime": 305,
    "syncfs": 306,
    "sendmmsg": 307,
    "setns": 308,
    "getcpu": 309,
    "process_vm_readv": 310,
    "process_vm_writev": 311,
    "kcmp": 312,
    "finit_module": 313,
    "sched_setattr": 314,
    "sched_getattr": 315,
    "renameat2": 316,
    "seccomp": 317,
    "getrandom": 318,
    "memfd_create": 319,
    "bpf": 321,
    "execveat": 322,
    "userfaultfd": 323,
    "membarrier": 324,
    "mlock2": 325,
    "copy_file_range": 326,
    "preadv2": 327,
    "pwritev2": 328,
    "pkey_mprotect": 329,
    "pkey_alloc": 330,
    "pkey_free": 331,
    "statx": 332,
    "rseq": 334,
    "pidfd_send_signal": 424,
    "pidfd_open": 434,
    "clone3": 435,
    "close_range": 436,
    "openat2": 437,
    "pidfd_getfd": 438,
    "faccessat2": 439,
    "epoll_pwait2": 441,
}

# All syscalls not in this list are KILLED, not just blocked
ALLOWED_SYSCALLS_WHITELIST = {
    # === FILE I/O ===
    "read",
    "write",
    "open",
    "openat",
    "close",
    "lseek",
    "pread64",
    "pwrite64",
    "readv",
    "writev",
    "preadv",
    "pwritev",
    "preadv2",
    "pwritev2",
    # === FILE METADATA ===
    "stat",
    "fstat",
    "lstat",
    "newfstatat",
    "statx",
    "access",
    "faccessat",
    "faccessat2",
    # === FILE OPERATIONS ===
    "fcntl",
    "flock",
    "fsync",
    "fdatasync",
    "truncate",
    "ftruncate",
    "rename",
    "renameat",
    "renameat2",
    "link",
    "linkat",
    "unlink",
    "unlinkat",
    "symlink",
    "symlinkat",
    "readlink",
    "readlinkat",
    "chmod",
    "fchmod",
    "fchmodat",
    "chown",
    "fchown",
    "fchownat",
    "lchown",
    "creat",
    "mknod",
    "mknodat",
    # === DIRECTORY ===
    "getdents",
    "getdents64",
    "getcwd",
    "chdir",
    "fchdir",
    "mkdir",
    "mkdirat",
    "rmdir",
    # === MEMORY MANAGEMENT ===
    "mmap",
    "mprotect",
    "munmap",
    "brk",
    "mremap",
    "msync",
    "mincore",
    "madvise",
    "mlock",
    "munlock",
    "mlockall",
    "munlockall",
    "mlock2",
    # === PROCESS CONTROL ===
    "fork",
    "vfork",
    "clone",
    "clone3",
    "execve",
    "execveat",
    "exit",
    "exit_group",
    "wait4",
    # === SIGNALS ===
    "rt_sigaction",
    "rt_sigprocmask",
    "rt_sigreturn",
    "rt_sigpending",
    "rt_sigtimedwait",
    "rt_sigsuspend",
    "sigaltstack",
    "kill",
    "tgkill",
    "tkill",
    # === PROCESS INFO ===
    "getpid",
    "getppid",
    "gettid",
    "getuid",
    "getgid",
    "geteuid",
    "getegid",
    "getresuid",
    "getresgid",
    "getgroups",
    "getpgid",
    "getpgrp",
    "getsid",
    # === PROCESS SETTINGS ===
    "setuid",
    "setgid",
    "setreuid",
    "setregid",
    "setresuid",
    "setresgid",
    "setgroups",
    "setpgid",
    "setsid",
    "setfsuid",
    "setfsgid",
    # === PIPES AND FIFOS ===
    "pipe",
    "pipe2",
    "dup",
    "dup2",
    "dup3",
    # === SOCKET OPERATIONS ===
    "socket",
    "connect",
    "accept",
    "accept4",
    "bind",
    "listen",
    "sendto",
    "recvfrom",
    "sendmsg",
    "recvmsg",
    "sendmmsg",
    "recvmmsg",
    "shutdown",
    "getsockname",
    "getpeername",
    "socketpair",
    "setsockopt",
    "getsockopt",
    # === POLLING AND MULTIPLEXING ===
    "poll",
    "ppoll",
    "select",
    "pselect6",
    "epoll_create",
    "epoll_create1",
    "epoll_ctl",
    "epoll_wait",
    "epoll_pwait",
    "epoll_pwait2",
    # === TIME ===
    "clock_gettime",
    "clock_getres",
    "clock_nanosleep",
    "gettimeofday",
    "nanosleep",
    "times",
    "timer_create",
    "timer_settime",
    "timer_gettime",
    "timer_getoverrun",
    "timer_delete",
    "alarm",
    "getitimer",
    "setitimer",
    # === SYNCHRONIZATION ===
    "futex",
    "set_robust_list",
    "get_robust_list",
    # === RANDOM ===
    "getrandom",
    # === RESOURCE LIMITS ===
    "getrlimit",
    "setrlimit",
    "prlimit64",
    "getrusage",
    # === SCHEDULING ===
    "sched_yield",
    "sched_getparam",
    "sched_setparam",
    "sched_getscheduler",
    "sched_setscheduler",
    "sched_get_priority_max",
    "sched_get_priority_min",
    "sched_rr_get_interval",
    "sched_getaffinity",
    "sched_setaffinity",
    "sched_getattr",
    "sched_setattr",
    # === SYSTEM INFO ===
    "uname",
    "sysinfo",
    "getcpu",
    # === MISC REQUIRED ===
    "ioctl",
    "prctl",
    "arch_prctl",
    "set_tid_address",
    "set_thread_area",
    "get_thread_area",
    "capget",  # Read capabilities only, capset removed for security
    "umask",
    "sync",
    "syncfs",
    # === EVENT/NOTIFICATION ===
    "eventfd",
    "eventfd2",
    "signalfd",
    "signalfd4",
    "timerfd_create",
    "timerfd_settime",
    "timerfd_gettime",
    "inotify_init1",
    # === MISC FILE ===
    "fallocate",
    "splice",
    "tee",
    "vmsplice",
    "copy_file_range",
    "sync_file_range",
    "memfd_create",
    "statfs",
    "fstatfs",
    "utime",
    "utimes",
    "utimensat",
    "futimesat",
    # === EXTENDED ATTRIBUTES (limited) ===
    "getxattr",
    "lgetxattr",
    "fgetxattr",
    "listxattr",
    "llistxattr",
    "flistxattr",
    # === MEMORY POLICY ===
    "mbind",
    "get_mempolicy",
    "set_mempolicy",
    # === MISC ===
    "pause",
    "rseq",
    "close_range",
    "openat2",
    "rt_tgsigqueueinfo",
    "membarrier",
}

# Attempting these results in immediate process termination
ABSOLUTELY_FORBIDDEN_SYSCALLS = {
    # === PROCESS TRACING (Container Escape) ===
    "ptrace",  # Debug/trace processes - major escape vector
    "process_vm_readv",  # Read another process's memory
    "process_vm_writev",  # Write another process's memory
    "kcmp",  # Compare kernel objects between processes
    # === KERNEL MODULE LOADING (Rootkit Installation) ===
    "init_module",  # Load kernel module from memory
    "finit_module",  # Load kernel module from file
    "delete_module",  # Unload kernel module
    # === KERNEL REPLACEMENT (System Compromise) ===
    "kexec_load",  # Load new kernel for later execution
    "kexec_file_load",  # Load new kernel from file
    # === SYSTEM CONTROL (Denial of Service) ===
    "reboot",  # Reboot/halt/power-off system
    "swapon",  # Enable swap partition
    "swapoff",  # Disable swap partition
    # === FILESYSTEM MOUNTING (Privilege Escalation) ===
    "mount",  # Mount filesystem
    "umount",  # Unmount filesystem (old)
    "umount2",  # Unmount filesystem
    "pivot_root",  # Change root filesystem
    # === TIME MANIPULATION (Security Bypass) ===
    "settimeofday",  # Set system time
    "clock_settime",  # Set clock time
    "clock_adjtime",  # Adjust clock time
    "adjtimex",  # Tune kernel clock
    # === HOSTNAME (UTS Namespace Escape) ===
    "sethostname",  # Set system hostname
    "setdomainname",  # Set domain name
    # === LOW-LEVEL I/O (Hardware Access) ===
    "iopl",  # Change I/O privilege level
    "ioperm",  # Set I/O port permissions
    # === ACCOUNTING/LOGGING ===
    "acct",  # Process accounting
    "syslog",  # Read/control kernel log
    "lookup_dcookie",  # Kernel profiling
    # === BPF/PERFORMANCE (Filter Bypass) ===
    "bpf",  # BPF operations
    "perf_event_open",  # Performance monitoring
    # === ADVANCED (Various Risks) ===
    "userfaultfd",  # User-space page fault handling
    "fanotify_init",  # Filesystem-wide monitoring
    "fanotify_mark",  # Mark filesystem objects
    # === KEYRING (Key Management) ===
    "add_key",  # Add key to keyring
    "keyctl",  # Keyring control
    "request_key",  # Request key from keyring
    # === CAPABILITIES ===
    "capset",  # Set capabilities (privilege escalation)
    # === NAMESPACE MANIPULATION ===
    "setns",  # Enter namespace
    "unshare",  # Create namespace (from inside container)
    # === PERSONALITY ===
    "personality",  # Set process execution domain
    # === QUOTAS ===
    "quotactl",  # Manipulate disk quotas
    # === VIRTUALIZATION ===
    "vhangup",  # Simulate hangup on terminal
    # === MOVE PAGES ===
    "move_pages",  # Move process pages to NUMA node
    # === SECCOMP ITSELF ===
    "seccomp",  # Modify seccomp filters
}


class SeccompError(Exception):
    """Exception raised for seccomp operations."""

    pass


def bpf_stmt(code: int, k: int) -> bytes:
    """
    Create a BPF statement (no jumps).

    struct sock_filter {
        __u16 code;
        __u8  jt;
        __u8  jf;
        __u32 k;
    };
    """
    return struct.pack("HBBI", code, 0, 0, k)


def bpf_jump(code: int, k: int, jt: int, jf: int) -> bytes:
    """
    Create a BPF jump instruction.

    Args:
        code: Instruction code
        k: Value to compare
        jt: Jump offset if true
        jf: Jump offset if false
    """
    return struct.pack("HBBI", code, jt, jf, k)


def set_no_new_privs() -> None:
    """
    Set NO_NEW_PRIVS flag.

    This prevents the process from gaining new privileges through
    execve (e.g., setuid/setgid executables).

    This is REQUIRED before installing a seccomp filter as non-root.
    """
    ret = libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
    if ret != 0:
        errno = ctypes.get_errno()
        raise SeccompError(f"Failed to set NO_NEW_PRIVS: errno {errno}")


def build_whitelist_filter(allowed_syscalls: Set[str]) -> bytes:
    """
    Build a BPF filter that ONLY allows whitelisted syscalls.

    SECURITY: This is a strict whitelist filter:
    - Listed syscalls → ALLOW
    - Everything else → KILL PROCESS (not just block!)

    Filter logic:
    1. Load architecture from seccomp_data.arch
    2. If not x86_64 → KILL (prevent architecture-based exploits)
    3. Load syscall number from seccomp_data.nr
    4. For each allowed syscall: if match → ALLOW
    5. Default action: KILL PROCESS

    Args:
        allowed_syscalls: Set of syscall names to allow

    Returns:
        Bytes of the BPF filter program
    """
    filter_parts = []

    # Load architecture from seccomp_data.arch
    filter_parts.append(bpf_stmt(BPF_LD | BPF_W | BPF_ABS, SECCOMP_DATA_ARCH_OFFSET))

    # Check architecture is x86_64, KILL if not (prevents exploits)
    filter_parts.append(
        bpf_jump(
            BPF_JMP | BPF_JEQ | BPF_K,
            AUDIT_ARCH_X86_64,
            1,  # Skip next instruction if equal (continue checking)
            0,  # Continue to KILL
        )
    )
    filter_parts.append(
        bpf_stmt(
            BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS  # KILL on wrong architecture
        )
    )

    # Load syscall number from seccomp_data.nr
    filter_parts.append(bpf_stmt(BPF_LD | BPF_W | BPF_ABS, SECCOMP_DATA_NR_OFFSET))

    # Convert syscall names to numbers, excluding forbidden syscalls
    allowed_numbers = set()
    for name in allowed_syscalls:
        # NEVER allow forbidden syscalls, even if explicitly requested
        if name in ABSOLUTELY_FORBIDDEN_SYSCALLS:
            continue
        if name in SYSCALLS:
            allowed_numbers.add(SYSCALLS[name])

    # Sort for consistent ordering
    sorted_numbers = sorted(allowed_numbers)

    # Add jump for each allowed syscall
    num_syscalls = len(sorted_numbers)

    for i, syscall_nr in enumerate(sorted_numbers):
        remaining = num_syscalls - i - 1
        filter_parts.append(
            bpf_jump(
                BPF_JMP | BPF_JEQ | BPF_K,
                syscall_nr,
                remaining + 1,  # Jump to ALLOW
                0,  # Check next syscall
            )
        )

    # DEFAULT: KILL PROCESS (syscall not in whitelist)
    # This is the core security guarantee - unknown syscalls = death
    filter_parts.append(bpf_stmt(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS))

    # ALLOW (only reached if syscall matched whitelist)
    filter_parts.append(bpf_stmt(BPF_RET | BPF_K, SECCOMP_RET_ALLOW))

    return b"".join(filter_parts)


def install_seccomp_filter(filter_prog: bytes) -> None:
    """
    Install a seccomp BPF filter.

    Uses prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &prog)

    Args:
        filter_prog: BPF filter program bytes
    """
    # struct sock_fprog {
    #     unsigned short len;    /* Number of BPF instructions */
    #     struct sock_filter *filter;
    # };

    # Calculate number of instructions (each is 8 bytes)
    num_instructions = len(filter_prog) // 8

    # Create filter array
    filter_array = ctypes.create_string_buffer(filter_prog)

    # Create sock_fprog structure
    class SockFprog(ctypes.Structure):
        _fields_ = [
            ("len", ctypes.c_ushort),
            ("filter", ctypes.POINTER(ctypes.c_char)),
        ]

    prog = SockFprog()
    prog.len = num_instructions
    prog.filter = ctypes.cast(filter_array, ctypes.POINTER(ctypes.c_char))

    # Install filter
    ret = libc.prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.byref(prog), 0, 0)

    if ret != 0:
        errno = ctypes.get_errno()
        raise SeccompError(f"Failed to install seccomp filter: errno {errno}")


def apply_seccomp_filter(whitelist: Set[str] = None) -> None:
    """
    Apply a strict whitelist seccomp filter to the current process.

    SECURITY MODEL:
    - Uses ALLOWED_SYSCALLS_WHITELIST by default
    - Custom whitelist can be provided but forbidden syscalls are always removed
    - ALL non-whitelisted syscalls result in immediate process termination
    - There is NO blocklist option - only whitelist

    Args:
        whitelist: Optional custom set of syscalls to allow
                   (defaults to ALLOWED_SYSCALLS_WHITELIST)
    """
    # Set NO_NEW_PRIVS first (required for non-root)
    set_no_new_privs()

    # Use provided whitelist or default
    if whitelist is None:
        allowed = ALLOWED_SYSCALLS_WHITELIST.copy()
    else:
        allowed = whitelist.copy()

    # ALWAYS remove forbidden syscalls - no exceptions
    allowed -= ABSOLUTELY_FORBIDDEN_SYSCALLS

    # Build and install filter
    filter_prog = build_whitelist_filter(allowed)
    install_seccomp_filter(filter_prog)


class Seccomp:
    """
    Seccomp whitelist filter manager.

    SECURITY: This class implements a strict whitelist-only model.
    Only syscalls explicitly added to the whitelist are allowed.
    All other syscalls result in immediate process termination.

    Example:
        seccomp = Seccomp()
        seccomp.add_allowed("socket")  # Add to whitelist
        seccomp.apply()

        # After apply():
        # - socket() works
        # - ptrace() → KILL (never allowed)
        # - newfangled_syscall() → KILL (not in whitelist)
    """

    def __init__(self, use_default: bool = True):
        """
        Initialize seccomp filter.

        Args:
            use_default: If True, start with ALLOWED_SYSCALLS_WHITELIST
                        If False, start with empty whitelist (very restrictive!)
        """
        if use_default:
            self.allowed = ALLOWED_SYSCALLS_WHITELIST.copy()
        else:
            self.allowed = set()

    def add_allowed(self, syscall: str) -> bool:
        """
        Add a syscall to the whitelist.

        Args:
            syscall: Syscall name to allow

        Returns:
            True if added, False if syscall is forbidden or unknown
        """
        if syscall in ABSOLUTELY_FORBIDDEN_SYSCALLS:
            return False  # Cannot allow forbidden syscalls
        if syscall in SYSCALLS:
            self.allowed.add(syscall)
            return True
        return False

    def remove_allowed(self, syscall: str) -> None:
        """Remove a syscall from the whitelist."""
        self.allowed.discard(syscall)

    def apply(self) -> None:
        """Apply the seccomp whitelist filter."""
        apply_seccomp_filter(whitelist=self.allowed)

    def get_filter_info(self) -> dict:
        """Get information about the filter configuration."""
        final_allowed = self.allowed - ABSOLUTELY_FORBIDDEN_SYSCALLS
        return {
            "allowed_count": len(final_allowed),
            "forbidden_count": len(ABSOLUTELY_FORBIDDEN_SYSCALLS),
            "allowed": sorted(final_allowed),
            "forbidden": sorted(ABSOLUTELY_FORBIDDEN_SYSCALLS),
        }
