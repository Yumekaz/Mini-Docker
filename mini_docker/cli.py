#!/usr/bin/env python3
"""
Command Line Interface for Mini-Docker.

Provides Docker-like CLI commands:
    mini-docker run <rootfs> <command>      - Run a container
    mini-docker exec <container> <command>  - Execute in container
    mini-docker ps                          - List containers
    mini-docker stop <container>            - Stop a container
    mini-docker rm <container>              - Remove a container
    mini-docker logs <container>            - Fetch container logs
    mini-docker inspect <container>         - Inspect a container
    mini-docker run-oci <bundle-path>       - Run OCI bundle
    mini-docker pod <subcommand>            - Pod management
    mini-docker build <path>                - Build an image
    mini-docker images                      - List images
    mini-docker rmi <image>                 - Remove an image
    mini-docker info                        - System information
    mini-docker version                     - Version information
    mini-docker cleanup                     - Clean up resources
"""

import argparse
import json
import os
import shutil
import sys
from dataclasses import asdict
from datetime import datetime
from typing import List, Optional

# Version information
__version__ = "1.0.0"


def create_parser() -> argparse.ArgumentParser:
    """Create argument parser with all commands and options."""
    parser = argparse.ArgumentParser(
        prog="mini-docker",
        description="Mini-Docker: A minimal container runtime built from scratch",
        epilog="For more information, see: https://github.com/Yumekaz/Mini-Docker",
    )

    # Global options
    parser.add_argument(
        "--version", "-v", action="version", version=f"Mini-Docker {__version__}"
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug output")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress output")

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # =========================================================================
    # run command
    # =========================================================================
    run_parser = subparsers.add_parser("run", help="Run a container")
    run_parser.add_argument("rootfs", help="Path to rootfs or image name")
    run_parser.add_argument("cmd", nargs="+", help="Command to run")
    run_parser.add_argument("--name", "-n", help="Container name")
    run_parser.add_argument("--hostname", "-H", help="Container hostname")
    run_parser.add_argument(
        "--no-overlay", action="store_true", help="Use chroot instead of OverlayFS"
    )
    run_parser.add_argument(
        "--cpu", "-c", type=int, help="CPU limit percentage (1-100)"
    )
    run_parser.add_argument("--memory", "-m", help="Memory limit (e.g., 100M, 1G)")
    run_parser.add_argument("--pids", type=int, help="Max number of processes")
    run_parser.add_argument(
        "--pids-limit", type=int, help="Max number of processes (alias for --pids)"
    )
    run_parser.add_argument(
        "--env",
        "-e",
        action="append",
        default=[],
        help="Set environment variable (KEY=VALUE)",
    )
    run_parser.add_argument(
        "--volume",
        "-V",
        action="append",
        default=[],
        help="Bind mount a volume (host:container)",
    )
    run_parser.add_argument(
        "--workdir", "-w", default="/", help="Working directory inside container"
    )
    run_parser.add_argument("--user", "-u", help="User to run as (user or uid:gid)")
    run_parser.add_argument("--net", action="store_true", help="Enable networking")
    run_parser.add_argument("--pod", help="Pod to join")
    run_parser.add_argument(
        "--rootless",
        action="store_true",
        help="Run in rootless mode (no root required)",
    )
    run_parser.add_argument(
        "--detach", "-d", action="store_true", help="Run container in background"
    )
    run_parser.add_argument(
        "--rm", action="store_true", help="Automatically remove container when it exits"
    )
    run_parser.add_argument(
        "--interactive", "-i", action="store_true", help="Keep STDIN open"
    )
    run_parser.add_argument(
        "--tty", "-t", action="store_true", help="Allocate a pseudo-TTY"
    )

    # =========================================================================
    # run-oci command
    # =========================================================================
    oci_parser = subparsers.add_parser("run-oci", help="Run OCI bundle")
    oci_parser.add_argument("bundle", help="Path to OCI bundle directory")
    oci_parser.add_argument("--name", "-n", help="Container name")
    oci_parser.add_argument(
        "--detach", "-d", action="store_true", help="Run container in background"
    )
    oci_parser.add_argument(
        "--rootless", action="store_true", help="Run in rootless mode"
    )

    # =========================================================================
    # exec command
    # =========================================================================
    exec_parser = subparsers.add_parser("exec", help="Execute command in container")
    exec_parser.add_argument("container", help="Container ID or name")
    exec_parser.add_argument("cmd", nargs="+", help="Command to run")
    exec_parser.add_argument("--workdir", "-w", help="Working directory")
    exec_parser.add_argument(
        "--env",
        "-e",
        action="append",
        default=[],
        help="Set environment variable (KEY=VALUE)",
    )
    exec_parser.add_argument("--user", "-u", help="User to run as (user or uid:gid)")
    exec_parser.add_argument(
        "--interactive", "-i", action="store_true", help="Keep STDIN open"
    )
    exec_parser.add_argument(
        "--tty", "-t", action="store_true", help="Allocate a pseudo-TTY"
    )

    # =========================================================================
    # ps command
    # =========================================================================
    ps_parser = subparsers.add_parser("ps", help="List containers")
    ps_parser.add_argument(
        "--all",
        "-a",
        action="store_true",
        help="Show all containers (including stopped)",
    )
    ps_parser.add_argument(
        "--quiet", "-q", action="store_true", help="Only display container IDs"
    )
    ps_parser.add_argument(
        "--format",
        "-f",
        choices=["table", "json"],
        default="table",
        help="Output format",
    )

    # =========================================================================
    # logs command
    # =========================================================================
    logs_parser = subparsers.add_parser("logs", help="Fetch container logs")
    logs_parser.add_argument("container", help="Container ID or name")
    logs_parser.add_argument(
        "--follow", "-f", action="store_true", help="Follow log output"
    )
    logs_parser.add_argument(
        "--tail", "-n", type=int, help="Number of lines to show from end"
    )
    logs_parser.add_argument(
        "--timestamps", "-t", action="store_true", help="Show timestamps"
    )

    # =========================================================================
    # inspect command
    # =========================================================================
    inspect_parser = subparsers.add_parser("inspect", help="Inspect a container")
    inspect_parser.add_argument(
        "container", nargs="+", help="Container ID(s) or name(s)"
    )
    inspect_parser.add_argument(
        "--format", "-f", choices=["json", "yaml"], default="json", help="Output format"
    )

    # =========================================================================
    # stop command
    # =========================================================================
    stop_parser = subparsers.add_parser("stop", help="Stop a container")
    stop_parser.add_argument("container", nargs="+", help="Container ID(s) or name(s)")
    stop_parser.add_argument(
        "--time", "-t", type=int, default=10, help="Seconds to wait before SIGKILL"
    )
    stop_parser.add_argument(
        "--force", "-f", action="store_true", help="Force stop (SIGKILL immediately)"
    )

    # =========================================================================
    # rm command
    # =========================================================================
    rm_parser = subparsers.add_parser("rm", help="Remove a container")
    rm_parser.add_argument("container", nargs="+", help="Container ID(s) or name(s)")
    rm_parser.add_argument(
        "--force", "-f", action="store_true", help="Force removal of running container"
    )
    rm_parser.add_argument(
        "--volumes", "-v", action="store_true", help="Remove associated volumes"
    )

    # =========================================================================
    # pod commands
    # =========================================================================
    pod_parser = subparsers.add_parser("pod", help="Pod management")
    pod_subparsers = pod_parser.add_subparsers(dest="pod_command")

    # pod create
    pod_create = pod_subparsers.add_parser("create", help="Create a pod")
    pod_create.add_argument("name", nargs="?", help="Pod name")
    pod_create.add_argument("--hostname", help="Pod hostname")
    pod_create.add_argument(
        "--net", action="store_true", help="Enable networking for pod"
    )

    # pod add (add container to pod)
    pod_add = pod_subparsers.add_parser("add", help="Add container to pod")
    pod_add.add_argument("pod", help="Pod ID or name")
    pod_add.add_argument("--name", "-n", help="Container name")
    pod_add.add_argument("rootfs", help="Path to rootfs")
    pod_add.add_argument("cmd", nargs="+", help="Command to run")

    # pod ls
    pod_list = pod_subparsers.add_parser("ls", help="List pods")
    pod_list.add_argument(
        "--quiet", "-q", action="store_true", help="Only display pod IDs"
    )
    pod_list.add_argument(
        "--format",
        "-f",
        choices=["table", "json"],
        default="table",
        help="Output format",
    )

    # pod ps (list containers in pod)
    pod_ps = pod_subparsers.add_parser("ps", help="List containers in pod")
    pod_ps.add_argument("pod", nargs="?", help="Pod ID or name (optional)")

    # pod rm
    pod_rm = pod_subparsers.add_parser("rm", help="Remove a pod")
    pod_rm.add_argument("pod", nargs="+", help="Pod ID(s) or name(s)")
    pod_rm.add_argument(
        "--force", "-f", action="store_true", help="Force removal of running pod"
    )

    # pod inspect
    pod_inspect = pod_subparsers.add_parser("inspect", help="Inspect a pod")
    pod_inspect.add_argument("pod", help="Pod ID or name")
    pod_inspect.add_argument(
        "--format", "-f", choices=["json", "yaml"], default="json", help="Output format"
    )

    # =========================================================================
    # build command
    # =========================================================================
    build_parser = subparsers.add_parser("build", help="Build an image")
    build_parser.add_argument("path", help="Path to build directory")
    build_parser.add_argument("--tag", "-t", help="Image name:tag")
    build_parser.add_argument(
        "--file", "-f", help="Build file name", default="Imagefile"
    )
    build_parser.add_argument(
        "--no-cache", action="store_true", help="Do not use cache when building"
    )

    # =========================================================================
    # images command
    # =========================================================================
    images_parser = subparsers.add_parser("images", help="List images")
    images_parser.add_argument(
        "--quiet", "-q", action="store_true", help="Only display image IDs"
    )
    images_parser.add_argument(
        "--format",
        "-f",
        choices=["table", "json"],
        default="table",
        help="Output format",
    )

    # =========================================================================
    # rmi command (NEW)
    # =========================================================================
    rmi_parser = subparsers.add_parser("rmi", help="Remove an image")
    rmi_parser.add_argument("image", nargs="+", help="Image ID(s) or name(s)")
    rmi_parser.add_argument("--force", "-f", action="store_true", help="Force removal")

    # =========================================================================
    # info command (NEW)
    # =========================================================================
    info_parser = subparsers.add_parser("info", help="Display system information")
    info_parser.add_argument(
        "--format", "-f", choices=["text", "json"], default="text", help="Output format"
    )

    # =========================================================================
    # version command (NEW)
    # =========================================================================
    version_parser = subparsers.add_parser("version", help="Show version information")
    version_parser.add_argument(
        "--format", "-f", choices=["text", "json"], default="text", help="Output format"
    )

    # =========================================================================
    # cleanup command (NEW)
    # =========================================================================
    cleanup_parser = subparsers.add_parser("cleanup", help="Clean up unused resources")
    cleanup_parser.add_argument(
        "--all", "-a", action="store_true", help="Remove all unused data"
    )
    cleanup_parser.add_argument(
        "--containers", action="store_true", help="Remove stopped containers"
    )
    cleanup_parser.add_argument(
        "--images", action="store_true", help="Remove unused images"
    )
    cleanup_parser.add_argument(
        "--volumes", action="store_true", help="Remove unused volumes"
    )
    cleanup_parser.add_argument(
        "--force", "-f", action="store_true", help="Do not prompt for confirmation"
    )

    return parser


