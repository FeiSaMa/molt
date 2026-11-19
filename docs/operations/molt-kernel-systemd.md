# Linux user service for molt-kernel

This is the supported supervised service example for a single-user Linux host
with systemd on the supported release architecture, Linux x86_64. Other
platforms reject the kernel default path. `1.1.0` uses kernel by default; set
`MOLT_KERNEL_SOCKET` for the service endpoint. `MOLT_KERNEL_BACKEND=v1` is
read-only compatibility mode for legacy state.

Install the release binary at `~/.local/bin/molt-kernel`, then initialize the
state exactly once before enabling the service:

```sh
install -D -m 0755 molt-kernel "$HOME/.local/bin/molt-kernel"
mkdir -p "$HOME/.local/share/molt"
"$HOME/.local/bin/molt-kernel" init --state "$HOME/.local/share/molt/kernel"
install -D -m 0644 packaging/systemd/molt-kernel.service \
  "$HOME/.config/systemd/user/molt-kernel.service"
systemctl --user daemon-reload
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

For an upgrade, stop the unit, replace the binary, run
`molt-kernel audit --state "$HOME/.local/share/molt/kernel"`, then start the
unit and verify protocol readiness. Keep a verified backup of the state
directory and an independently stored checkpoint before upgrading. Do not run
two kernel binaries against one state.
Rollback replaces the executable/configuration and uses the original v1
snapshot from migration time; it never opens or downgrades v2 state with a v1
writer. Later v2 writes are not automatically carried into that v1 snapshot.
Keep a separate verified v2 backup/export and rehearse its recovery before
relying on rollback.

This service unit is a deployment example, not a signed package or an automatic
installer. The `1.1.0` release bundle includes an installer and the release-bound
backup and recovery procedure.
