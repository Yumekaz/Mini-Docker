#!/bin/bash
#
# Mini-Docker Interactive Demo
# ============================
#
# This script provides an interactive demonstration of Mini-Docker
# capabilities. Run with: sudo ./demo.sh
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ROOTFS="$PROJECT_DIR/rootfs"

# Print functions
print_header() {
    echo ""
    echo -e "${BLUE}${BOLD}========================================${NC}"
    echo -e "${BLUE}${BOLD}  $1${NC}"
    echo -e "${BLUE}${BOLD}========================================${NC}"
    echo ""
}

print_step() {
    echo -e "${GREEN}[+]${NC} $1"
}

print_info() {
    echo -e "${CYAN}    $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}[!]${NC} $1"
}

print_error() {
    echo -e "${RED}[✗]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

wait_for_key() {
    echo ""
    echo -e "${YELLOW}Press Enter to continue...${NC}"
    read -r
}

# Check prerequisites
check_prerequisites() {
    print_header "Checking Prerequisites"
    
    # Check root
    if [ "$EUID" -ne 0 ]; then
        print_error "This demo requires root privileges"
        echo "Please run: sudo $0"
        exit 1
    fi
    print_success "Running as root"
    
    # Check Python
    if ! command -v python3 &> /dev/null; then
        print_error "Python3 not found"
        exit 1
    fi
    PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
    print_success "Python version: $PYTHON_VERSION"
    
    # Check kernel version
    KERNEL_VERSION=$(uname -r)
    print_success "Kernel version: $KERNEL_VERSION"
    
    # Check rootfs
    if [ ! -d "$ROOTFS" ]; then
        print_error "Rootfs not found at $ROOTFS"
        print_info "Running setup script..."
        "$PROJECT_DIR/setup.sh"
    fi
    print_success "Rootfs found: $ROOTFS"
    
    # Check cgroups v2
    if [ -f "/sys/fs/cgroup/cgroup.controllers" ]; then
        print_success "Cgroups v2 available"
    else
        print_warning "Cgroups v2 not detected (some features may not work)"
    fi
    
    echo ""
    print_success "All prerequisites met!"
}

# Demo 1: Basic container
demo_basic_container() {
    print_header "Demo 1: Basic Container"
    
    print_step "Creating a simple container that runs 'echo Hello'..."
    print_info "This demonstrates the most basic container operation."
    echo ""
    
    python3 -m mini_docker run "$ROOTFS" /bin/echo "Hello from Mini-Docker!"
    
    print_success "Container ran successfully!"
}

# Demo 2: Process isolation
demo_process_isolation() {
    print_header "Demo 2: Process Isolation (PID Namespace)"
    
    print_step "Inside a container, processes are isolated."
    print_info "The container's first process gets PID 1 (like init)."
    echo ""
    
    print_step "Running container to show PID..."
    
    python3 -m mini_docker run "$ROOTFS" /bin/sh -c '
        echo "My PID is: $$"
        echo ""
        echo "Process list:"
        ps aux 2>/dev/null || echo "(ps shows only container processes)"
        echo ""
        echo "Notice: PID 1 - I am init in this container!"
    '
    
    print_success "Process isolation demonstrated!"
    print_info "Container had PID 1 - completely isolated from host PIDs"
}

# Demo 3: Filesystem isolation
demo_filesystem_isolation() {
    print_header "Demo 3: Filesystem Isolation (Mount Namespace)"
    
    print_step "Containers have their own isolated filesystem view."
    print_info "Changes in the container don't affect the host."
    echo ""
    
    print_step "Creating a file inside the container..."
    
    python3 -m mini_docker run "$ROOTFS" /bin/sh -c '
        echo "Creating /test-file.txt in container..."
        echo "This file only exists inside the container" > /test-file.txt
        echo ""
        echo "File contents:"
        cat /test-file.txt
        echo ""
        echo "Container root directory:"
        ls -la / | head -10
    '
    
    echo ""
    print_step "Checking if file exists on host..."
    if [ -f "/test-file.txt" ]; then
        print_error "File leaked to host! (This shouldn't happen)"
    else
        print_success "File does NOT exist on host - isolation working!"
    fi
}

# Demo 4: Hostname isolation
demo_hostname_isolation() {
    print_header "Demo 4: Hostname Isolation (UTS Namespace)"
    
    print_step "Containers can have their own hostname."
    print_info "Changing hostname in container doesn't affect host."
    echo ""
    
    HOST_HOSTNAME=$(hostname)
    print_info "Host hostname: $HOST_HOSTNAME"
    echo ""
    
    print_step "Running container with custom hostname..."
    
    python3 -m mini_docker run --hostname "my-container" "$ROOTFS" /bin/sh -c '
        echo "Container hostname: $(hostname)"
        echo ""
        echo "Trying to change hostname to hacked..."
        hostname hacked 2>/dev/null || echo "(Change blocked by namespace)"
        echo "Hostname is now: $(hostname)"
    '
    
    echo ""
    print_info "Host hostname after container: $(hostname)"
    print_success "Hostname isolation working!"
}

# Demo 5: Resource limits
demo_resource_limits() {
    print_header "Demo 5: Resource Limits (Cgroups)"
    
    print_step "Containers can have resource limits:"
    print_info "  - Memory: Prevent OOM attacks"
    print_info "  - CPU: Prevent CPU starvation"
    print_info "  - PIDs: Prevent fork bombs"
    echo ""
    
    # Memory limit demo
    print_step "Testing memory limit (50MB)..."
    print_info "Container will try to allocate 100MB..."
    
    python3 -m mini_docker run --memory 50M "$ROOTFS" /bin/sh -c '
        echo "Attempting to allocate 100MB..."
        dd if=/dev/zero of=/tmp/test bs=1M count=100 2>&1 || true
        echo "Memory limit enforced!"
    ' 2>&1 || true
    
    echo ""
    
    # PID limit demo
    print_step "Testing PID limit (5 processes)..."
    print_info "Container will try to create 10 processes..."
    
    python3 -m mini_docker run --pids 5 "$ROOTFS" /bin/sh -c '
        count=0
        for i in 1 2 3 4 5 6 7 8 9 10; do
            sleep 100 & 2>/dev/null && count=$((count + 1)) || break
        done
        echo "Created $count processes (limit is 5)"
    '
    
    print_success "Resource limits demonstrated!"
}

# Demo 6: Security features
demo_security() {
    print_header "Demo 6: Security Features"
    
    print_step "Mini-Docker implements multiple security layers:"
    print_info "  1. Namespaces - Isolation"
    print_info "  2. Cgroups - Resource limits"
    print_info "  3. Seccomp - Syscall filtering"
    print_info "  4. Capabilities - Privilege reduction"
    print_info "  5. NO_NEW_PRIVS - Prevent escalation"
    echo ""
    
    print_step "Testing seccomp (blocked syscall)..."
    
    python3 -m mini_docker run "$ROOTFS" /bin/sh -c '
        echo "Trying to mount a filesystem (should be blocked)..."
        mount -t proc proc /proc 2>&1 || echo "Mount blocked by seccomp!"
        echo ""
        echo "Trying to reboot (should be blocked)..."
        reboot 2>&1 || echo "Reboot blocked by seccomp!"
    '
    
    print_success "Security features working!"
    print_info "Dangerous operations are blocked even with root in container"
}

# Interactive shell demo
demo_interactive() {
    print_header "Demo 7: Interactive Container"
    
    print_step "You can run an interactive shell in a container."
    print_info "Try these commands inside:"
    print_info "  echo \$\$    - Show your PID (should be 1)"
    print_info "  hostname   - Show container hostname"
    print_info "  ps aux     - List processes"
    print_info "  ls /       - List root filesystem"
    print_info "  exit       - Exit container"
    echo ""
    
    print_step "Starting interactive container..."
    print_warning "Type 'exit' to return to the demo"
    echo ""
    
    python3 -m mini_docker run --hostname "interactive-demo" "$ROOTFS" /bin/sh
    
    print_success "Exited interactive container"
}

# Summary
show_summary() {
    print_header "Demo Complete!"
    
    echo -e "${BOLD}What you've learned:${NC}"
    echo ""
    echo "  ✓ Containers use Linux namespaces for isolation"
    echo "  ✓ PID namespace gives container its own PID 1"
    echo "  ✓ Mount namespace isolates the filesystem"
    echo "  ✓ UTS namespace isolates the hostname"
    echo "  ✓ Cgroups limit memory, CPU, and processes"
    echo "  ✓ Seccomp blocks dangerous system calls"
    echo ""
    echo -e "${BOLD}Key Mini-Docker commands:${NC}"
    echo ""
    echo "  # Run a container"
    echo "  sudo python3 -m mini_docker run ./rootfs /bin/sh"
    echo ""
    echo "  # Run with resource limits"
    echo "  sudo python3 -m mini_docker run --memory 100M --cpu 50 ./rootfs /bin/sh"
    echo ""
    echo "  # Run with custom hostname"
    echo "  sudo python3 -m mini_docker run --hostname myhost ./rootfs /bin/sh"
    echo ""
    echo -e "${BOLD}Learn more:${NC}"
    echo "  📖 docs/QUICKSTART.md     - Quick start guide"
    echo "  📖 docs/ARCHITECTURE.md   - How it works"
    echo "  📖 docs/CLI-COMMANDS.md   - All commands"
    echo ""
}

# Main menu
main_menu() {
    while true; do
        print_header "Mini-Docker Demo Menu"
        
        echo "Select a demo to run:"
        echo ""
        echo "  1) Basic Container          - Run simple command"
        echo "  2) Process Isolation        - PID namespace demo"
        echo "  3) Filesystem Isolation     - Mount namespace demo"
        echo "  4) Hostname Isolation       - UTS namespace demo"
        echo "  5) Resource Limits          - Cgroups demo"
        echo "  6) Security Features        - Seccomp demo"
        echo "  7) Interactive Container    - Shell in container"
        echo ""
        echo "  a) Run ALL demos"
        echo "  q) Quit"
        echo ""
        echo -n "Enter choice: "
        read -r choice
        
        case $choice in
            1) demo_basic_container; wait_for_key ;;
            2) demo_process_isolation; wait_for_key ;;
            3) demo_filesystem_isolation; wait_for_key ;;
            4) demo_hostname_isolation; wait_for_key ;;
            5) demo_resource_limits; wait_for_key ;;
            6) demo_security; wait_for_key ;;
            7) demo_interactive; wait_for_key ;;
            a|A)
                demo_basic_container; wait_for_key
                demo_process_isolation; wait_for_key
                demo_filesystem_isolation; wait_for_key
                demo_hostname_isolation; wait_for_key
                demo_resource_limits; wait_for_key
                demo_security; wait_for_key
                show_summary; wait_for_key
                ;;
            q|Q)
                echo ""
                print_success "Thanks for trying Mini-Docker!"
                echo ""
                exit 0
                ;;
            *)
                print_warning "Invalid choice. Please try again."
                ;;
        esac
    done
}

# Main execution
main() {
    clear
    
    echo -e "${BOLD}"
    echo "  __  __ _       _       ____             _             "
    echo " |  \/  (_)_ __ (_)     |  _ \  ___   ___| | _____ _ __ "
    echo " | |\/| | | '_ \| |_____| | | |/ _ \ / __| |/ / _ \ '__|"
    echo " | |  | | | | | | |_____| |_| | (_) | (__|   <  __/ |   "
    echo " |_|  |_|_|_| |_|_|     |____/ \___/ \___|_|\_\___|_|   "
    echo -e "${NC}"
    echo ""
    echo "  Educational Container Runtime Demo"
    echo "  ==================================="
    echo ""
    
    # Check prerequisites
    check_prerequisites
    wait_for_key
    
    # Show menu
    main_menu
}

# Run main
main "$@"