def parse_memory_limit(memory_str: str) -> int:
    """Parse memory limit string (e.g., '100M', '1G') to bytes."""
    if not memory_str:
        return 0

    memory_str = memory_str.upper().strip()

    multipliers = {
        "B": 1,
        "K": 1024,
        "KB": 1024,
        "M": 1024 * 1024,
        "MB": 1024 * 1024,
        "G": 1024 * 1024 * 1024,
        "GB": 1024 * 1024 * 1024,
    }

    for suffix, multiplier in sorted(multipliers.items(), key=lambda x: -len(x[0])):
        if memory_str.endswith(suffix):
            try:
                value = int(memory_str[: -len(suffix)])
                return value * multiplier
            except ValueError:
                break

    # Try parsing as plain integer (bytes)
    try:
        return int(memory_str)
    except ValueError:
        raise ValueError(f"Invalid memory format: {memory_str}")


def parse_user(user_str: str) -> tuple:
    """Parse user string (e.g., 'nobody', '1000', '1000:1000') to (uid, gid)."""
    if not user_str:
        return None, None

    if ":" in user_str:
        parts = user_str.split(":", 1)
        uid = int(parts[0]) if parts[0].isdigit() else None
        gid = int(parts[1]) if parts[1].isdigit() else None
        return uid, gid
    elif user_str.isdigit():
        return int(user_str), None
    else:
        # Try to look up user by name
        import pwd

        try:
            pw = pwd.getpwnam(user_str)
            return pw.pw_uid, pw.pw_gid
        except KeyError:
            return None, None


