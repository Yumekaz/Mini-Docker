"""
Mini-Docker Test Suite
======================

This package contains unit tests for the Mini-Docker container runtime.

Test Categories:
    - test_basic.py: Import tests and basic functionality
    - (future) test_namespaces.py: Namespace isolation tests
    - (future) test_cgroups.py: Resource limiting tests
    - (future) test_security.py: Security feature tests

Running Tests:
    pytest tests/ -v
    pytest tests/ -v --cov=mini_docker

Note:
    Some tests require root privileges to test actual container features.
    Tests that require root are marked with @pytest.mark.skipif(os.geteuid() != 0).
"""

import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

__all__ = ["test_basic"]
