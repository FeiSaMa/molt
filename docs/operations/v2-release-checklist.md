# v2 Release Candidate Checklist

## Candidate scope

- Candidate: `1.1.0rc1` (PEP 440); this is not the default-backend release R.
- Supported installation target: Linux x86_64 with systemd user manager and `uv`.
- Python package compatibility: Python `>=3.11`; CI and project baseline use Python `3.12.13`.
- Other platforms remain on the v1 backend and must not install this kernel bundle.
- The production default remains v1 until a separate maintainer release decision.

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

## Remaining approval gates

- [x] Record release-linked failure/unknown-request recovery, disk-full and permission-error
      checks against the corrected `1.1.0rc1` Linux x86_64 bundle. Evidence and bundle hash are
      in [`v2-candidate-evidence.md`](../research/v2-candidate-evidence.md).
- [ ] Bind and publish the backup, checkpoint, restore and rollback procedure for the selected
      release; candidate procedure is in [`v2-backup-restore.md`](v2-backup-restore.md).
- [ ] Name the exact default release R and its full v1 read-only compatibility window.
- [ ] Obtain the separate maintainer approval required by ADR-0015 before changing the default.

The `1.1.0rc1` candidate evidence is recorded in
[`v2-candidate-evidence.md`](../research/v2-candidate-evidence.md). A passing checklist is not
authorization to change the production default.