def parse_volume(volume_str: str) -> tuple:
    """Parse volume string (e.g., '/host:/container:ro') to (host, container, mode)."""
    parts = volume_str.split(":")
    if len(parts) == 2:
        return parts[0], parts[1], "rw"
    elif len(parts) == 3:
        return parts[0], parts[1], parts[2]
    else:
        raise ValueError(f"Invalid volume format: {volume_str}")


# =============================================================================
# Command Handlers
# =============================================================================


def cmd_run(args: argparse.Namespace) -> int:
    """Handle run command."""
    from mini_docker.container import Container, ContainerError

    # Parse environment variables
    env = {}
    for e in args.env:
        if "=" in e:
            key, value = e.split("=", 1)
            env[key] = value

    # Parse memory limit
    memory_mb = None
    if args.memory:
        try:
            memory_bytes = parse_memory_limit(args.memory)
            memory_mb = memory_bytes // (1024 * 1024)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    # Parse PIDs limit (support both --pids and --pids-limit)
    max_pids = args.pids or args.pids_limit

    # Parse volumes
    volumes = []
    for v in args.volume:
        try:
            host_path, container_path, mode = parse_volume(v)
            volumes.append(
                {"host": host_path, "container": container_path, "mode": mode}
            )
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    # Parse user
    uid, gid = parse_user(args.user) if args.user else (None, None)

    # Resolve rootfs path (handle image names)
    rootfs_path = args.rootfs
    if not os.path.exists(rootfs_path):
        from mini_docker.image_builder import resolve_image_path

        resolved = resolve_image_path(rootfs_path)
        if resolved:
            print(f"Using image: {rootfs_path}")
            rootfs_path = resolved
        else:
            print(f"Error: Rootfs or image not found: {args.rootfs}", file=sys.stderr)
            return 1

    container = Container()

    try:
        # Create container
        config = container.create(
            rootfs=rootfs_path,
            command=args.cmd,
            name=args.name,
            hostname=args.hostname,
            use_overlay=not args.no_overlay,
            cpu_quota=args.cpu,
            memory_mb=memory_mb,
            max_pids=max_pids,
            env=env,
            workdir=args.workdir,
            pod_id=args.pod,
            rootless=args.rootless,
            network=args.net,
            volumes=volumes,
            uid=uid,
            gid=gid,
            auto_remove=args.rm,
            interactive=args.interactive,
            tty=args.tty,
        )

        print(f"Created container: {config.id[:12]}")

        # Start container
        pid = container.start(config.id)

        if args.detach:
            print(f"Container started with PID {pid}")
            return 0
        else:
            # Wait for container to exit
            try:
                _, status = os.waitpid(pid, 0)
                exit_code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else 1
            except ChildProcessError:
                exit_code = 0

            from mini_docker.metadata import update_container_status

            update_container_status(config.id, "stopped", exit_code=exit_code)

            # Auto-remove if requested
            if args.rm:
                try:
                    container.remove(config.id)
                except Exception:
                    pass

            return exit_code

    except ContainerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted")
        return 130


