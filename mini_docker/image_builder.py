#!/usr/bin/env python3
"""
Minimal Image Builder for Mini-Docker.

Implements a simple DSL for building container images,
similar to a simplified Dockerfile.

Supported Instructions:
    FROM <base-image-path>
    RUN <command>
    COPY <src> <dest>
    ENV <key>=<value>
    WORKDIR <path>
    CMD <command>

Each instruction creates a layer in the OverlayFS structure.
"""

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from mini_docker.utils import (IMAGES_PATH, ensure_directories,
                               generate_container_id)


@dataclass
class ImageLayer:
    """A single image layer."""

    id: str
    instruction: str
    command: str
    created_at: float = 0


@dataclass
class ImageConfig:
    """Image configuration."""

    id: str = ""
    name: str = ""
    tag: str = "latest"
    layers: List[ImageLayer] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    workdir: str = "/"
    cmd: List[str] = field(default_factory=list)
    entrypoint: List[str] = field(default_factory=list)


class BuildError(Exception):
    """Exception raised during image build."""

    pass


class ImageError(Exception):
    """Exception raised during image operations."""

    pass


def parse_image_file(content: str) -> List[Tuple[str, str]]:
    """
    Parse image build file (simplified Dockerfile).

    Args:
        content: File content

    Returns:
        List of (instruction, arguments) tuples
    """
    instructions = []
    current_line = ""

    for line in content.split("\n"):
        # Skip comments and empty lines
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Handle line continuation
        if stripped.endswith("\\"):
            current_line += stripped[:-1] + " "
            continue

        current_line += stripped

        # Parse instruction
        parts = current_line.split(None, 1)
        if len(parts) >= 1:
            instruction = parts[0].upper()
            args = parts[1] if len(parts) > 1 else ""
            instructions.append((instruction, args))

        current_line = ""

    return instructions


def get_image_path(image_id: str) -> str:
    """Get path to image directory."""
    return os.path.join(IMAGES_PATH, image_id)


def get_layer_path(image_id: str, layer_id: str) -> str:
    """Get path to a layer directory."""
    return os.path.join(get_image_path(image_id), "layers", layer_id)


