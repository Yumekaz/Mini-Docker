#!/usr/bin/env python3
"""
Daemon / REST API Server for Mini-Docker.

This exposes the Mini-Docker runtime through a Unix Domain Socket,
allowing external PaaS controllers to programmatically interact with
the container lifecycle (create, start, stop, rm, ps, logs, etc.).
"""

import json
import os
import socketserver
import urllib.parse
from http.server import BaseHTTPRequestHandler
from typing import Dict, Any

from mini_docker.container import Container, ContainerError
from mini_docker.metadata import asdict
from mini_docker.utils import ensure_directories, DEFAULT_SOCKET_PATH


class UnixSocketHTTPServer(socketserver.UnixStreamServer):
    """A HTTP server that listens on a Unix domain socket."""

    def get_request(self):
        request, client_address = super().get_request()
        # BaseHTTPRequestHandler expects the client_address to be a tuple of strings
        return request, ["local", 0]


class DockerAPIHandler(BaseHTTPRequestHandler):
    """
    HTTP Request handler for the Mini-Docker API.
    Routes requests to the underlying Container manager.
    """

    def __init__(self, *args, **kwargs):
        # We instantiate Container here to interface with the core logic
        self.container_manager = Container()
        super().__init__(*args, **kwargs)

    def send_json_response(self, status_code: int, payload: Any):
        """Helper to send JSON HTTP responses."""
        self.send_response(status_code)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode("utf-8"))

    def send_error_response(self, status_code: int, message: str):
        """Helper to send error responses."""
        self.send_json_response(status_code, {"error": message})

    def parse_body(self) -> Dict[str, Any]:
        """Helper to parse JSON request bodies."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            return {}
        post_data = self.rfile.read(content_length)
        try:
            return json.loads(post_data.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path == "/containers/json":
            # List containers
            containers = self.container_manager.list(all_containers=True)
            self.send_json_response(200, [asdict(c) for c in containers])
            return

        elif path.startswith("/containers/") and path.endswith("/json"):
            # Inspect container
            container_id = path.split("/")[2]
            config = self.container_manager.inspect(container_id)
            if config:
                self.send_json_response(200, asdict(config))
            else:
                self.send_error_response(404, "Container not found")
            return

        elif path == "/info":
            # System info
            self.send_json_response(
                200, {"version": "1.0.0", "system": "Mini-Docker API"}
            )
            return

        self.send_error_response(404, "Not Found")

    def do_POST(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path == "/containers/create":
            body = self.parse_body()
            try:
                # Basic mapping from Docker API body to mini_docker kwargs
                rootfs = body.get("Image", "./rootfs")
                command = body.get("Cmd", ["/bin/sh"])
                name = body.get("name")

                # Parse host config
                host_config = body.get("HostConfig", {})

                # Handle port bindings simply
                port_bindings = host_config.get("PortBindings", {})
                ports = []
                for container_port_proto, host_bindings in port_bindings.items():
                    container_port = container_port_proto.split("/")[0]
                    for binding in host_bindings:
                        host_port = binding.get("HostPort")
                        if host_port:
                            ports.append(f"{host_port}:{container_port}")

                config = self.container_manager.create(
                    rootfs=rootfs,
                    command=command,
                    name=name,
                    ports=ports if ports else None,
                    detach=True,  # Daemon creations are inherently detached from the socket
                )
                self.send_json_response(201, {"Id": config.id})
            except Exception as e:
                self.send_error_response(500, str(e))
            return

        elif path.startswith("/containers/") and path.endswith("/start"):
            container_id = path.split("/")[2]
            try:
                pid = self.container_manager.start(container_id)
                self.send_json_response(
                    204, {"message": f"Started container {container_id} with PID {pid}"}
                )
            except ContainerError as e:
                self.send_error_response(500, str(e))
            return

        elif path.startswith("/containers/") and path.endswith("/stop"):
            container_id = path.split("/")[2]
            try:
                self.container_manager.stop(container_id)
                self.send_json_response(
                    204, {"message": f"Stopped container {container_id}"}
                )
            except ContainerError as e:
                self.send_error_response(500, str(e))
            return

        self.send_error_response(404, "Not Found")

    def do_DELETE(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path.startswith("/containers/"):
            container_id = path.split("/")[2]
            # Parse query params for force/v
            query = urllib.parse.parse_qs(parsed_url.query)
            force = query.get("force", ["false"])[0].lower() in ["true", "1"]
            v = query.get("v", ["false"])[0].lower() in ["true", "1"]

            try:
                success = self.container_manager.remove(
                    container_id, force=force, remove_volumes=v
                )
                if success:
                    self.send_json_response(
                        204, {"message": f"Removed container {container_id}"}
                    )
                else:
                    self.send_error_response(500, "Failed to remove container")
            except ContainerError as e:
                self.send_error_response(500, str(e))
            return

        self.send_error_response(404, "Not Found")


def run_daemon(socket_path: str = DEFAULT_SOCKET_PATH):
    """
    Start the Mini-Docker API daemon.
    """
    ensure_directories()

    # Ensure directory for socket exists
    socket_dir = os.path.dirname(socket_path)
    os.makedirs(socket_dir, exist_ok=True)

    if os.path.exists(socket_path):
        os.remove(socket_path)

    print(f"Starting Mini-Docker daemon listening on unix://{socket_path}")

    with UnixSocketHTTPServer(socket_path, DockerAPIHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down daemon...")
        finally:
            if os.path.exists(socket_path):
                os.remove(socket_path)
