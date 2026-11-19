# 1.1.0 kernel-default release plan

Status: release decision approved; package acceptance passed on 2026-11-19 for
the exact `1.1.0` release archive.
R is `1.1.0`, and the approved kernel default is in effect for Linux x86_64.
Public publication and installation into the user's regular environment have not
been performed.

## Version and compatibility window

- R is `1.1.0`, supported on Linux x86_64 with a systemd user manager.
- All `1.1.x` releases retain v1 reading, semantic audit and explicit migration.
  The compatibility window starts with R.
- Starting with R, v1 writes must fail before persistent mutation, including
  recovery, adoption, rollback, ledger/event and indirect storage writes.
  Kernel projections remain writable temporary workspaces, not legacy state.
- `1.2.0` is the earliest possible removal release. Removal is not automatic:
  it requires a separate decision and release notes. If that decision is absent,
  retain v1 read-only compatibility beyond `1.1.x`.
- Other platforms keep the v1 default or reject the kernel installation. The
  Linux-only bundle is not evidence of cross-platform support.

## Release notes prepared for R

R will use the Rust kernel as the Linux x86_64 persistence backend while Python
continues to own scenario, verifier, gate and semantic audit. v1 schemas and
evidence semantics remain unchanged. A configured, separately supervised kernel
service is required; service failure must not fall back to v1 or another state.
The kernel's hash chain detects tampering relative to a separately protected,
known-latest checkpoint. It does not prove execution truth or resist an attacker
who can replace both the state and that checkpoint.

Before migration, preserve and audit the original v1 snapshot. Preview migration
without writing, then apply it with a stable migration ID. Reuse that ID when a
response is lost; a new ID must not conceal an unknown commit outcome. Keep v1
and v2 state directories separate throughout the compatibility window.

## Backup, recovery and rollback binding

The versioned procedure is [`v2-backup-restore.md`](v2-backup-restore.md). For R,
use `$HOME/.local/share/molt/releases/1.1.0-linux-x86_64` as `release`, the state
path configured in the installed unit as `state`, and a new backup destination.
Stop the service before checkpoint export and independent state copying; audit
the restored copy against the separately preserved checkpoint before serving it.
Verify state identity, current generation and Python semantic audit after restore.
Rehearse using the exact R archive and record its SHA-256, not an rc1 archive.

Rollback means stopping the kernel service, switching code/configuration back,
and opening the untouched v1 snapshot with the old v1 writer. That snapshot does
not include post-migration v2 writes. Preserve and reconcile a separate v2 backup;
do not downgrade v2 data into the v1 directory or claim lossless rollback.

## Release-bound evidence

- Build the exact `1.1.0` wheel/kernel bundle and record its archive SHA-256 and
  source fingerprint. Candidate hashes cannot be relabeled as R evidence.
- Run the isolated wheel/kernel verifier, default-selection and v1 read-only
  tests, migration/parity tests, and persistent installation/service lifecycle
  and failure/unknown-request/disk/permission recovery rehearsals against R.
- Record the versioned backup/checkpoint/restore/rollback rehearsal and publish
  these release notes with the approved archive.
- Decision reference: user's independent formal release decision in the Codex
  task on 2026-11-18. This authorization does not substitute for the exact-R
  package, service and recovery evidence listed above.

M4 implementation and M5 scope remain outside this release plan (ADR-0016).
