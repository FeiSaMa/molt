# v2 M2/M3 migration contract

This document records the M2/M3 rollout before the default-backend decision.
References below to an opt-in kernel and a v1 production default describe that
period. Linux x86_64 release `1.1.0` adopted the kernel default, with read-only
v1 audit and explicit migration throughout `1.1.x`.
For current commands and release scope, see the [migration guide](../operations/kernel-cli.md#v1-只读兼容与显式迁移)
and the [release record](../operations/v2-release-1.1.0.md).

## M2 opt-in mirror

`MOLT_KERNEL_SOCKET=<kernel.sock>` enables the CLI mirror for commands that use
an explicit persistent `--state` path. The existing Python state remains the
execution and read source. After a successful command, the CLI uploads durable
state files as SHA-256 CAS objects, creates a kernel manifest, checks the
manifest roots against the source snapshot, and commits one generation. The
`work/` directory is excluded; symlinks and non-regular files fail closed.

The mirror is opt-in and one-way. If it fails after the Python command has
committed, the CLI reports `v1 state committed; kernel mirror incomplete` and
returns failure. Re-running is safe: the request ID is derived from the
previous generation and target manifest, and identical current state creates
no additional generation. If the connection drops during commit, the outcome
is unknown until the next request recovers the kernel state; retry the same
mirror operation before making another state change.

The kernel exposes manifest and object reads for a bounded projection into a
new directory. M2 integration tests run a fixture search and the full replay
demo, mirror both states, materialize the replay state from the kernel, and
compare every durable file digest and mode. The replay state also passes the
same `audit_state` recomputation before and after projection. Partial search
states are checked for byte parity only because they intentionally lack report
scope and redaction records required by full semantic audit.

M2 therefore validates a shadow-write and readback path for fixture/replay
evidence. Python still executes commands against its state projection; this is
not an automatic M3 primary-backend switch.

## M3 implementation status

`molt migrate --state <v1-state>` is exposed as a read-only preview by
default; `--apply --migration-id <id>` performs an import. Preview reports the
target state ID, source file count and bytes, source manifest digest, and a
sorted per-file digest/size difference list. A named migration always records
its request ID, including when the same manifest is already current. Reusing
that ID with a different source manifest fails; a new ID imports a changed
snapshot. The source tree is checked after upload and is never used as a
projection target.

`MOLT_KERNEL_BACKEND=kernel` selects an opt-in primary CLI path when
`MOLT_KERNEL_SOCKET` is also set. Each command materializes the current
generation into a disposable directory, runs the existing Python domain
operation there, and publishes the resulting durable state as one kernel
generation. The caller's `--state` path is a logical identifier and is not
opened or written by this mode; an existing path is rejected so a v1 tree
cannot be mistaken for the kernel state. A failed domain command discards its temporary
projection; a failed kernel commit reports an incomplete commit and leaves the
kernel's last confirmed generation authoritative. This keeps the v1 writer
away from v2 data, although it still writes the disposable v1-format
projection before the kernel commit.

The production default remains `v1`. ADR-0015 has accepted the persistence,
transaction, threat-boundary, and migration architecture, but has not approved
the production default switch. Enabling the kernel backend by default has UDS
integration coverage for the retained stateful CLI workflows:
fixture search, offline propose, adopt, revert, fixture calibration, doctor
recovery, and replay demo followed by CLI semantic audit. The kernel-primary
projection is also audited directly after replay demo. The production default
still requires its separate release gate: a supported kernel service lifecycle,
diagnosable failure behavior without fallback, enforced v1 read-only compatibility
for one full release, packaged-kernel and migration checks, and versioned release
evidence. See the release gate in [ADR-0015](../decisions/0015-v2-kernel.md).
Until every gate passes and a maintainer explicitly approves the switch, the
default stays `v1`.

The Linux systemd user-service example and operator procedure are documented in
[`molt-kernel-systemd.md`](../operations/molt-kernel-systemd.md). CI now builds
and tests the Rust kernel, runs the Python UDS integration suite including
duplicate-service and stale-socket recovery, and publishes a Linux x86_64 kernel
binary artifact for each workflow run. This is build evidence, not yet a signed
release package or installation acceptance. If separately approved, the first
kernel-default formal release is R: v1 reading, audit, and explicit migration
remain available through all of R, with removal considered no earlier than its
successor and only after a separate decision and release note. R rejects every
v1 state write before persistence. Linux x86_64 is the initial candidate
support scope; other platforms must remain on v1 or reject kernel installation.
Restoring an older executable and the original v1 snapshot returns to migration
time and does not recover later v2 writes; keep a separate verified v2 backup.
R's exact version and the compatibility mechanism remain subject to the separate
default-switch review.

## M3 gate

Default switching remains disabled until the v2 primary-backend path and
ADR-0015 release gates are satisfied. A dry run must report source file count,
bytes, per-file digest differences, and
the target state ID without mutating either state. Migration must preserve the
source tree, be idempotent for the same source digest, and refuse a changed
source after a completed migration unless given a new migration identity.

The v1 writer must be removed from migrated v2 paths, not retained as a fallback
that can mutate the v2 state. During R, source v1 trees remain readable and
auditable, while all v1 writes (including recovery, adopt, revert, and indirect
storage helpers) fail before persistence. Rollback is a code/configuration
switch to the original v1 snapshot at migration time; it does not bring forward
later v2 writes, which require their own verified backup and recovery procedure.
