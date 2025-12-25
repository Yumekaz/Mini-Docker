# Security Policy

## Reporting Vulnerabilities

Please do not open public issues for security vulnerabilities.

Instead, report them privately via email:
- pathaktarun431@gmail.com

## Scope

Mini-Docker is an educational project intended to demonstrate container
runtime concepts using Linux primitives.

It is **not designed for production use** or for running untrusted workloads.

## Security Considerations

The project explores mechanisms such as:
- Linux namespaces
- cgroups v2
- seccomp filtering
- Linux capabilities

These are provided for learning purposes and may be incomplete.

For production container security, use established runtimes such as:
- runc
- crun
- gVisor
- Kata Containers
