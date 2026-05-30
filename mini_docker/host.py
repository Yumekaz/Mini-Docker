#!/usr/bin/env python3
"""
Host safety checks and runtime cleanup helpers for Mini-Docker.

These helpers are intentionally conservative. They report what root-mode
runtime operations would touch and only clean Mini-Docker-owned resources.
"""

import json
import os
import platform
import re
import shutil
from typing import Dict, List, Optional

from mini_docker.cgroups import MINI_DOCKER_CGROUP
from mini_docker.metadata import (
    CONTAINERS_PATH,
    ContainerConfig,
    _hydrate_container_config,
)
from mini_docker.network import (
    BRIDGE_NAME,
    BRIDGE_SUBNET,
    delete_bridge,
    delete_veth,
    parse_port_mapping,
    remove_nat,
    remove_port_forwarding,
)
from mini_docker.utils import MINI_DOCKER_ROOT, RUN_PATH, read_file

OK = "ok"
WARN = "warn"
FAIL = "fail"
SKIP = "skip"


def _check(name: str, status: str, detail: str, fix: str = "") -> Dict[str, str]:
    return {"name": name, "status": status, "detail": detail, "fix": fix}


def _operation(
    action: str, target: str, status: str, detail: str = ""
) -> Dict[str, str]:
    return {"action": action, "target": target, "status": status, "detail": detail}


def _is_linux() -> bool:
    return platform.system().lower() == "linux"


def is_wsl() -> bool:
    """Return True when running inside Windows Subsystem for Linux."""
    version = read_file("/proc/version") or ""
    return "microsoft" in version.lower() or "WSL_INTEROP" in os.environ


def _is_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return callable(geteuid) and geteuid() == 0


def _read_first_line(path: str) -> Optional[str]:
    content = read_file(path)
    if not content:
        return None
    return content.splitlines()[0]


def _file_contains(path: str, needle: str) -> bool:
    content = read_file(path) or ""
    return needle in content


def _command_status(command: str) -> Dict[str, str]:
    path = shutil.which(command)
    if path:
        return _check(f"command:{command}", OK, path)
    return _check(
        f"command:{command}",
        FAIL,
        f"{command} not found in PATH",
        f"Install {command} before running root-mode containers.",
    )


def validate_rootfs(rootfs_path: str) -> List[Dict[str, str]]:
    """Validate a rootfs well enough to catch broken symlinks/file modes."""
    checks = []
    rootfs_path = os.path.abspath(rootfs_path)

    if not os.path.isdir(rootfs_path):
        return [
            _check(
                "rootfs",
                FAIL,
                f"rootfs not found: {rootfs_path}",
                "Run scripts/setup.sh or pass a valid rootfs path.",
            )
        ]

    checks.append(_check("rootfs", OK, rootfs_path))

    required = ["bin/busybox", "bin/sh"]
    for rel_path in required:
        path = os.path.join(rootfs_path, rel_path)
        if not os.path.exists(path):
            checks.append(
                _check(
                    f"rootfs:{rel_path}",
                    FAIL,
                    "missing",
                    "Run scripts/setup.sh --rootfs-only to repair the rootfs.",
                )
            )
            continue

        if not os.access(path, os.X_OK):
            checks.append(
                _check(
                    f"rootfs:{rel_path}",
                    FAIL,
                    "not executable",
                    "Run scripts/setup.sh --rootfs-only to repair the rootfs.",
                )
            )
            continue

        checks.append(_check(f"rootfs:{rel_path}", OK, "executable"))

    return checks


