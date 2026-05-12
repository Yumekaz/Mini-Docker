#!/usr/bin/env python3
"""
Pod support for Mini-Docker.
"""

import json
import os
import signal
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from mini_docker.utils import (
    PODS_PATH,
    ensure_directories,
    generate_container_id,
    generate_container_name,
    is_process_alive,
)


class PodError(Exception):
    """Exception raised during pod operations."""

    pass


@dataclass
class PodConfig:
    """Pod configuration."""

    id: str = ""
    name: str = ""
    containers: List[str] = field(default_factory=list)
    infra_pid: Optional[int] = None
    ip_address: Optional[str] = None
    shared_namespaces: List[str] = field(default_factory=lambda: ["net", "ipc", "uts"])
    hostname: str = ""
    network: Optional[bool] = None
    status: str = "created"
    created_at: float = field(default_factory=time.time)

    def __post_init__(self):
        if not self.id:
            self.id = generate_container_id()
        if not self.name:
            self.name = generate_container_name()
        if not self.hostname:
            self.hostname = self.name

        if self.network is None:
            self.network = "net" in self.shared_namespaces
        elif self.network:
            if "net" not in self.shared_namespaces:
                self.shared_namespaces.insert(0, "net")
        else:
            self.shared_namespaces = [
                ns for ns in self.shared_namespaces if ns != "net"
            ]


def get_pod_path(pod_id: str) -> str:
    """Get path to pod directory."""
    return os.path.join(PODS_PATH, pod_id)


def _pod_config_path(pod_id: str) -> str:
    return os.path.join(get_pod_path(pod_id), "config.json")


def _list_pod_ids() -> List[str]:
    if not os.path.exists(PODS_PATH):
        return []
    return sorted(os.listdir(PODS_PATH))


def _read_pod_data(pod_id: str) -> Optional[Dict]:
    config_path = _pod_config_path(pod_id)
    if not os.path.exists(config_path):
        return None

    try:
        with open(config_path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _refresh_pod_state(config: PodConfig) -> PodConfig:
    if config.infra_pid and not is_process_alive(config.infra_pid):
        config.infra_pid = None
        config.status = "stopped"
        save_pod_config(config)

    return config


def _load_pod_config_by_id(
    pod_id: str, refresh_state: bool = True
) -> Optional[PodConfig]:
    data = _read_pod_data(pod_id)
    if not data:
        return None

    try:
        config = PodConfig(**data)
    except TypeError:
        return None

    if refresh_state:
        config = _refresh_pod_state(config)

    return config


def pod_exists(pod_id: str) -> bool:
    """Check if pod exists."""
    return os.path.exists(_pod_config_path(pod_id))


def save_pod_config(config: PodConfig) -> str:
    """Save pod configuration."""
    ensure_directories()

    pod_path = get_pod_path(config.id)
    os.makedirs(pod_path, exist_ok=True)

    config_path = _pod_config_path(config.id)
    with open(config_path, "w") as f:
        json.dump(asdict(config), f, indent=2)

    return config_path


def load_pod_config(pod_id: str) -> Optional[PodConfig]:
    """Load pod configuration by full ID, prefix, or name."""
    full_id = find_pod_id(pod_id)
    if not full_id:
        return None
    return _load_pod_config_by_id(full_id)


def delete_pod_config(pod_id: str) -> bool:
    """Delete pod configuration."""
    import shutil

    full_id = find_pod_id(pod_id)
    if not full_id:
        return False

    config = _load_pod_config_by_id(full_id, refresh_state=False)
    if config and config.infra_pid:
        try:
            os.kill(config.infra_pid, signal.SIGTERM)
            time.sleep(0.5)
            os.kill(config.infra_pid, signal.SIGKILL)
        except OSError:
            pass

    try:
        shutil.rmtree(get_pod_path(full_id))
        return True
    except (OSError, IOError):
        return False


def list_pods() -> List[PodConfig]:
    """List all pods."""
    ensure_directories()

    pods = []
    for pod_id in _list_pod_ids():
        config = _load_pod_config_by_id(pod_id)
        if config:
            pods.append(config)

    pods.sort(key=lambda p: p.created_at, reverse=True)
    return pods


def find_pod_id(prefix: str) -> Optional[str]:
    """Find pod by ID prefix or name."""
    if not prefix:
        return None

    if pod_exists(prefix):
        return prefix

    pod_ids = _list_pod_ids()

    for pod_id in pod_ids:
        if pod_id.startswith(prefix):
            return pod_id

    for pod_id in pod_ids:
        data = _read_pod_data(pod_id)
        if data and data.get("name") == prefix:
            return pod_id

    return None


def add_container_to_pod(pod_id: str, container_id: str) -> bool:
    """Add a container to a pod."""
    full_id = find_pod_id(pod_id)
    if not full_id:
        return False

    config = _load_pod_config_by_id(full_id, refresh_state=False)
    if not config:
        return False

    if container_id not in config.containers:
        config.containers.append(container_id)
        save_pod_config(config)

    return True


def remove_container_from_pod(pod_id: str, container_id: str) -> bool:
    """Remove a container from a pod."""
    full_id = find_pod_id(pod_id)
    if not full_id:
        return False

    config = _load_pod_config_by_id(full_id, refresh_state=False)
    if not config:
        return False

    if container_id in config.containers:
        config.containers.remove(container_id)
        save_pod_config(config)

    return True


class PodManager:
    """Pod manager for Mini-Docker."""

    def __init__(self):
        ensure_directories()

    def create(self, name: Optional[str] = None, **kwargs) -> PodConfig:
        config = PodConfig(name=name or "", **kwargs)
        save_pod_config(config)
        self._start_infra_container(config)
        return config

    def _start_infra_container(self, config: PodConfig) -> None:
        """Start the infra container that holds shared namespaces."""
        pid = os.fork()
        if pid == 0:
            try:
                from mini_docker.namespaces import create_namespaces, sethostname

                create_namespaces(config.shared_namespaces, hostname=config.hostname)

                if config.hostname:
                    try:
                        sethostname(config.hostname)
                    except Exception:
                        pass

                while True:
                    time.sleep(3600)
            except Exception:
                os._exit(1)
        else:
            config.infra_pid = pid
            config.status = "running"
            save_pod_config(config)

    def get(self, pod_id: str) -> Optional[PodConfig]:
        return load_pod_config(pod_id)

    def update(self, config: PodConfig) -> None:
        save_pod_config(config)

    def delete(self, pod_id: str, force: bool = False) -> bool:
        config = load_pod_config(pod_id)
        if not config:
            return False

        if config.containers and not force:
            raise PodError(
                f"Pod has {len(config.containers)} containers. Use --force to remove."
            )

        return delete_pod_config(pod_id)

    def list(self) -> List[PodConfig]:
        return list_pods()

    def add_container(self, pod_id: str, container_id: str) -> bool:
        return add_container_to_pod(pod_id, container_id)

    def remove_container(self, pod_id: str, container_id: str) -> bool:
        return remove_container_from_pod(pod_id, container_id)

    def set_infra_pid(self, pod_id: str, pid: int) -> bool:
        config = load_pod_config(pod_id)
        if not config:
            return False
        config.infra_pid = pid
        save_pod_config(config)
        return True

    def get_shared_ns_paths(self, pod_id: str) -> Dict[str, str]:
        config = load_pod_config(pod_id)
        if not config or not config.infra_pid:
            return {}

        paths = {}
        for ns in config.shared_namespaces:
            ns_path = f"/proc/{config.infra_pid}/ns/{ns}"
            if os.path.exists(ns_path):
                paths[ns] = ns_path

        return paths
