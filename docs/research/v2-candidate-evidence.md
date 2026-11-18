# v2 kernel candidate evidence

This is a validation record for the current uncommitted candidate, not approval
to change the production default. The production backend remains v1; M4
implementation authorization remains closed.

## Candidate identity

- Base commit: `a1aaee4de4acec2e8272492dcb8412ea207df4ab`
- Package version built: `1.1.0rc1` (pre-release candidate; not release R)
- Platform: Linux x86_64, kernel `7.3.4-zen2-2-zen`
- Python: `3.12.13`
- Rust: `rustc 1.100.0 (0ef1bc386 2026-11-09)`
- uv: `0.13.8 (3d97a6aad 2026-11-12)`
- systemd: `262.2-1-arch`
- Worktree content fingerprint (excluding this evidence file):
  `4b28f8b87def6a806fd7e3526e989fb5a897d5f7d017546eb1d3f188ba025689`

The base commit alone does not identify this candidate: the worktree contains
uncommitted tracked and untracked changes. The fingerprint command below covers
all non-ignored repository files except this self-referential evidence file.

## Passed evidence

- Read-only compatibility boundary: `tests/test_kernel_cli_contract.py`,
  `tests/test_boundary.py`, and `tests/test_zero_write.py`: **11 passed**.
  The file-system snapshots cover source contents, modes, directory entries,
  symlinks, rejected operations, and failure entry paths.
- Full Python suite: **377 passed, 4 skipped** in 64.73s. The skips were two
  explicit live-Pi tests and two kernel integration tests requiring
  `MOLT_KERNEL_BIN`; the latter were subsequently run with the locally built
  binary and **both passed** in `tests/test_kernel_client.py`.
- Rust workspace with fault injection: **20 + 1 + 5 passed** after the package
  response-disconnect regression was added.
- `cargo fmt --all --check`: passed.
- `cargo clippy --workspace --all-targets --features molt-kernel/fault-injection -- -D warnings`: passed.
- Ruff: passed.
- Schema validation: 36 records passed; demo scenario validation passed.
- `doctor --deps` and `doctor --repo-policy`: passed.
- `git diff --check`: passed.
- `systemd-analyze verify /home/feisama/.config/systemd/user/molt-kernel.service`: passed.
- uv packaging: `uv build --out-dir /tmp/molt-uv-artifacts` built the source
  distribution and wheel. The wheel was installed with `uv pip install` into a
  fresh `/tmp/molt-uv-clean312` environment using Python 3.12.13. The installed
  `molt --help`, `molt doctor --deps`, package import location, kernel protocol
  handshake (`molt-kernel/1`), and read-only `current` call passed from outside
  the repository.
- Real user-manager smoke: service started with `ActiveState=active`,
  `NRestarts=0`, and a nonzero `MainPID`; installed Python completed handshake
  and queried current state. Service was stopped after the smoke test and
  verified `ActiveState=inactive`, `MainPID=0`.
- User-manager recovery and upgrade rehearsal: a temporary supervised unit
  recovered after `SIGKILL` with a new PID, then the same isolated state was
  stopped and reopened by the release binary. Both restarts passed protocol
  ping and audit with state ID
  `sha256:ca4d815f658812dfbed47d54401b74464536447daace4d68eaf88950cf06c1b8`.
  The units were `molt-kernel-upgrade-debug-17873.service` and
  `molt-kernel-upgrade-release-17873.service`; test state is retained at
  `/tmp/molt-systemd-upgrade.ISFvst`.

## Skips and open gates

- Live Pi tests remain skipped because external Pi execution was not enabled.
- This candidate documents and tests Linux x86_64 only. Other platforms must
  remain on v1 or reject kernel installation until separately validated.
- The current manager rehearsal uses temporary transient units and an isolated
  state. It does not replace a release installation rehearsal using the
  packaged persistent service unit.
- `systemd-analyze verify` is static unit validation; it is not a substitute for
  the user-manager lifecycle evidence.
- No release R has been selected. ADR-0015 reviewed the default switch and did
  not approve it. v1 reads, audits, and explicit migration remain supported
  for the full R release cycle; removal requires a later decision and release
  note.
- Rollback to an older executable and the original v1 snapshot returns to the
  migration-time state. It does not restore later v2 writes; preserve and verify
  a separate v2 backup.

## 1.1.0rc1 package rehearsal

