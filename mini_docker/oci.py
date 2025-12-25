#!/usr/bin/env python3
"""
OCI Runtime Specification Support for Mini-Docker.

Implements loading and running OCI bundles following the OCI Runtime Spec:
https://github.com/opencontainers/runtime-spec

An OCI bundle contains:
    bundle/
    ├── config.json    # OCI runtime configuration
    └── rootfs/        # Container root filesystem

The config.json follows the OCI runtime-spec format:
{
    "ociVersion": "1.0.0",
    "process": { ... },
    "root": { ... },
    "mounts": [ ... ],
    "linux": { ... }
}
"""

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from mini_docker.metadata import ContainerConfig, NetworkConfig, ResourceLimits


@dataclass
class OCIProcess:
    """OCI Process configuration."""

    terminal: bool = False
    user: Dict[str, int] = field(default_factory=lambda: {"uid": 0, "gid": 0})
    args: List[str] = field(default_factory=list)
    env: List[str] = field(
        default_factory=lambda: [
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        ]
    )
    cwd: str = "/"
    capabilities: Dict[str, List[str]] = field(default_factory=dict)
    rlimits: List[Dict] = field(default_factory=list)
    noNewPrivileges: bool = True


@dataclass
class OCIRoot:
    """OCI Root filesystem configuration."""

    path: str = "rootfs"
    readonly: bool = False


@dataclass
class OCIMount:
    """OCI Mount configuration."""

    destination: str = ""
    type: str = ""
    source: str = ""
    options: List[str] = field(default_factory=list)


@dataclass
class OCINamespace:
    """OCI Linux namespace configuration."""

    type: str = ""
    path: Optional[str] = None


@dataclass
class OCILinuxResources:
    """OCI Linux resource limits."""

    memory: Dict[str, int] = field(default_factory=dict)
    cpu: Dict[str, Any] = field(default_factory=dict)
    pids: Dict[str, int] = field(default_factory=dict)


@dataclass
class OCILinux:
    """OCI Linux-specific configuration."""

    namespaces: List[OCINamespace] = field(default_factory=list)
    resources: OCILinuxResources = field(default_factory=OCILinuxResources)
    seccomp: Dict = field(default_factory=dict)
    maskedPaths: List[str] = field(default_factory=list)
    readonlyPaths: List[str] = field(default_factory=list)


@dataclass
class OCIConfig:
    """Complete OCI runtime configuration."""

    ociVersion: str = "1.0.0"
    process: OCIProcess = field(default_factory=OCIProcess)
    root: OCIRoot = field(default_factory=OCIRoot)
    hostname: str = ""
    mounts: List[OCIMount] = field(default_factory=list)
    linux: OCILinux = field(default_factory=OCILinux)


class OCIError(Exception):
    """Exception raised for OCI operations."""

    pass


def load_oci_config(bundle_path: str) -> OCIConfig:
    """
    Load OCI config.json from a bundle.

    Args:
        bundle_path: Path to OCI bundle directory

    Returns:
        OCIConfig instance

    Raises:
        OCIError: If config is invalid or missing
    """
    config_path = os.path.join(bundle_path, "config.json")

    if not os.path.exists(config_path):
        raise OCIError(f"config.json not found in bundle: {bundle_path}")

    try:
        with open(config_path, "r") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise OCIError(f"Invalid JSON in config.json: {e}")

    return parse_oci_config(data)


