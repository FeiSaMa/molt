# v2 kernel backup and restore

Use the bundled kernel with an initialized kernel state on Linux x86_64.
Set the installed bundle path and state path before running the procedure.
For the historical 1.1.0 archive, see [its exact release record](v2-release-1.1.0.md).

## Backup

Keep the kernel state and an exported checkpoint in separate protected storage.
The checkpoint is a chain reference, not a copy of the state contents.

```bash
release=/absolute/path/to/installed/molt-bundle
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
release=/absolute/path/to/installed/molt-bundle
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

## Historical v1 data

Keep the old backup together with the exact release that supported its format.
Use a separate copy for historical reads and exports. Kernel state backups
retain their own generation history. See [the historical release record](v2-release-1.1.0.md).