class ImageBuilder:
    """
    Build container images from build files.

    Example:
        builder = ImageBuilder()
        image_id = builder.build("./Imagefile", "my-app:latest")
    """

    def __init__(self):
        ensure_directories()
        self.current_layer: Optional[str] = None
        self.layers: List[ImageLayer] = []
        self.env: Dict[str, str] = {}
        self.workdir = "/"
        self.cmd: List[str] = []

    def build(self, build_file: str, name: str = "", no_cache: bool = False) -> str:
        """
        Build an image from a build file.

        Args:
            build_file: Path to build file
            name: Image name:tag
            no_cache: If True, don't use cached layers

        Returns:
            Image ID
        """
        if not os.path.exists(build_file):
            raise BuildError(f"Build file not found: {build_file}")

        with open(build_file, "r") as f:
            content = f.read()

        instructions = parse_image_file(content)

        if not instructions:
            raise BuildError("No instructions found in build file")

        # Parse name and tag
        image_name = name or "image"
        image_tag = "latest"
        if ":" in image_name:
            image_name, image_tag = image_name.rsplit(":", 1)

        # Generate image ID
        image_id = generate_container_id()
        image_path = get_image_path(image_id)
        os.makedirs(os.path.join(image_path, "layers"), exist_ok=True)

        # Build context directory
        build_context = os.path.dirname(os.path.abspath(build_file))

        # Process instructions
        for instruction, args in instructions:
            self._process_instruction(image_id, instruction, args, build_context)

        # Save image config
        config = ImageConfig(
            id=image_id,
            name=image_name,
            tag=image_tag,
            layers=self.layers,
            env=self.env,
            workdir=self.workdir,
            cmd=self.cmd,
        )
        self._save_config(image_id, config)

        return image_id

    def _process_instruction(
        self, image_id: str, instruction: str, args: str, build_context: str
    ) -> None:
        """Process a single build instruction."""
        handlers = {
            "FROM": self._handle_from,
            "RUN": self._handle_run,
            "COPY": self._handle_copy,
            "ENV": self._handle_env,
            "WORKDIR": self._handle_workdir,
            "CMD": self._handle_cmd,
            "ENTRYPOINT": self._handle_entrypoint,
        }

        handler = handlers.get(instruction)
        if handler:
            handler(image_id, args, build_context)
        else:
            print(f"Warning: Unknown instruction: {instruction}")

    def _create_layer(self, image_id: str, instruction: str, command: str) -> str:
        """Create a new layer."""
        import time

        layer_id = generate_container_id()
        layer_path = get_layer_path(image_id, layer_id)
        os.makedirs(layer_path, exist_ok=True)

        layer = ImageLayer(
            id=layer_id,
            instruction=instruction,
            command=command,
            created_at=time.time(),
        )
        self.layers.append(layer)
        self.current_layer = layer_id

        return layer_path

    def _handle_from(self, image_id: str, args: str, context: str) -> None:
        """Handle FROM instruction."""
        base_path = args.strip()

        # Resolve relative path
        if not os.path.isabs(base_path):
            base_path = os.path.join(context, base_path)

        if not os.path.isdir(base_path):
            raise BuildError(f"Base image not found: {base_path}")

        # Create base layer
        layer_path = self._create_layer(image_id, "FROM", args)

        # Copy base image to layer
        shutil.copytree(base_path, layer_path, dirs_exist_ok=True, symlinks=True)

    def _handle_run(self, image_id: str, args: str, context: str) -> None:
        """Handle RUN instruction."""
        if not self.current_layer:
            raise BuildError("RUN instruction before FROM")

        layer_path = self._create_layer(image_id, "RUN", args)

        # Get previous layer for overlay
        if len(self.layers) > 1:
            prev_layer = self.layers[-2]
            prev_path = get_layer_path(image_id, prev_layer.id)
        else:
            prev_path = layer_path

        # Execute command in chroot
        # This is simplified - real implementation would use namespaces
        try:
            # Copy from previous layer
            if prev_path != layer_path:
                shutil.copytree(
                    prev_path, layer_path, dirs_exist_ok=True, symlinks=True
                )

            # Run command
            result = subprocess.run(
                ["/bin/sh", "-c", args],
                cwd=layer_path,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                raise BuildError(f"RUN failed: {result.stderr}")

        except subprocess.CalledProcessError as e:
            raise BuildError(f"RUN failed: {e}")

    def _handle_copy(self, image_id: str, args: str, context: str) -> None:
        """Handle COPY instruction."""
        if not self.current_layer:
            raise BuildError("COPY instruction before FROM")

        parts = args.split()
        if len(parts) < 2:
            raise BuildError("COPY requires source and destination")

        *sources, dest = parts

        layer_path = self._create_layer(image_id, "COPY", args)

        # Copy from previous layer first
        if len(self.layers) > 1:
            prev_layer = self.layers[-2]
            prev_path = get_layer_path(image_id, prev_layer.id)
            shutil.copytree(prev_path, layer_path, dirs_exist_ok=True, symlinks=True)

        # Copy sources to destination
        dest_path = os.path.join(layer_path, dest.lstrip("/"))
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)

        for src in sources:
            src_path = os.path.join(context, src)
            if os.path.isdir(src_path):
                shutil.copytree(src_path, dest_path, dirs_exist_ok=True, symlinks=True)
            else:
                shutil.copy2(src_path, dest_path, follow_symlinks=False)

    def _handle_env(self, image_id: str, args: str, context: str) -> None:
        """Handle ENV instruction."""
        if "=" in args:
            key, value = args.split("=", 1)
            self.env[key.strip()] = value.strip()
        else:
            parts = args.split(None, 1)
            if len(parts) == 2:
                self.env[parts[0]] = parts[1]

    def _handle_workdir(self, image_id: str, args: str, context: str) -> None:
        """Handle WORKDIR instruction."""
        self.workdir = args.strip()

    def _handle_cmd(self, image_id: str, args: str, context: str) -> None:
        """Handle CMD instruction."""
        # Parse as JSON array or shell command
        args = args.strip()
        if args.startswith("["):
            import json

            try:
                self.cmd = json.loads(args)
            except json.JSONDecodeError:
                self.cmd = ["/bin/sh", "-c", args]
        else:
            self.cmd = ["/bin/sh", "-c", args]

    def _handle_entrypoint(self, image_id: str, args: str, context: str) -> None:
        """Handle ENTRYPOINT instruction."""
        args = args.strip()
        if args.startswith("["):
            import json

            try:
                self.cmd = json.loads(args)
            except json.JSONDecodeError:
                self.cmd = [args]
        else:
            self.cmd = [args]

    def _save_config(self, image_id: str, config: ImageConfig) -> None:
        """Save image configuration."""
        import json
        from dataclasses import asdict

        config_path = os.path.join(get_image_path(image_id), "config.json")

        # Convert layers to dicts
        data = asdict(config)

        with open(config_path, "w") as f:
            json.dump(data, f, indent=2)

    def get_rootfs(self, image_id: str) -> str:
        """Get path to final rootfs for an image."""
        # Return last layer as rootfs
        config_path = os.path.join(get_image_path(image_id), "config.json")

        if not os.path.exists(config_path):
            raise BuildError(f"Image not found: {image_id}")

        import json

        with open(config_path, "r") as f:
            config = json.load(f)

        if not config.get("layers"):
            raise BuildError("Image has no layers")

        last_layer = config["layers"][-1]
        return get_layer_path(image_id, last_layer["id"])


