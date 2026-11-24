# Linux user service for molt-kernel

This is the supervised service example for a single-user Linux host with
systemd on Linux x86_64. Set `MOLT_KERNEL_SOCKET` for the service endpoint.
The rc8 package has been exercised in isolated transient user-manager units.
That evidence does not cover persistent enablement, login auto-start, or a
production deployment.
The current CLI uses initialized kernel state and rejects `MOLT_KERNEL_BACKEND=v1`.

Run the release bundle's `install-user.sh` to install a matched CLI, TUI and kernel,
switch the user entry points, and write the unit. Then initialize the state
exactly once before starting the service:

```sh
mkdir -p "$HOME/.local/share/molt"
"$HOME/.local/bin/molt-kernel" init --state "$HOME/.local/share/molt/kernel"
systemctl --user enable --now molt-kernel.service
```

The service owns `~/.local/share/molt/kernel/kernel.sock`; the kernel creates
it with mode `0600`. The service unit makes the filesystem read-only except for
the state directory, uses a private temporary directory and `UMask=0077`, and
restarts after unexpected process exit. Keep the state on a local filesystem that supports
`flock`, same-directory rename, and file/directory `fsync`.

Check process state with `systemctl --user status molt-kernel.service`. Its
`ExecStartPost` runs `molt-kernel ping --socket ...`, which succeeds only after
the protocol handshake returns a state identity. Callers can use the same
read-only readiness probe. If a later request loses its connection, the CLI
returns failure and never falls back to v1.

Stop or restart with `systemctl --user stop|restart molt-kernel.service`.
SIGTERM stops the server process and leaves its socket path for safe recovery;
the next server start removes it only after acquiring the state-specific service
lock, confirming the path is a socket, and confirming it refuses connections.
An active server is protected by the lock. A regular file or symlink at the
socket path is never removed and requires operator investigation. Concurrent
manual starts fail with an explicit `kernel server already running` error.

For an upgrade, retain a verified backup of the state directory and an
independently stored checkpoint before upgrading. Install the new bundle,
restart the unit, run `molt-kernel audit --state "$HOME/.local/share/molt/kernel"`,
and verify protocol readiness. Do not run two kernel binaries against one state.
Rollback replaces the executable/configuration and uses the original v1
snapshot from migration time; it never opens or downgrades v2 state with a v1
writer. Later v2 writes are not automatically carried into that v1 snapshot.
Keep a separate verified v2 backup/export and rehearse its recovery before
relying on rollback.

This service unit is a deployment example, not a signed package or an automatic
installer. The `1.2.0rc9` bundle is a local candidate. Before a
public or production deployment, record the exact bundle checksum, source
fingerprint, persistent-user-manager test, backup and recovery evidence, and
the responsible operator. The historical `1.1.0` release bundle and its
procedure are retained as archival evidence.
