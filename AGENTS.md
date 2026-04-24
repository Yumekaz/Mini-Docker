# Agent Rules for Mini-Docker

Mini-Docker is transitioning from an educational project into a robust, lightweight container runtime serving as the foundation for a scalable Platform-as-a-Service (PaaS) similar to Vercel, Render, or Railway. When working on this project, adhere to the following rules:

* **PaaS Foundation:** The primary goal is to build a highly scalable, secure, and performant server environment capable of running untrusted workloads efficiently.
* **Core Runtime Architecture:** The core container runtime is still built from scratch. You must continue to use `os`, `ctypes`, and `libc` interfaces for raw Linux syscalls (such as `clone`, `unshare`, `mount`, and `pivot_root`) to manage container lifecycles without relying on external high-level container runtimes (like runc or containerd).
* **Dependency Management:** The strict "zero-dependency" rule is relaxed. Well-vetted, robust third-party libraries are allowed, especially for networking, API servers, and the PaaS layer. However, keep the core runtime as lightweight as possible.
* **Security & Isolation:** Since this will run multi-tenant PaaS workloads, prioritize strict security boundaries. Implement robust namespace isolation, cgroups resource limiting, and Seccomp/Capability filtering to prevent privilege escalation or container breakouts.
* **Performance & Scalability:** Optimize for fast container startup times and low resource overhead to support high-density deployments characteristic of a PaaS environment.