def list_images() -> List[ImageConfig]:
    """List all built images."""
    import json

    images = []

    if not os.path.exists(IMAGES_PATH):
        return images

    for image_id in os.listdir(IMAGES_PATH):
        config_path = os.path.join(get_image_path(image_id), "config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    data = json.load(f)

                # Convert layers dicts to dataclass
                layers = [ImageLayer(**layer) for layer in data.get("layers", [])]
                data["layers"] = layers

                images.append(ImageConfig(**data))
            except (json.JSONDecodeError, TypeError):
                pass

    return images


def resolve_image_path(name_or_id: str) -> Optional[str]:
    """
    Resolve an image name or ID to its rootfs path.

    Args:
        name_or_id: Image ID (prefix), "name", or "name:tag"

    Returns:
        Path to image rootfs (last layer) or None if not found
    """
    images = list_images()

    # Try exact ID match or prefix
    for img in images:
        if img.id.startswith(name_or_id):
            return ImageBuilder().get_rootfs(img.id)

    # Try name:tag
    target_name = name_or_id
    target_tag = "latest"
    if ":" in name_or_id:
        target_name, target_tag = name_or_id.split(":", 1)

    for img in images:
        if img.name == target_name and img.tag == target_tag:
            return ImageBuilder().get_rootfs(img.id)

    return None


def remove_image(name_or_id: str, force: bool = False) -> bool:
    """
    Remove an image by name or ID.

    Args:
        name_or_id: Image ID (prefix), "name", or "name:tag"
        force: Force removal even if image is in use

    Returns:
        True if image was removed

    Raises:
        ImageError: If image not found or cannot be removed
    """
    images = list_images()
    target_image = None

    # Try exact ID match or prefix
    for img in images:
        if img.id.startswith(name_or_id):
            target_image = img
            break

    # Try name:tag
    if not target_image:
        target_name = name_or_id
        target_tag = "latest"
        if ":" in name_or_id:
            target_name, target_tag = name_or_id.split(":", 1)

        for img in images:
            if img.name == target_name and img.tag == target_tag:
                target_image = img
                break

    if not target_image:
        raise ImageError(f"Image not found: {name_or_id}")

    # Check if image is in use (by any container)
    # In a full implementation, we'd check container configs
    # For now, we just remove if force is True or always allow

    image_path = get_image_path(target_image.id)

    try:
        if os.path.exists(image_path):
            shutil.rmtree(image_path)
        return True
    except OSError as e:
        raise ImageError(f"Cannot remove image: {e}")
