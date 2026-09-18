[中文](AGENTS.zh-CN.md)

# linux-6.18 project requirements

This project-local contract narrows the workspace contract in the root
`AGENTS.md` and the governance documents; it cannot relax them. It records only
what is specific to the kernel project.

- Scope is kernel-side work for the K3: configuration deltas now, declared
  patches to the pinned BSP tree later, always delivered as Debian packages.
- The pinned source is `src/linux-6.18` at the commit in `sources.lock.yaml`. It
  is never edited in place: a source change is a patch file plus its manifest
  entry, so the build can be reproduced from the lock alone.
- Configuration is a base plus a delta. The base is `config/base.config`, taken
  from the board's own running kernel and pinned by digest; each update declares
  one delta in `config/updateN.config`, and the resolved config is sealed with
  every build.
- Only a frozen update carries a version number: `6.18.3-generic-updateN` for an
  update meant to be kept, `6.18.3-generic-dev+<commit>` for iteration. A
  recorded run names the version it used.
- Installing a kernel on the board is a remote change. It happens only when the
  operator asks for it, the door prints the package identity first, and the
  running kernel is reported afterwards.
- Every build seals a manifest outside Git: base config digest, delta, resolved
  config digest, cross compiler identity, package names and digests.
- Kernel builds are host-side and use the workspace toolchain
  `spacemit-toolchain-linux-glibc-x86_64-v1.2.4`; they never read the device
  credential records, which stay operator-only.
