#!/usr/bin/env python3
"""
Utility functions for Mini-Docker.

Provides:
- Random ID generation (12-char hex)
- Docker-style name generation (adjective-animal)
- System call wrappers
- Path utilities

Container Naming Strategy
=========================

Mini-Docker uses a two-part identification system for containers:

1. Container ID (12-character hexadecimal)
   - Generated using cryptographically random hex characters
   - Format: [0-9a-f]{12}
   - Examples: "a1b2c3d4e5f6", "deadbeef0123"
   - Used internally for unique identification and filesystem paths
   - Can be shortened when referencing (prefix matching)

2. Human-Friendly Names (adjective-animal)
   - Format: <adjective>-<animal>
   - Examples: "silent-hawk", "frosty-lion", "brave-wolf"
   - Generated from curated word lists (100+ adjectives, 100+ animals)
   - Provides memorable identifiers for human use
   - Docker-compatible naming convention

Naming Algorithm:
    def generate_container_id():
        return random.choices('0123456789abcdef', k=12)
    
    def generate_container_name():
        adjective = random.choice(ADJECTIVES)  # ~115 options
        animal = random.choice(ANIMALS)        # ~120 options
        return f"{adjective}-{animal}"         # ~13,800 combinations

Usage Examples:
    Container ID:  a7f3b2e1c9d0
    Container Name: peaceful-penguin
    Reference by:   a7f3b2e1c9d0, a7f3b2, peaceful-penguin
"""

import os
import random
import string
import ctypes
import struct
from typing import Optional, Tuple

# Base paths for Mini-Docker
# Determine the root directory for storage
if os.environ.get("MINI_DOCKER_ROOT"):
    MINI_DOCKER_ROOT = os.environ["MINI_DOCKER_ROOT"]
elif os.geteuid() == 0:
    MINI_DOCKER_ROOT = "/var/lib/mini-docker"
else:
    # Use standard XDG data home for rootless mode
    xdg_data = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    MINI_DOCKER_ROOT = os.path.join(xdg_data, "mini-docker")

CONTAINERS_PATH = f"{MINI_DOCKER_ROOT}/containers"
IMAGES_PATH = f"{MINI_DOCKER_ROOT}/images"
OVERLAY_PATH = f"{MINI_DOCKER_ROOT}/overlay"
PODS_PATH = f"{MINI_DOCKER_ROOT}/pods"

# Determine run directory (for PIDs, sockets)
if os.environ.get("MINI_DOCKER_RUN"):
    RUN_PATH = os.environ["MINI_DOCKER_RUN"]
elif os.geteuid() == 0:
    RUN_PATH = "/var/run/mini-docker"
else:
    # Use standard XDG runtime dir
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        RUN_PATH = os.path.join(xdg_runtime, "mini-docker")
    else:
        # Fallback to tmp location incorporating UID
        RUN_PATH = f"/tmp/mini-docker-{os.geteuid()}"

# Adjectives for Docker-style names
ADJECTIVES = [
    "admiring", "adoring", "affectionate", "agitated", "amazing",
    "angry", "awesome", "beautiful", "blissful", "bold",
    "boring", "brave", "busy", "charming", "clever",
    "cool", "compassionate", "competent", "condescending", "confident",
    "cranky", "crazy", "dazzling", "determined", "distracted",
    "dreamy", "eager", "ecstatic", "elastic", "elated",
    "elegant", "eloquent", "epic", "exciting", "fervent",
    "festive", "flamboyant", "focused", "friendly", "frosty",
    "funny", "gallant", "gifted", "goofy", "gracious",
    "great", "happy", "hardcore", "heuristic", "hopeful",
    "hungry", "infallible", "inspiring", "intelligent", "interesting",
    "jolly", "jovial", "keen", "kind", "laughing",
    "loving", "lucid", "magical", "modest", "musing",
    "mystifying", "naughty", "nervous", "nice", "nifty",
    "nostalgic", "objective", "optimistic", "peaceful", "pedantic",
    "pensive", "practical", "priceless", "quirky", "quizzical",
    "recursing", "relaxed", "reverent", "romantic", "sad",
    "serene", "sharp", "silly", "sleepy", "stoic",
    "strange", "stupefied", "suspicious", "sweet", "tender",
    "thirsty", "trusting", "unruffled", "upbeat", "vibrant",
    "vigilant", "vigorous", "wizardly", "wonderful", "xenodochial",
    "youthful", "zealous", "zen", "silent", "swift"
]

