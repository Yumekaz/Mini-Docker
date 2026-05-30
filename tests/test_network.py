import subprocess

import pytest

from mini_docker import network


def test_parse_port_mapping_accepts_valid_mapping():
    assert network.parse_port_mapping("8080:80") == (8080, 80)


@pytest.mark.parametrize("mapping", ["8080", "abc:80", "0:80", "65536:80", "80:0"])
def test_parse_port_mapping_rejects_invalid_mapping(mapping):
    with pytest.raises(network.NetworkError):
        network.parse_port_mapping(mapping)


def test_setup_nat_adds_missing_masquerade_rule(monkeypatch):
    calls = []

    def fake_iptables(args, check=True):
        calls.append((args, check))
        if "-C" in args:
            return subprocess.CompletedProcess(["iptables"] + args, 1)
        return subprocess.CompletedProcess(["iptables"] + args, 0)

    monkeypatch.setattr("mini_docker.network.run_iptables_command", fake_iptables)

    network.setup_nat("10.0.0.0/24")

    assert calls == [
        (
            ["-t", "nat", "-C", "POSTROUTING", "-s", "10.0.0.0/24", "-j", "MASQUERADE"],
            False,
        ),
        (
            ["-t", "nat", "-A", "POSTROUTING", "-s", "10.0.0.0/24", "-j", "MASQUERADE"],
            True,
        ),
    ]


def test_setup_nat_does_not_add_existing_masquerade_rule(monkeypatch):
    calls = []

    def fake_iptables(args, check=True):
        calls.append((args, check))
        return subprocess.CompletedProcess(["iptables"] + args, 0)

    monkeypatch.setattr("mini_docker.network.run_iptables_command", fake_iptables)

    network.setup_nat("10.0.0.0/24")

    assert calls == [
        (
            ["-t", "nat", "-C", "POSTROUTING", "-s", "10.0.0.0/24", "-j", "MASQUERADE"],
            False,
        )
    ]


def test_setup_port_forwarding_raises_on_iptables_failure(monkeypatch):
    def fail_run(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], stderr=b"iptables failed")

    monkeypatch.setattr("mini_docker.network.subprocess.run", fail_run)

    with pytest.raises(network.NetworkError, match="iptables failed"):
        network.setup_port_forwarding(8080, 80, "10.0.0.2")