def parse_oci_config(data: Dict) -> OCIConfig:
    """
    Parse OCI config dictionary into OCIConfig dataclass.

    Args:
        data: Dictionary from config.json

    Returns:
        OCIConfig instance
    """
    config = OCIConfig()

    config.ociVersion = data.get("ociVersion", "1.0.0")
    config.hostname = data.get("hostname", "")

    # Parse process
    if "process" in data:
        proc = data["process"]
        config.process = OCIProcess(
            terminal=proc.get("terminal", False),
            user=proc.get("user", {"uid": 0, "gid": 0}),
            args=proc.get("args", []),
            env=proc.get("env", []),
            cwd=proc.get("cwd", "/"),
            capabilities=proc.get("capabilities", {}),
            rlimits=proc.get("rlimits", []),
            noNewPrivileges=proc.get("noNewPrivileges", True),
        )

    # Parse root
    if "root" in data:
        root = data["root"]
        config.root = OCIRoot(
            path=root.get("path", "rootfs"),
            readonly=root.get("readonly", False),
        )

    # Parse mounts
    if "mounts" in data:
        config.mounts = [
            OCIMount(
                destination=m.get("destination", ""),
                type=m.get("type", ""),
                source=m.get("source", ""),
                options=m.get("options", []),
            )
            for m in data["mounts"]
        ]

    # Parse linux
    if "linux" in data:
        linux = data["linux"]

        namespaces = []
        for ns in linux.get("namespaces", []):
            namespaces.append(
                OCINamespace(
                    type=ns.get("type", ""),
                    path=ns.get("path"),
                )
            )

        resources = OCILinuxResources()
        if "resources" in linux:
            res = linux["resources"]
            resources.memory = res.get("memory", {})
            resources.cpu = res.get("cpu", {})
            resources.pids = res.get("pids", {})

        config.linux = OCILinux(
            namespaces=namespaces,
            resources=resources,
            seccomp=linux.get("seccomp", {}),
            maskedPaths=linux.get("maskedPaths", []),
            readonlyPaths=linux.get("readonlyPaths", []),
        )

    return config


def oci_to_container_config(oci_config: OCIConfig, bundle_path: str) -> ContainerConfig:
    """
    Convert OCI config to Mini-Docker ContainerConfig.

    Args:
        oci_config: OCIConfig instance
        bundle_path: Path to OCI bundle

    Returns:
        ContainerConfig instance
    """
    # Get rootfs path
    rootfs = os.path.join(bundle_path, oci_config.root.path)
    if not os.path.isabs(rootfs):
        rootfs = os.path.abspath(rootfs)

    # Parse environment variables
    env = {}
    for e in oci_config.process.env:
        if "=" in e:
            key, value = e.split("=", 1)
            env[key] = value

    # Parse resource limits
    resources = ResourceLimits()

    if oci_config.linux.resources.memory:
        mem = oci_config.linux.resources.memory
        if "limit" in mem:
            resources.memory_mb = mem["limit"] // (1024 * 1024)

    if oci_config.linux.resources.cpu:
        cpu = oci_config.linux.resources.cpu
        if "quota" in cpu:
            resources.cpu_quota = cpu["quota"]
        if "period" in cpu:
            resources.cpu_period = cpu["period"]

    if oci_config.linux.resources.pids:
        pids = oci_config.linux.resources.pids
        if "limit" in pids:
            resources.max_pids = pids["limit"]

    # Parse namespaces
    namespaces = []
    ns_type_map = {
        "pid": "pid",
        "network": "net",
        "mount": "mnt",
        "ipc": "ipc",
        "uts": "uts",
        "user": "user",
        "cgroup": "cgroup",
    }

    for ns in oci_config.linux.namespaces:
        if ns.type in ns_type_map:
            namespaces.append(ns_type_map[ns.type])

    # Parse capabilities
    caps = []
    if oci_config.process.capabilities:
        # Use bounding or permitted set
        cap_set = (
            oci_config.process.capabilities.get("bounding")
            or oci_config.process.capabilities.get("permitted")
            or []
        )
        caps = cap_set

    # Create container config
    return ContainerConfig(
        rootfs=rootfs,
        command=oci_config.process.args,
        hostname=oci_config.hostname,
        env=env,
        workdir=oci_config.process.cwd,
        resources=resources,
        namespaces=namespaces,
        capabilities=caps,
        seccomp_enabled=bool(oci_config.linux.seccomp),
    )