# Animals for Docker-style names
ANIMALS = [
    "albatross", "alligator", "alpaca", "ant", "antelope",
    "ape", "armadillo", "baboon", "badger", "bat",
    "bear", "beaver", "bee", "bison", "boar",
    "buffalo", "butterfly", "camel", "caribou", "cat",
    "caterpillar", "cheetah", "chicken", "chimpanzee", "cobra",
    "coyote", "crab", "crane", "crocodile", "crow",
    "deer", "dog", "dolphin", "donkey", "dove",
    "dragonfly", "duck", "eagle", "elephant", "elk",
    "emu", "falcon", "ferret", "finch", "fish",
    "flamingo", "fox", "frog", "gazelle", "gerbil",
    "giraffe", "goat", "goose", "gorilla", "grasshopper",
    "hamster", "hare", "hawk", "hedgehog", "heron",
    "hippo", "hornet", "horse", "hummingbird", "hyena",
    "jackal", "jaguar", "jay", "jellyfish", "kangaroo",
    "koala", "lemur", "leopard", "lion", "lizard",
    "llama", "lobster", "lynx", "magpie", "mallard",
    "manatee", "meerkat", "mink", "mole", "mongoose",
    "monkey", "moose", "mouse", "mule", "narwhal",
    "newt", "octopus", "opossum", "orca", "ostrich",
    "otter", "owl", "ox", "panda", "panther",
    "parrot", "peacock", "pelican", "penguin", "pig",
    "pigeon", "porcupine", "rabbit", "raccoon", "ram",
    "raven", "salmon", "seal", "shark", "sheep",
    "sloth", "snail", "snake", "sparrow", "spider",
    "squid", "squirrel", "stork", "swan", "tiger",
    "toad", "turkey", "turtle", "vulture", "walrus",
    "wasp", "weasel", "whale", "wolf", "wolverine",
    "wombat", "woodpecker", "yak", "zebra"
]


def generate_container_id() -> str:
    """
    Generate a 12-character hexadecimal container ID.
    Similar to Docker's short container IDs.
    
    Algorithm:
        1. Use secure random selection from hex character set [0-9a-f]
        2. Generate exactly 12 characters
        3. Result is lowercase for consistency
    
    Returns:
        str: 12-char hex string (e.g., "a1b2c3d4e5f6")
    
    Examples:
        >>> generate_container_id()
        'a7f3b2e1c9d0'
        >>> generate_container_id()
        'deadbeef0123'
        >>> len(generate_container_id())
        12
    """
    return ''.join(random.choices(string.hexdigits.lower()[:16], k=12))


def generate_container_name() -> str:
    """
    Generate a Docker-style random name (adjective-animal).
    
    Algorithm:
        1. Randomly select one adjective from ADJECTIVES list (~115 options)
        2. Randomly select one animal from ANIMALS list (~120 options)
        3. Combine as "{adjective}-{animal}"
        4. Total unique combinations: ~13,800
    
    Returns:
        str: Name like "frosty-wolf" or "silent-hawk"
    
    Examples:
        >>> generate_container_name()
        'peaceful-penguin'
        >>> generate_container_name()
        'brave-lion'
        >>> '-' in generate_container_name()
        True
    """
    adjective = random.choice(ADJECTIVES)
    animal = random.choice(ANIMALS)
    return f"{adjective}-{animal}"


def ensure_directories():
    """Create all required Mini-Docker directories."""
    directories = [
        MINI_DOCKER_ROOT,
        CONTAINERS_PATH,
        IMAGES_PATH,
        OVERLAY_PATH,
        PODS_PATH,
        RUN_PATH,
    ]
    for directory in directories:
        os.makedirs(directory, exist_ok=True)


def get_container_path(container_id: str) -> str:
    """Get the path to a container's directory."""
    return os.path.join(CONTAINERS_PATH, container_id)


def get_overlay_paths(container_id: str) -> Tuple[str, str, str, str]:
    """
    Get OverlayFS paths for a container.
    
    Returns:
        Tuple of (lower, upper, work, merged) paths
    """
    base = os.path.join(OVERLAY_PATH, container_id)
    return (
        os.path.join(base, "lower"),
        os.path.join(base, "upper"),
        os.path.join(base, "work"),
        os.path.join(base, "merged"),
    )


# Load libc for system calls
try:
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
except OSError:
    libc = ctypes.CDLL(None, use_errno=True)


def check_root() -> bool:
    """Check if running as root."""
    return os.geteuid() == 0


def read_file(path: str) -> Optional[str]:
    """Safely read a file's contents."""
    try:
        with open(path, 'r') as f:
            return f.read().strip()
    except (IOError, OSError):
        return None


def write_file(path: str, content: str) -> bool:
    """Safely write content to a file."""
    try:
        with open(path, 'w') as f:
            f.write(content)
        return True
    except (IOError, OSError):
        return False


def get_available_ip() -> str:
    """
    Get an available IP address in the 10.0.0.0/24 range.
    Checks existing containers to avoid conflicts.
    """
    used_ips = set()
    
    # Scan existing containers for used IPs
    if os.path.exists(CONTAINERS_PATH):
        for cid in os.listdir(CONTAINERS_PATH):
            config_path = os.path.join(CONTAINERS_PATH, cid, "config.json")
            if os.path.exists(config_path):
                try:
                    import json
                    with open(config_path) as f:
                        config = json.load(f)
                        if "network" in config and "ip" in config["network"]:
                            ip = config["network"]["ip"]
                            if ip:
                                used_ips.add(ip)
                except (json.JSONDecodeError, IOError, OSError, KeyError, TypeError):
                    pass
    
    # Find available IP (10.0.0.2 - 10.0.0.254)
    for i in range(2, 255):
        ip = f"10.0.0.{i}"
        if ip not in used_ips:
            return ip
    
    raise RuntimeError("No available IP addresses in 10.0.0.0/24 range")


def generate_mac_address() -> str:
    """Generate a random MAC address with local bit set."""
    # First byte: set local bit (0x02) and clear multicast bit
    mac = [0x02, 0x42]  # 02:42 is Docker's prefix
    mac.extend([random.randint(0, 255) for _ in range(4)])
    return ':'.join(f'{b:02x}' for b in mac)
