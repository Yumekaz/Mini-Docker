# 📁 Examples

This directory contains example scripts demonstrating Mini-Docker capabilities.

---

## Quick Start

```bash
# Run the interactive demo
sudo ./demo.sh

# Or run individual examples
sudo python3 simple_container.py
sudo python3 resource_limits.py
sudo python3 networking.py
```

---

## Files

| File | Description |
|------|-------------|
| `simple_container.py` | Basic container creation |
| `resource_limits.py` | Memory, CPU, PID limits |
| `networking.py` | Container networking setup |
| `demo.sh` | Interactive demonstration script |

---

## Example Descriptions

### simple_container.py

Demonstrates basic container creation:
- Creating namespaces
- Setting up filesystem
- Running a command
- Cleanup

```bash
sudo python3 simple_container.py
```

### resource_limits.py

Shows resource limiting:
- Memory limits (prevent OOM)
- CPU throttling
- Process count limits (fork bomb protection)

```bash
sudo python3 resource_limits.py
```

### networking.py

Demonstrates container networking:
- Creating network namespace
- Setting up veth pairs
- Bridge configuration
- NAT for internet access

```bash
sudo python3 networking.py
```

### demo.sh

Interactive demo script that:
- Checks prerequisites
- Runs example containers
- Shows isolation in action
- Demonstrates resource limits

```bash
sudo ./demo.sh
```

---

## Prerequisites

Before running examples:

1. **Linux kernel 4.18+**
   ```bash
   uname -r
   ```

2. **Python 3.7+**
   ```bash
   python3 --version
   ```

3. **Root access** (for most examples)
   ```bash
   sudo -v
   ```

4. **Mini-Docker installed**
   ```bash
   cd /path/to/Mini-Docker
   pip install -e .
   ```

5. **Rootfs available**
   ```bash
   ./setup.sh
   ```

---

## Running Examples

### As Scripts

```bash
cd examples/
sudo python3 simple_container.py
```

### As Module

```bash
cd Mini-Docker/
sudo python3 -c "from examples import simple_container; simple_container.main()"
```

### Interactive Demo

```bash
cd examples/
sudo ./demo.sh
```

---

## Expected Output

### simple_container.py

```
[+] Creating simple container...
[+] Setting up namespaces...
[+] Configuring filesystem...
[+] Running command in container...

Container output:
  PID: 1
  Hostname: mini-container
  User: root

[+] Container exited successfully
[+] Cleanup complete
```

### resource_limits.py

```
[+] Testing memory limit (50MB)...
[+] Memory allocation test: PASSED (killed at limit)

[+] Testing CPU limit (25%)...
[+] CPU throttling test: PASSED (limited to ~25%)

[+] Testing PID limit (10)...
[+] Fork bomb protection: PASSED (stopped at 10 processes)
```

### networking.py

```
[+] Setting up container networking...
[+] Created veth pair: veth-host <-> veth-container
[+] Created bridge: mini-docker-br0
[+] Container IP: 10.0.0.2

[+] Testing connectivity...
[+] Ping to host: SUCCESS
[+] Ping to internet: SUCCESS

[+] Cleanup complete
```

---

## Troubleshooting

### Permission Denied

```bash
# Run with sudo
sudo python3 simple_container.py
```

### Module Not Found

```bash
# Install Mini-Docker
cd /path/to/Mini-Docker
pip install -e .
```

### Rootfs Not Found

```bash
# Create rootfs
cd /path/to/Mini-Docker
sudo ./setup.sh
```

### Cgroup Errors

```bash
# Check cgroups v2
mount | grep cgroup2

# Enable if needed
sudo mount -t cgroup2 none /sys/fs/cgroup
```

---

## Modifying Examples

Feel free to modify these examples for learning:

1. **Change resource limits** in `resource_limits.py`
2. **Add new commands** in `simple_container.py`
3. **Experiment with network config** in `networking.py`
4. **Extend the demo** in `demo.sh`

---

## See Also

- [Full Documentation](../docs/)
- [CLI Commands](../docs/CLI-COMMANDS.md)
- [Troubleshooting](../docs/TROUBLESHOOTING.md)