def generate_oci_config(config: ContainerConfig) -> Dict:
    """
    Generate OCI config.json from ContainerConfig.

    Args:
        config: ContainerConfig instance

    Returns:
        Dictionary suitable for config.json
    """
    # Convert env dict to list
    env_list = [f"{k}={v}" for k, v in config.env.items()]
    if not env_list:
        env_list = ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"]

    # Build namespaces list
    ns_map = {
        "pid": "pid",
        "net": "network",
        "mnt": "mount",
        "ipc": "ipc",
        "uts": "uts",
        "user": "user",
    }
    namespaces = [{"type": ns_map.get(ns, ns)} for ns in config.namespaces]

    # Build resources
    resources = {}
    if config.resources.memory_mb:
        resources["memory"] = {"limit": config.resources.memory_mb * 1024 * 1024}
    if config.resources.cpu_quota:
        resources["cpu"] = {
            "quota": config.resources.cpu_quota,
            "period": config.resources.cpu_period,
        }
    if config.resources.max_pids:
        resources["pids"] = {"limit": config.resources.max_pids}

    oci_config = {
        "ociVersion": "1.0.0",
        "process": {
            "terminal": True,
            "user": {"uid": 0, "gid": 0},
            "args": config.command,
            "env": env_list,
            "cwd": config.workdir,
            "capabilities": {
                "bounding": config.capabilities,
                "effective": config.capabilities,
                "inheritable": config.capabilities,
                "permitted": config.capabilities,
            },
            "noNewPrivileges": True,
        },
        "root": {
            "path": "rootfs",
            "readonly": False,
        },
        "hostname": config.hostname,
        "mounts": [
            {
                "destination": "/proc",
                "type": "proc",
                "source": "proc",
            },
            {
                "destination": "/dev",
                "type": "tmpfs",
                "source": "tmpfs",
                "options": ["nosuid", "strictatime", "mode=755", "size=65536k"],
            },
            {
                "destination": "/sys",
                "type": "sysfs",
                "source": "sysfs",
                "options": ["nosuid", "noexec", "nodev", "ro"],
            },
        ],
        "linux": {
            "namespaces": namespaces,
            "resources": resources,
        },
    }

    return oci_config


def validate_bundle(bundle_path: str) -> List[str]:
    """
    Validate an OCI bundle.

    Args:
        bundle_path: Path to OCI bundle

    Returns:
        List of validation errors (empty if valid)
    """
    errors = []

    if not os.path.isdir(bundle_path):
        errors.append(f"Bundle path is not a directory: {bundle_path}")
        return errors

    config_path = os.path.join(bundle_path, "config.json")
    if not os.path.exists(config_path):
        errors.append("config.json not found in bundle")
        return errors

    try:
        oci_config = load_oci_config(bundle_path)
    except OCIError as e:
        errors.append(str(e))
        return errors

    # Check rootfs exists
    rootfs_path = os.path.join(bundle_path, oci_config.root.path)
    if not os.path.isdir(rootfs_path):
        errors.append(f"Root filesystem not found: {rootfs_path}")

    # Check process has args
    if not oci_config.process.args:
        errors.append("Process args not specified")

    return errors


class OCIRuntime:
    """
    OCI Runtime implementation.

    Example:
        runtime = OCIRuntime()
        errors = runtime.validate("/path/to/bundle")
        if not errors:
            config = runtime.load("/path/to/bundle")
            container_config = runtime.to_container_config(config, "/path/to/bundle")
    """

    def validate(self, bundle_path: str) -> List[str]:
        """Validate an OCI bundle."""
        return validate_bundle(bundle_path)

    def load(self, bundle_path: str) -> OCIConfig:
        """Load OCI configuration from bundle."""
        return load_oci_config(bundle_path)

    def to_container_config(
        self, oci_config: OCIConfig, bundle_path: str
    ) -> ContainerConfig:
        """Convert OCI config to container config."""
        return oci_to_container_config(oci_config, bundle_path)

    def generate(self, config: ContainerConfig) -> Dict:
        """Generate OCI config.json from container config."""
        return generate_oci_config(config)
