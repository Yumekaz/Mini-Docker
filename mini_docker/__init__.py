"""
Mini-Docker: A fully functional container runtime built from scratch.

This runtime implements:
- Linux namespaces (PID, UTS, Mount, IPC, Network, User)
- Cgroups v2 resource limits
- OverlayFS filesystem isolation
- Seccomp syscall filtering
- Linux capabilities
- Container networking with veth pairs
- OCI runtime specification support
- Pod support for shared namespaces
- Rootless container execution

Author: Mihir Swarnkar
License: MIT
"""

from .capabilities import Capabilities
from .cgroups import Cgroup
from .container import Container
from .image_builder import ImageBuilder
from .namespaces import Namespace
from .network import Network
from .oci import OCIRuntime as OCI
from .pod import PodManager as Pod
from .seccomp import Seccomp

__version__ = "1.0.0"
__all__ = [
    "Container",
    "Namespace",
    "Cgroup",
    "Network",
    "Seccomp",
    "Capabilities",
    "OCI",
    "Pod",
    "ImageBuilder",
]
