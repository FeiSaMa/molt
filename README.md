# molt

[简体中文](README.zh-CN.md)

molt is a CLI for versioning and auditing local agent workspaces. Save OpenCode
configuration and tool files as snapshots, switch or merge branches, and roll
back the active version. Each new job can read a fixed snapshot. Audit records
preserve the original evidence and the operator's reason for a change.

The Rust kernel stores content-addressed objects and version history and exposes
transactions over a Unix socket. The Python CLI connects the kernel to local workflows.

## Run from source

The following steps start an isolated instance on Linux. You'll need
[uv](https://docs.astral.sh/uv/) and [Rust](https://rustup.rs/).

From the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 uv sync --locked --python 3.12.13
cargo build --locked -p molt-kernel
molt_workspace=$(mktemp -d /tmp/molt-XXXXXX)
target/debug/molt-kernel init --state "$molt_workspace/state"
printf 'Socket: %s\n' "$molt_workspace/state/kernel.sock"
target/debug/molt-kernel serve --state "$molt_workspace/state"
```

Leave the service running. In another terminal, return to the same repository and
set the socket path to the one printed above.

```bash
export MOLT_KERNEL_SOCKET=/tmp/molt-XXXXXX/state/kernel.sock
PYTHONDONTWRITEBYTECODE=1 uv run --locked molt inspect
PYTHONDONTWRITEBYTECODE=1 uv run --locked molt current
PYTHONDONTWRITEBYTECODE=1 uv run --locked molt audit
```

The [transaction example](docs/operations/external-integration.md) walks through
upload, commit, and lookup. If the outcome of a commit is unknown, use
`molt request-status` to look up the original request ID and reuse the same ID and
parameters during recovery.

## OpenCode project snapshots

Follow [installation and first run](docs/operations/version-cli.md#安装与首次运行)
to unpack the bundle and start an isolated workspace. Stop at
`test "$ready" = 1` and continue in the same Bash session.

```bash
mkdir -m 700 "$base/edit" "$base/exports"
cat > "$base/project.json" <<'JSON'
{"workspace":"w","edits":"edit","exports":"exports","files":["opencode.json"]}
JSON
p() { "$base/venv/bin/molt" project --config "$base/project.json" "$@"; }

edit=$(p begin | jq -er '.edit')
printf '%s\n' '{"model":"isolated/demo"}' > "$edit/opencode.json"
initial=$(p save --reason 'Save the trial baseline' | jq -er '.snapshot')

printf '%s\n' '{"model":"isolated/edited"}' > "$edit/opencode.json"
p preview
p save --reason 'Update the sample configuration' > "$base/edited.json"
v audit "$(jq -er '.event' "$base/edited.json")"

p rollback "$initial"
tree=$(p read-tree | jq -er '.tree')
cat "$tree/opencode.json"
```

The last command should print `{"model":"isolated/demo"}`. With OpenCode v2
installed, run `p opencode -- debug config` to see the configuration a new job
reads. The [project CLI guide](docs/operations/version-cli.md#项目级-cli) covers
save conflicts, request recovery, and launch options.

Use `molt version` for the underlying workspace and version commands. The
[workspace guide](docs/operations/version-cli.md) covers branching, merging,
pinned reads, and how an installed offline model can add a digest with
references to the original evidence. The
[source example](docs/operations/synthetic-versions.md) uses the same broker.

## Usage and maintenance

| Task | Documentation |
| --- | --- |
| Query, commit, audit, and recover transactions | [CLI reference](docs/operations/kernel-cli.md) |
| Build and install the Linux bundle or run a user service | [Linux packaging](docs/operations/v2-release-checklist.md) · [User service](docs/operations/molt-kernel-systemd.md) |
| Back up, verify, and restore into a new directory | [Backup and restore](docs/operations/v2-backup-restore.md) |
| Read or migrate older state | [v1 compatibility and migration](docs/operations/kernel-cli.md#v1-只读兼容与显式迁移) |
| Understand the kernel and client boundaries | [Architecture](docs/architecture.md) |
| Build and verify candidate packages | [Contributing](CONTRIBUTING.md) |

molt 1.1.x supports read-only auditing and explicit migration of v1 state.
Rollback changes local state. External operations keep their own transaction
and recovery lifecycle.

## Development

See [AGENTS.md](AGENTS.md) and [Contributing](CONTRIBUTING.md) for development
and review conventions.