def cmd_run_oci(args: argparse.Namespace) -> int:
    """Handle run-oci command."""
    from mini_docker.container import Container, ContainerError
    from mini_docker.oci import OCIError, OCIRuntime

    oci = OCIRuntime()
    container = Container()

    try:
        # Validate bundle
        errors = oci.validate(args.bundle)
        if errors:
            for err in errors:
                print(f"Error: {err}", file=sys.stderr)
            return 1

        # Load OCI config
        oci_config = oci.load(args.bundle)

        # Convert to container config
        config = oci.to_container_config(oci_config, args.bundle)
        config.rootless = args.rootless

        # Set name if provided
        if args.name:
            config.name = args.name

        # Save and start
        from mini_docker.metadata import save_container_config

        save_container_config(config)

        print(f"Created container: {config.id[:12]}")

        pid = container.start(config.id)

        if args.detach:
            print(f"Container started with PID {pid}")
            return 0
        else:
            try:
                _, status = os.waitpid(pid, 0)
                exit_code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else 1
            except ChildProcessError:
                exit_code = 0
            return exit_code

    except (ContainerError, OCIError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_exec(args: argparse.Namespace) -> int:
    """Handle exec command."""
    from mini_docker.container import Container, ContainerError

    container = Container()

    # Parse environment variables
    env = {}
    for e in args.env:
        if "=" in e:
            key, value = e.split("=", 1)
            env[key] = value

    # Parse user
    uid, gid = parse_user(args.user) if args.user else (None, None)

    try:
        exit_code = container.exec(
            args.container,
            args.cmd,
            workdir=args.workdir,
            env=env,
            uid=uid,
            gid=gid,
            interactive=args.interactive,
            tty=args.tty,
        )
        return exit_code
    except ContainerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_ps(args: argparse.Namespace) -> int:
    """Handle ps command."""
    from mini_docker.container import Container

    container = Container()
    containers = container.list(all_containers=args.all)

    if args.quiet:
        # Only print container IDs
        for c in containers:
            print(c.id[:12])
    elif args.format == "json":
        # JSON output
        data = [asdict(c) for c in containers]
        print(json.dumps(data, indent=2, default=str))
    else:
        # Table output
        print(
            f"{'CONTAINER ID':<14} {'NAME':<20} {'STATUS':<12} {'COMMAND':<30} {'CREATED'}"
        )

        for c in containers:
            container_id = c.id[:12]
            name = (c.name or "")[:20]
            status = c.status
            command = " ".join(c.command)[:30]
            created = datetime.fromtimestamp(c.created_at).strftime("%Y-%m-%d %H:%M")

            print(f"{container_id:<14} {name:<20} {status:<12} {command:<30} {created}")

    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    """Handle logs command."""
    from mini_docker.container import Container, ContainerError

    container = Container()

    try:
        container.logs(
            args.container,
            follow=args.follow,
            tail=args.tail,
            timestamps=args.timestamps,
        )
        return 0
    except ContainerError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_inspect(args: argparse.Namespace) -> int:
    """Handle inspect command."""
    from mini_docker.container import Container

    container = Container()
    results = []

    for container_id in args.container:
        config = container.inspect(container_id)
        if not config:
            print(f"Error: Container not found: {container_id}", file=sys.stderr)
            return 1
        results.append(asdict(config))

    if args.format == "yaml":
        try:
            import yaml

            print(yaml.dump(results, default_flow_style=False))
        except ImportError:
            print("YAML format requires PyYAML. Using JSON instead.")
            print(json.dumps(results, indent=2, default=str))
    else:
        print(json.dumps(results, indent=2, default=str))

    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    """Handle stop command."""
    from mini_docker.container import Container, ContainerError

    container = Container()
    exit_code = 0

    for container_id in args.container:
        try:
            if args.force:
                container.stop(container_id, timeout=0)  # Immediate SIGKILL
            else:
                container.stop(container_id, timeout=args.time)
            print(f"Stopped: {container_id}")
        except ContainerError as e:
            print(f"Error stopping {container_id}: {e}", file=sys.stderr)
            exit_code = 1

    return exit_code


def cmd_rm(args: argparse.Namespace) -> int:
    """Handle rm command."""
    from mini_docker.container import Container, ContainerError

    container = Container()
    exit_code = 0

    for container_id in args.container:
        try:
            container.remove(
                container_id, force=args.force, remove_volumes=args.volumes
            )
            print(f"Removed: {container_id}")
        except ContainerError as e:
            print(f"Error removing {container_id}: {e}", file=sys.stderr)
            exit_code = 1

    return exit_code


def cmd_pod(args: argparse.Namespace) -> int:
    """Handle pod commands."""
    from mini_docker.pod import PodError, PodManager, load_pod_config

    pods = PodManager()

    if args.pod_command == "create":
        try:
            pod = pods.create(
                name=args.name,
                hostname=getattr(args, "hostname", None),
                network=getattr(args, "net", False),
            )
            print(f"Created pod: {pod.id[:12]} ({pod.name})")
            return 0
        except PodError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    elif args.pod_command == "add":
        # Add container to pod
        from mini_docker.container import Container, ContainerError

        container = Container()
        try:
            config = container.create(
                rootfs=args.rootfs,
                command=args.cmd,
                name=args.name,
                pod_id=args.pod,
            )
            print(f"Added container {config.id[:12]} to pod {args.pod}")

            pid = container.start(config.id)
            print(f"Container started with PID {pid}")
            return 0
        except ContainerError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    elif args.pod_command == "ls":
        pod_list = pods.list()

        if getattr(args, "quiet", False):
            for p in pod_list:
                print(p.id[:12])
        elif getattr(args, "format", "table") == "json":
            data = [asdict(p) for p in pod_list]
            print(json.dumps(data, indent=2, default=str))
        else:
            print(f"{'POD ID':<14} {'NAME':<20} {'CONTAINERS':<12} {'STATUS'}")
            for p in pod_list:
                pod_id = p.id[:12]
                name = (p.name or "")[:20]
                num_containers = len(p.containers)
                status = p.status
                print(f"{pod_id:<14} {name:<20} {num_containers:<12} {status}")
        return 0

    elif args.pod_command == "ps":
        # List containers in pod(s)
        from mini_docker.container import Container

        container = Container()
        all_containers = container.list(all_containers=True)

        pod_filter = getattr(args, "pod", None)

        print(f"{'CONTAINER ID':<14} {'POD':<14} {'NAME':<20} {'STATUS'}")
        for c in all_containers:
            if c.pod_id:
                if pod_filter and not c.pod_id.startswith(pod_filter):
                    continue
                print(
                    f"{c.id[:12]:<14} {c.pod_id[:12]:<14} {(c.name or '')[:20]:<20} {c.status}"
                )
        return 0

    elif args.pod_command == "rm":
        exit_code = 0
        force = getattr(args, "force", False)

        for pod_id in args.pod:
            try:
                if pods.delete(pod_id, force=force):
                    print(f"Removed pod: {pod_id}")
                else:
                    print(f"Error: Pod not found: {pod_id}", file=sys.stderr)
                    exit_code = 1
            except PodError as e:
                print(f"Error: {e}", file=sys.stderr)
                exit_code = 1
        return exit_code

    elif args.pod_command == "inspect":
        pod = load_pod_config(args.pod)
        if not pod:
            print(f"Error: Pod not found: {args.pod}", file=sys.stderr)
            return 1

        data = asdict(pod)
        if getattr(args, "format", "json") == "yaml":
            try:
                import yaml

                print(yaml.dump(data, default_flow_style=False))
            except ImportError:
                print(json.dumps(data, indent=2, default=str))
        else:
            print(json.dumps(data, indent=2, default=str))
        return 0

    else:
        print("Usage: mini-docker pod <create|add|ls|ps|rm|inspect>")
        return 1


def cmd_build(args: argparse.Namespace) -> int:
    """Handle build command."""
    from mini_docker.image_builder import BuildError, ImageBuilder

    builder = ImageBuilder()

    # Determine build file path
    if os.path.isdir(args.path):
        build_file = os.path.join(args.path, args.file)
    else:
        build_file = args.path

    try:
        image_id = builder.build(
            build_file,
            name=args.tag or "",
            no_cache=args.no_cache,
        )
        print(f"Successfully built: {image_id[:12]}")
        if args.tag:
            print(f"Tagged: {args.tag}")
        return 0
    except BuildError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_images(args: argparse.Namespace) -> int:
    """Handle images command."""
    from mini_docker.image_builder import list_images

    images = list_images()

    if args.quiet:
        for img in images:
            print(img.id[:12])
    elif args.format == "json":
        data = [asdict(img) for img in images]
        print(json.dumps(data, indent=2, default=str))
    else:
        print(f"{'IMAGE ID':<14} {'NAME':<30} {'TAG':<12} {'LAYERS':<8} {'SIZE'}")

        for img in images:
            image_id = img.id[:12]
            name = (img.name or "<none>")[:30]
            tag = (img.tag or "latest")[:12]
            layers = len(img.layers) if hasattr(img, "layers") else 0
            size = getattr(img, "size", "N/A")
            print(f"{image_id:<14} {name:<30} {tag:<12} {layers:<8} {size}")

    return 0


def cmd_rmi(args: argparse.Namespace) -> int:
    """Handle rmi (remove image) command."""
    from mini_docker.image_builder import ImageError, remove_image

    exit_code = 0

    for image_id in args.image:
        try:
            remove_image(image_id, force=args.force)
            print(f"Removed: {image_id}")
        except ImageError as e:
            print(f"Error removing {image_id}: {e}", file=sys.stderr)
            exit_code = 1
        except Exception as e:
            print(f"Error removing {image_id}: {e}", file=sys.stderr)
            exit_code = 1

    return exit_code


def cmd_info(args: argparse.Namespace) -> int:
    """Handle info command - display system information."""
    import platform

    # Gather system information
    info = {
        "Version": __version__,
        "Python": platform.python_version(),
        "Platform": platform.platform(),
        "Kernel": platform.release(),
        "Architecture": platform.machine(),
    }

    # Check cgroups version
    if os.path.exists("/sys/fs/cgroup/cgroup.controllers"):
        info["Cgroups"] = "v2"
    elif os.path.exists("/sys/fs/cgroup/memory"):
        info["Cgroups"] = "v1"
    else:
        info["Cgroups"] = "unknown"

    # Check available namespaces
    namespaces = []
    ns_path = "/proc/self/ns"
    if os.path.exists(ns_path):
        namespaces = os.listdir(ns_path)
    info["Namespaces"] = ", ".join(sorted(namespaces))

    # Check seccomp
    seccomp_status = "unknown"
    try:
        with open("/proc/sys/kernel/seccomp/actions_avail", "r") as f:
            seccomp_status = "enabled"
    except FileNotFoundError:
        if os.path.exists("/proc/self/seccomp"):
            seccomp_status = "enabled"
        else:
            seccomp_status = "disabled"
    info["Seccomp"] = seccomp_status

    # Count containers and images
    from mini_docker.container import Container
    from mini_docker.image_builder import list_images

    container = Container()
    all_containers = container.list(all_containers=True)
    running = sum(1 for c in all_containers if c.status == "running")
    stopped = len(all_containers) - running

    info["Containers"] = f"{running} running, {stopped} stopped"
    info["Images"] = str(len(list_images()))

    # Storage path
    info["Storage"] = os.path.expanduser("~/.mini-docker")

    if args.format == "json":
        print(json.dumps(info, indent=2))
    else:
        print("Mini-Docker System Information")
        print("=" * 40)
        for key, value in info.items():
            print(f"{key + ':':<20} {value}")

    return 0


def cmd_version(args: argparse.Namespace) -> int:
    """Handle version command."""
    import platform

    version_info = {
        "Mini-Docker": __version__,
        "Python": platform.python_version(),
        "Kernel": platform.release(),
        "OS": platform.system(),
        "Architecture": platform.machine(),
    }

    if args.format == "json":
        print(json.dumps(version_info, indent=2))
    else:
        print(f"Mini-Docker version {__version__}")
        print(f"Python version {platform.python_version()}")
        print(f"Kernel {platform.release()}")
        print(f"OS/Arch: {platform.system()}/{platform.machine()}")

    return 0


def cmd_cleanup(args: argparse.Namespace) -> int:
    """Handle cleanup command - remove unused resources."""
    from mini_docker.container import Container
    from mini_docker.image_builder import list_images, remove_image

    # Confirm unless --force
    if not args.force and not args.all and not args.containers and not args.images:
        print("This will remove:")
        print("  - All stopped containers")
        print("  - All unused images")
        print("  - All unused volumes")
        print("")
        response = input("Are you sure? [y/N] ")
        if response.lower() not in ("y", "yes"):
            print("Cancelled.")
            return 0
        args.all = True

    removed_containers = 0
    removed_images = 0
    removed_volumes = 0
    reclaimed_space = 0

    container = Container()

    # Clean containers
    if args.all or args.containers:
        all_containers = container.list(all_containers=True)
        for c in all_containers:
            if c.status != "running":
                try:
                    container.remove(c.id)
                    removed_containers += 1
                except Exception:
                    pass

    # Clean images
    if args.all or args.images:
        images = list_images()
        # For now, just report - don't remove images in use
        # In a real implementation, we'd track which images are used
        pass

    # Clean volumes
    if args.all or args.volumes:
        # Clean up overlay directories
        storage_path = os.path.expanduser("~/.mini-docker")
        overlay_path = os.path.join(storage_path, "overlay")
        if os.path.exists(overlay_path):
            for item in os.listdir(overlay_path):
                item_path = os.path.join(overlay_path, item)
                if os.path.isdir(item_path):
                    try:
                        shutil.rmtree(item_path)
                        removed_volumes += 1
                    except Exception:
                        pass

    print(f"Removed {removed_containers} container(s)")
    print(f"Removed {removed_images} image(s)")
    print(f"Removed {removed_volumes} volume(s)")

    return 0


# =============================================================================
# Main Entry Point
# =============================================================================


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    # Dispatch to command handler
    handlers = {
        "run": cmd_run,
        "run-oci": cmd_run_oci,
        "exec": cmd_exec,
        "ps": cmd_ps,
        "logs": cmd_logs,
        "inspect": cmd_inspect,
        "stop": cmd_stop,
        "rm": cmd_rm,
        "pod": cmd_pod,
        "build": cmd_build,
        "images": cmd_images,
        "rmi": cmd_rmi,
        "info": cmd_info,
        "version": cmd_version,
        "cleanup": cmd_cleanup,
    }

    handler = handlers.get(args.command)
    if handler:
        try:
            return handler(args)
        except KeyboardInterrupt:
            print("\nInterrupted")
            return 130
        except Exception as e:
            if getattr(args, "debug", False):
                raise
            print(f"Error: {e}", file=sys.stderr)
            return 1
    else:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