- The corrected release bundle is
  `/tmp/molt-release-20261118-final2/molt-1.1.0rc1-linux-x86_64.tar.gz`,
  SHA-256 `3bfdae7bbc77ddb27c440090c1ad16b7513cb7f75a893a9d5d5454d85a1d0464`.
  It contains the response-disconnect fix and the corrected filesystem
  permission error code. The earlier bundle hash below predates those fixes and
  must not be installed.
- Against the extracted corrected bundle, package-level tests covered
  permission-denied and ENOSPC writes, same-ID retry, a durable commit with its
  response discarded, restart and `request.status` resolution, and duplicate
  retry suppression. An isolated 8 MiB tmpfs was used for ENOSPC.
- Backup recovery exported a checkpoint, copied the complete stopped state to
  a separate directory, verified the checkpoint against the copy, audited it,
  and started the kernel against the restored copy. The state ID remained
  `sha256:141ec2c5863d3f4c8d5a64dbd0cb39a43f81404b85dfe54fc0cbca48e916a2f7`.
- Run evidence: `/tmp/molt-package-failure-final-20261118.json`,
  `/tmp/molt-package-disk-final-20261118.json`,
  `/tmp/molt-rust-20261118-final.log`,
  `/tmp/molt-clippy-20261118-final.log`, and
  `/tmp/molt-release-build-20261118-final2.log`.
- Operator procedure for stop, checkpoint, independent backup, restore and v1
  rollback: [`v2-backup-restore.md`](../operations/v2-backup-restore.md).

- Bundle: `/home/feisama/.local/state/molt-release-artifacts/molt-1.1.0rc1-linux-x86_64.tar.gz`
- SHA-256: `93c0ca44a5b832f1b6a7eae54ef77bf436e62f2f4ee58fbd229b72a65d3e8d6d`
- Bundle contains the Python wheel, release kernel binary, runtime data root,
  persistent systemd user unit and user installer. Build and installer scripts
  reject non-Linux or non-x86_64 targets.
- Extracted-wheel smoke outside the checkout passed with Python 3.13.15; the
  final installer rehearsal used Python 3.11.18:
  all 25 bundled schema records, demo scenario validation, and kernel CLI startup.
- `install-user.sh` on the final bundle used isolated HOME
  `/home/feisama/.local/state/molt-rc1-verified.IRPWni` and a uniquely
  named linked persistent unit. Installed schema/scenario checks passed outside
  the checkout; readiness ping and audit passed with state ID
  `sha256:a576935eaf377dd1cc4ed6ea1c81b56c18fedc61c1295b32a8fe40b268c7d84b`.
- SIGKILL recovery changed PID from `15666` to `15706`, with `NRestarts=1`;
  post-recovery audit and a subsequent clean stop/start (PID `15903`) retained
  the same state ID. The final test
  rehearsal unit link was removed with `gio trash`; both it and the regular
  `molt-kernel.service` are inactive. The isolated install and state remain
  available at the path above for inspection. The source-tree test run passed
  377 Python tests with 4 skips, 20 + 1 + 5 Rust tests, formatting, Clippy,
  Ruff, lock consistency and diff checks. The four skips were two live-Pi tests
  and two kernel integration tests without `MOLT_KERNEL_BIN`; the latter were
  run separately against the candidate kernel and passed.
- The initial rc1 package recovery experiment found that a disconnected client
  could make the server exit on `Broken pipe`; permission errors were also
  mislabeled as `handshake_required`. Both issues were fixed. The new Rust
  regression `disconnected_commit_caller_does_not_stop_server` passes, and the
  filesystem error code is now `permission_denied`.
- Corrected bundle:
  `/tmp/molt-release-20261118-final2/molt-1.1.0rc1-linux-x86_64.tar.gz`,
  SHA-256 `3bfdae7bbc77ddb27c440090c1ad16b7513cb7f75a893a9d5d5454d85a1d0464`.
  The previous artifact at `/tmp/molt-release-20261118/molt-1.1.0rc1-linux-x86_64.tar.gz`
  (SHA-256 `fe107169ecd3ab6fa02af7fd373f4f5d9ec339a9a0636dacf816425b576756db`)
  predates the fix and must not be used as the candidate.
- Package-level permission failure returned `permission_denied`; after restoring
  directory permissions, the same request ID committed exactly generation 1.
  An 8 MiB tmpfs filled to `ENOSPC` rejected a write; after freeing space, the
  same request ID committed exactly generation 1 and audit passed.
