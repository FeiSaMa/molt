# v2 Release Checklist

## Candidate scope

- Release R: `1.1.0` (Linux x86_64).
- Supported installation target: Linux x86_64 with systemd user manager and `uv`.
- Python package compatibility: Python `>=3.11`; CI and project baseline use Python `3.12.13`.
- Other platforms remain on the v1 backend and must not install this kernel bundle.
- The default backend is kernel on the supported target; v1 is read-only for all
  `1.1.x` releases. The approved decision and release notes are in
  [`v2-release-1.1.0.md`](v2-release-1.1.0.md).

## Build and installation checks

- [x] Build `packaging/release/build-linux-x86_64.sh`; verify the adjacent SHA-256 file.
- [x] Extract into a clean directory and install the wheel in a clean environment.
- [x] Run schema and scenario validation using the bundled data root, outside the checkout.
- [x] Install with `install-user.sh` in an isolated HOME; confirm CLI and kernel paths resolve
      to the same versioned release directory.
- [x] Initialize an isolated state, load the persistent unit, and verify readiness ping and audit.
- [x] Kill the supervised process; verify restart count and changed PID, then stop cleanly.
- [x] Restart the installed release against the same state and verify unchanged state identity.
- [x] Verify the regular user service and its state were not changed by the rehearsal.

## Release gates

- [x] Record failure/unknown-request, disk-full and permission-error recovery checks against
      the exact `1.1.0` archive. ENOSPC evidence:
      `/home/feisama/.local/state/molt-r110-enospc-20261119/exact-bundle-enospc.json`.
- [x] Rehearse persistent service install, SIGKILL recovery, clean restart and same-state audit
      against the exact `1.1.0` archive; evidence is in
      [`v2-candidate-evidence.md`](../research/v2-candidate-evidence.md).
- [x] Rehearse backup, checkpoint, restore, rollback and v1 read-only migration behavior
      against the exact `1.1.0` archive. Rollback used a wheel rebuilt from historical
      v1.0.0 source commit `a1aaee4`; the original published v1 artifact was unavailable.
      Full report: `/home/feisama/.local/state/molt-v1-rollback-20261119/formal-r110/rollback-rehearsal.json`.
- [x] Record the independent formal release decision for R=`1.1.0` and the complete `1.1.x`
      v1 read-only compatibility window in [`v2-release-1.1.0.md`](v2-release-1.1.0.md).

Earlier candidate evidence is recorded in [`v2-candidate-evidence.md`](../research/v2-candidate-evidence.md).
It does not replace the release-bound checks above.
