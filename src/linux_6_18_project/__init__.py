"""Automation for the SpacemiT 6.18 kernel project.

The pinned BSP tree lives in ``src/linux-6.18`` and is never tracked; this
package assembles the configuration from the board's own running config plus a
declared update delta, builds the kernel with the workspace cross toolchain, and
packages it as Debian packages whose version says whether it is a frozen update
or an iteration.
"""