def collect_host_report(
    rootless: bool = False, rootfs_path: str = "./rootfs"
) -> Dict[str, object]:
    """Collect non-mutating host compatibility checks."""
    checks: List[Dict[str, str]] = []

    if _is_linux():
        checks.append(_check("os", OK, platform.platform()))
    else:
        checks.append(_check("os", FAIL, platform.platform(), "Use a Linux host."))

    if is_wsl():
        checks.append(
            _check(
                "environment",
                WARN,
                "WSL detected; root-mode networking/mount behavior may differ.",
                "Prefer rootless smoke tests here and full root tests on a VM/server.",
            )
        )

    if rootless:
        checks.append(_check("mode", OK, "rootless checks selected"))
    elif _is_root():
        checks.append(_check("privileges", OK, "running as root"))
    else:
        checks.append(
            _check(
                "privileges",
                FAIL,
                "root mode selected but current process is not root",
                "Use --rootless for safer dev checks or run root mode with sudo.",
            )
        )

    required_commands = ["mount", "chroot", "unshare"]
    if not rootless:
        required_commands.extend(["ip", "iptables"])

    for command in required_commands:
        checks.append(_command_status(command))

    cgroup_controllers = read_file("/sys/fs/cgroup/cgroup.controllers")
    if cgroup_controllers:
        checks.append(_check("cgroups", OK, f"v2 controllers: {cgroup_controllers}"))
        for controller in ("cpu", "memory", "pids"):
            status = OK if controller in cgroup_controllers.split() else WARN
            detail = "available" if status == OK else "not listed"
            checks.append(_check(f"cgroup:{controller}", status, detail))
    else:
        checks.append(
            _check(
                "cgroups",
                WARN if rootless else FAIL,
                "cgroups v2 controllers not available",
                "Enable cgroups v2 for root-mode resource limits.",
            )
        )

    if _file_contains("/proc/filesystems", "overlay"):
        checks.append(_check("overlayfs", OK, "overlay listed in /proc/filesystems"))
    else:
        checks.append(
            _check(
                "overlayfs",
                WARN,
                "overlay not listed in /proc/filesystems",
                "Use --no-overlay or enable OverlayFS.",
            )
        )

    seccomp_actions = _read_first_line("/proc/sys/kernel/seccomp/actions_avail")
    if seccomp_actions:
        checks.append(_check("seccomp", OK, seccomp_actions))
    else:
        checks.append(_check("seccomp", WARN, "seccomp status not detected"))

    userns_clone = _read_first_line("/proc/sys/kernel/unprivileged_userns_clone")
    if rootless and userns_clone == "0":
        checks.append(
            _check(
                "rootless:userns",
                FAIL,
                "unprivileged user namespaces are disabled",
                "Enable kernel.unprivileged_userns_clone or use root mode.",
            )
        )
    elif rootless:
        detail = userns_clone or "no distro toggle found"
        checks.append(_check("rootless:userns", OK, detail))

    checks.extend(validate_rootfs(rootfs_path))

    touches = _host_touches(rootless=rootless)
    ok = not any(check["status"] == FAIL for check in checks)

    return {
        "ok": ok,
        "mode": "rootless" if rootless else "root",
        "checks": checks,
        "host_touches": touches,
    }


def _host_touches(rootless: bool) -> List[Dict[str, str]]:
    touches = [
        {
            "resource": MINI_DOCKER_ROOT,
            "purpose": "container, image, pod, overlay, and log metadata",
        },
        {"resource": RUN_PATH, "purpose": "daemon sockets and runtime files"},
        {"resource": "rootfs mounts", "purpose": "proc/sys/dev and pivot/chroot setup"},
    ]

    if rootless:
        touches.append(
            {
                "resource": "user/mount/pid/uts/ipc namespaces",
                "purpose": "rootless container isolation",
            }
        )
        return touches

    touches.extend(
        [
            {
                "resource": MINI_DOCKER_CGROUP,
                "purpose": "cgroups v2 resource limiting and cleanup",
            },
            {
                "resource": BRIDGE_NAME,
                "purpose": "host bridge for container networking",
            },
            {"resource": "veth* interfaces", "purpose": "container network links"},
            {
                "resource": "iptables nat PREROUTING/POSTROUTING",
                "purpose": "port publishing and outbound NAT",
            },
            {
                "resource": "/proc/sys/net/ipv4/ip_forward",
                "purpose": "container outbound networking",
            },
        ]
    )
    return touches


def cleanup_runtime_resources(dry_run: bool = True) -> Dict[str, object]:
    """
    Clean host runtime resources owned by Mini-Docker.

    This intentionally skips running containers and only deletes known
    Mini-Docker cgroups when they are empty.
    """
    operations: List[Dict[str, str]] = []
    containers = _load_existing_containers()
    running = [container for container in containers if container.status == "running"]
    running_ids = {container.id for container in running}
    known_ids = {container.id for container in containers}

    for container in containers:
        if container.status == "running":
            operations.append(
                _operation(
                    "skip-container",
                    container.id,
                    SKIP,
                    "container is running",
                )
            )
            continue

        operations.extend(_cleanup_container_runtime(container, dry_run=dry_run))

    operations.extend(
        _cleanup_orphan_cgroups(
            known_ids=known_ids,
            running_ids=running_ids,
            dry_run=dry_run,
        )
    )

    if running:
        operations.append(
            _operation(
                "skip-network-base",
                BRIDGE_NAME,
                SKIP,
                "running containers still exist",
            )
        )
    else:
        operations.extend(_cleanup_network_base(dry_run=dry_run))

    failed = [op for op in operations if op["status"] == FAIL]
    removed = [op for op in operations if op["status"] == OK]
    planned = [op for op in operations if op["status"] == "planned"]

    return {
        "ok": not failed,
        "dry_run": dry_run,
        "operations": operations,
        "summary": {
            "removed": len(removed),
            "planned": len(planned),
            "failed": len(failed),
            "skipped": len([op for op in operations if op["status"] == SKIP]),
        },
    }