- A commit whose response was discarded was found as `committed` by
  `request.status` after service restart. Retrying the same request ID returned
  generation 2 without a duplicate. Checkpoint export, independent state copy,
  checkpoint verification, audit, and serving the restored copy passed with an
  unchanged state ID.
- Logs: `/tmp/molt-package-failure-final-20261118.json`,
  `/tmp/molt-package-disk-final-20261118.json`,
  `/tmp/molt-rust-20261118-final.log`,
  `/tmp/molt-release-build-20261118-final2.log`.
- These checks close the package-level failure and recovery item for the
  corrected bundle. Remaining gates are selecting formal release R and its v1
  compatibility window, publishing release-specific backup/checkpoint/
  restore/rollback instructions, and separate approval for a default switch.
  The production default remains v1.

## Kernel-first development and repeatable recovery (2026-11-18)

- README now documents an isolated kernel-primary replay/audit workflow. The
  production default and v1 compatibility policy remain unchanged.
- Release bundles now include the backup/restore, release checklist and service
  procedures; the installer prints the installed recovery-document path. Backup
  checkpoint checksum files use relative names so transfer does not retain a
  source-machine absolute path.
- Added `test_kernel_backup_restores_replay_evidence`: create a full replay state
  through the primary backend, stop the service, export a checkpoint, copy the
  state without its runtime socket, move the source offline, restore into a new
  directory, verify the checkpoint and current generation, then run CLI and
  direct Python semantic audits against the restored evidence. CI runs it both
  in the UDS suite and against the extracted release kernel.
- Local verification used Python 3.12.13: Rust fault-injection suite 26 passed;
  Python kernel client/CLI/canonical-vector suite 9 passed; lint, 36 schema
  records and the 15-task demo scenario passed. The extracted release kernel
  also passed the new recovery test (1 passed). This is not a full Python-suite
  or systemd installation rehearsal.
- Rebuilt bundle: `/tmp/molt-v2-transition-release/molt-1.1.0rc1-linux-x86_64.tar.gz`,
  SHA-256 `64f86dc054ac8f487fb376d3169bcba9f4f789fb44ce121512d59df339624cd3`.
  This development rebuild includes the recovery documentation; the earlier
  corrected candidate remains the reference for its recorded systemd and
  filesystem failure rehearsals, which were not repeated on this rebuild.
- Logs: `/tmp/molt-v2-transition-rust.log`,
  `/tmp/molt-v2-transition-python.log`,
  `/tmp/molt-v2-transition-package-recovery.log`,
  `/tmp/molt-v2-transition-package.log`.

## Reproduction commands

Commands below were run from `/home/feisama/dev/molt`; Python commands use the
working-tree source unless they explicitly invoke `/tmp/molt-uv-clean312`.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/feisama/dev/molt UV_CACHE_DIR=/tmp/molt-uv-cache uv run --no-sync pytest -q
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/feisama/dev/molt MOLT_KERNEL_BIN=/home/feisama/dev/molt/target/debug/molt-kernel UV_CACHE_DIR=/tmp/molt-uv-cache uv run --no-sync pytest -q tests/test_kernel_client.py
cargo test --workspace --features molt-kernel/fault-injection --quiet
cargo fmt --all --check
cargo clippy --workspace --all-targets --features molt-kernel/fault-injection -- -D warnings
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/feisama/dev/molt UV_CACHE_DIR=/tmp/molt-uv-cache uv run --no-sync ruff check .
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/feisama/dev/molt UV_CACHE_DIR=/tmp/molt-uv-cache uv run --no-sync python -m molt schema validate --all
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/feisama/dev/molt UV_CACHE_DIR=/tmp/molt-uv-cache uv run --no-sync python -m molt scenario validate --scenario scenarios/demo/scenario.json
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/feisama/dev/molt UV_CACHE_DIR=/tmp/molt-uv-cache uv run --no-sync python -m molt doctor --deps
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/feisama/dev/molt UV_CACHE_DIR=/tmp/molt-uv-cache uv run --no-sync python -m molt doctor --repo-policy
systemd-analyze verify /home/feisama/.config/systemd/user/molt-kernel.service
```

Fingerprint recipe (run after all candidate changes are complete):

```bash
git ls-files -co --exclude-standard -z \
  | grep -z -v -F 'docs/research/v2-candidate-evidence.md' \
  | sort -z \
  | xargs -0 sha256sum \
  | sha256sum
```
