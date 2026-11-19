# v2 kernel backup and restore

This procedure targets the `1.1.0` Linux x86_64 release installed with
`packaging/release/install-user.sh`. Substitute the installed release directory
and state path when running it. R is `1.1.0`; v1 remains read-only throughout
the `1.1.x` release line. The release binding and compatibility window are in
[`v2-release-1.1.0.md`](v2-release-1.1.0.md).

## Backup

Keep the kernel state and an exported checkpoint in separate protected storage.
The checkpoint is a chain reference, not a copy of the state contents.

```bash
release="$HOME/.local/share/molt/releases/1.1.0-linux-x86_64"
state="$HOME/.local/share/molt/kernel"
backup="$HOME/.local/state/molt-backups/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -m 0700 -p "$backup"
systemctl --user stop molt-kernel.service
"$release/bin/molt-kernel" audit --state "$state"
"$release/bin/molt-kernel" checkpoint export --state "$state" \
  --out "$backup/checkpoint.json" --created-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cp -a "$state" "$backup/state"
"$release/bin/molt-kernel" audit --state "$backup/state" \
  --checkpoint "$backup/checkpoint.json"
(cd "$backup" && sha256sum checkpoint.json > checkpoint.json.sha256)
(cd "$backup" && sha256sum -c checkpoint.json.sha256)
```

Copy the completed backup directory to storage independent of the machine and
verify the checkpoint checksum after transfer. Keep the service stopped during
the state copy so the directory tree represents one consistent point.

## Restore

Restore to an empty state path. Preserve the failed state for diagnosis rather
than overlaying the backup onto it.

```bash
release="$HOME/.local/share/molt/releases/1.1.0-linux-x86_64"
backup="/path/to/verified/molt-backup"
restore="$HOME/.local/share/molt/kernel-restore"
(cd "$backup" && sha256sum -c checkpoint.json.sha256)
test ! -e "$restore"
cp -a "$backup/state" "$restore"
"$release/bin/molt-kernel" audit --state "$restore" \
  --checkpoint "$backup/checkpoint.json"
```

Only after the audit succeeds should the operator configure the service to use
the restored state path, start it, and verify readiness and current state:

```bash
systemctl --user start molt-kernel.service
"$release/bin/molt-kernel" ping --socket "$restore/kernel.sock"
"$release/bin/molt-kernel" audit --state "$restore" \
  --checkpoint "$backup/checkpoint.json"
```

Keep the previous state untouched until the restored service has passed the
application-level checks. The checkpoint verifies that the restored log
contains the referenced committed prefix; it does not make separate filesystems
or the checkpoint copy part of one atomic transaction.

## Rollback to v1

Stop the kernel service before changing executables or state paths. Rollback
uses the v1 snapshot taken at migration time. Restoring an older executable and
that original v1 snapshot returns to the migration-time state; it does not
bring forward any later v2 writes. Preserve the separate verified v2 state
backup so those later writes remain recoverable.

Do not point the v1 writer at the v2 kernel state directory. Validate the
restored v1 snapshot with the matching v1 release and its audit procedure before
resuming writes. Record the selected release, migration ID, v1 snapshot, v2
backup, checkpoint digest, and restore test result in the release record.
