# Agent Rules for Mini-Docker

This is a zero-dependency educational container runtime built from scratch. When working on this project, adhere strictly to the following rules:

* **Educational & Built from Scratch:** This project demonstrates how containers work at the Linux kernel level. All implementations should reflect this educational purpose.
* **Zero Dependencies:** No external packages or dependencies are allowed.
* **Python Standard Library Only:** Use only the Python standard library for all functionality.
* **Raw Linux Syscalls:** You must use the `os`, `ctypes`, and `libc` interfaces to perform raw Linux syscalls (such as `clone`, `unshare`, `mount`, and `pivot_root`).
* **No High-Level Wrappers:** Absolutely no external high-level container libraries, wrappers, or tools are to be used under any circumstances.