def _load_existing_containers() -> List[ContainerConfig]:
    """Load container metadata without creating storage directories."""
    if not os.path.isdir(CONTAINERS_PATH):
        return []

    containers = []
    for container_id in sorted(os.listdir(CONTAINERS_PATH)):
        config_path = os.path.join(CONTAINERS_PATH, container_id, "config.json")
        if not os.path.exists(config_path):
            continue

        try:
            with open(config_path, "r") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue

        config = _hydrate_container_config(data)
        if config:
            containers.append(config)

    return containers


def _cleanup_container_runtime(
    container: ContainerConfig, dry_run: bool
) -> List[Dict[str, str]]:
    operations: List[Dict[str, str]] = []

    if container.network.ports and container.network.ip:
        for port_mapping in container.network.ports:
            try:
                host_port, container_port = parse_port_mapping(port_mapping)
            except Exception as e:
                operations.append(
                    _operation("cleanup-port", port_mapping, FAIL, str(e))
                )
                continue

            target = f"{host_port}->{container.network.ip}:{container_port}"
            if dry_run:
                operations.append(_operation("cleanup-port", target, "planned"))
                continue

            try:
                remove_port_forwarding(
                    host_port, container_port, container.network.ip
                )
                operations.append(_operation("cleanup-port", target, OK))
            except Exception as e:
                operations.append(_operation("cleanup-port", target, FAIL, str(e)))

    if container.network.veth_host:
        operations.append(
            _cleanup_veth(container.network.veth_host, dry_run=dry_run)
        )

    cgroup_path = os.path.join(MINI_DOCKER_CGROUP, container.id)
    operations.append(_cleanup_empty_cgroup(cgroup_path, dry_run=dry_run))

    return operations


def _cleanup_network_base(dry_run: bool) -> List[Dict[str, str]]:
    operations = []

    if dry_run:
        operations.append(_operation("cleanup-nat", BRIDGE_SUBNET, "planned"))
        operations.append(_operation("cleanup-bridge", BRIDGE_NAME, "planned"))
        return operations

    try:
        remove_nat(BRIDGE_SUBNET)
        operations.append(_operation("cleanup-nat", BRIDGE_SUBNET, OK))
    except Exception as e:
        operations.append(_operation("cleanup-nat", BRIDGE_SUBNET, FAIL, str(e)))

    try:
        delete_bridge(BRIDGE_NAME)
        operations.append(_operation("cleanup-bridge", BRIDGE_NAME, OK))
    except Exception as e:
        operations.append(_operation("cleanup-bridge", BRIDGE_NAME, FAIL, str(e)))

    return operations


def _cleanup_orphan_cgroups(
    known_ids: set, running_ids: set, dry_run: bool
) -> List[Dict[str, str]]:
    operations: List[Dict[str, str]] = []

    if not os.path.isdir(MINI_DOCKER_CGROUP):
        return operations

    for name in sorted(os.listdir(MINI_DOCKER_CGROUP)):
        if name in known_ids or name in running_ids:
            continue
        if not re.fullmatch(r"[0-9a-f]{12}", name):
            operations.append(
                _operation(
                    "skip-cgroup",
                    os.path.join(MINI_DOCKER_CGROUP, name),
                    SKIP,
                    "not a Mini-Docker container id",
                )
            )
            continue
        operations.append(
            _cleanup_empty_cgroup(
                os.path.join(MINI_DOCKER_CGROUP, name),
                dry_run=dry_run,
            )
        )

    return operations


def _cleanup_empty_cgroup(cgroup_path: str, dry_run: bool) -> Dict[str, str]:
    if not os.path.exists(cgroup_path):
        return _operation("cleanup-cgroup", cgroup_path, SKIP, "not present")

    procs = read_file(os.path.join(cgroup_path, "cgroup.procs"))
    if procs:
        return _operation("cleanup-cgroup", cgroup_path, SKIP, "cgroup has processes")

    if dry_run:
        return _operation("cleanup-cgroup", cgroup_path, "planned")

    try:
        os.rmdir(cgroup_path)
        return _operation("cleanup-cgroup", cgroup_path, OK)
    except OSError as e:
        return _operation("cleanup-cgroup", cgroup_path, FAIL, str(e))


def _cleanup_veth(veth_name: str, dry_run: bool) -> Dict[str, str]:
    if dry_run:
        return _operation("cleanup-veth", veth_name, "planned")

    try:
        delete_veth(veth_name)
        return _operation("cleanup-veth", veth_name, OK)
    except Exception as e:
        return _operation("cleanup-veth", veth_name, FAIL, str(e))
