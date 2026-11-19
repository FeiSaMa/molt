# v2 kernel candidate evidence

This file records the formal `1.1.0` release decision and package-bound
validation, followed by historical candidate evidence. The `1.1.0` release
decision is approved and package acceptance passed for the archive hash recorded
below. M4 implementation authorization remains closed.

## Formal 1.1.0 archive acceptance (2026-11-19)

- Final documentation-complete archive:
  `/home/feisama/.local/state/molt-release-1.1.0-final2-20261119/molt-1.1.0-linux-x86_64.tar.gz`.
  SHA-256 `4d5b4a04675971480ee593dd0210a91db952288144fa9472c8e79275f51845ad`;
  the adjacent checksum verified. The earlier accepted archive
  `/home/feisama/.local/state/molt-release-1.1.0/molt-1.1.0-linux-x86_64.tar.gz`
  has SHA-256 `c1861b3b30e8c442eb431444eebfe964b6caf0ac66d2a2c14d1cea95d66ab1c3`.
  Extracted trees were compared: outside the release checklist and release plan
  documents (and test-generated `__pycache__`), all bundle files are identical;
  the wheel SHA-256 is `8b3bef2a702453aa8d326b584b15410a4a8c22ed107211699295678edbd38fbf`
  in both archives. Thus the earlier exact-runtime service, failure, migration
  and rollback rehearsals apply to the unchanged wheel and kernel payload in this
  final archive. The final archive's own package smoke and isolated kernel tests
  are recorded below. The extracted wheel reports version `1.1.0`
  and `DEFAULT_BACKEND=kernel` in an isolated Python 3.12.13 environment.
- The packaged-wheel verifier passed all 25 bundled schema records, demo
  scenario validation, kernel init/audit, and replay backup/checkpoint/restore
  with semantic audit (**1 passed**, 8.43s). The complete copied
  `tests/test_kernel_client.py` suite ran against this archive's kernel binary
  and installed wheel: **3 passed** (39.89s), including restored-state service
  and semantic audit.
- A transient user-manager unit ran the exact archive binary against isolated
  state `sha256:cdc0627a85103a1b393de23ec6c9e42471d82dc590debfc36281b49787144b96`.
  SIGKILL changed PID `28242` to `28326`, `NRestarts=1`, and retained the state
  ID. A fresh transient unit then started against the same state; ping and
  kernel audit passed. The regular `molt-kernel.service` remained inactive.
- An isolated v1-format replay state passed installed-wheel semantic audit
  (**31 runs, 4 results, 1 decision, 26 events**). Audit and migration preview
  each left an exact directory metadata and content-hash snapshot unchanged.
  Preview reported **254 files / 1,905,541 bytes** and generation 0; no import
  was applied.
- Exact-bundle failure checks passed for `permission_denied` followed by a
  successful upload after repair, and for a deliberately discarded commit
  response. After restart, `request.status` returned `committed`; retrying the
  same request ID returned generation 1 without duplication. Results are in
  `/home/feisama/.local/state/molt-r110-acceptance.vpNczn/exact-bundle-failure-paths-final.json`.
- Final source checks on 2026-11-19 passed: Python **377 passed, 5 skipped**;
  Rust fault-injection workspace **20 + 1 + 5 passed**; fmt, Clippy, Ruff,
  37 source schema records, scenario, both doctor checks, systemd unit
  validation, shell syntax, and `git diff --check`.
- The initial host-level `pkexec` attempt could not show a dialog because no
  authentication agent is installed or running. The exact R ENOSPC check was
  then completed without host privileges in an unprivileged user+mount
  namespace using an isolated 8 MiB tmpfs. After filling **8,368,128 bytes**,
  `generation.commit` returned `io_error` / `No space left on device (os error
  28)`. After service restart and freeing space, the same request ID committed
  generation 1; a duplicate retry returned the same result and audit passed.
  Evidence: `/home/feisama/.local/state/molt-r110-enospc-20261119/exact-bundle-enospc.json`.
- The exact R wheel migrated a copied v1 snapshot into the exact R kernel
  (254 files, generation 1). A path/content hash fingerprint confirmed the
  source v1 snapshot was unchanged. The R checkpoint was exported, the stopped
  kernel state was copied independently, and the copied state passed checkpoint
  audit with state ID `sha256:3d70e232f777191907a437a78a92126b41d20e05e6174d450d11e1cc130ab71a`.
  Rollback then used a separate copy of the original v1 snapshot with a wheel
  rebuilt from historical source commit `a1aaee4de4acec2e8272492dcb8412ea207df4ab`
  (`molt` 1.0.0, wheel SHA-256
  `1d0067af151f8f16b4f171e3796166435c81f77a1d035ab663aa818b00127c64`): fixture
  search wrote 10 runs and the old v1 audit passed. The original published v1
  artifact/tag was not found; this is a source-reconstructed rollback rehearsal,
  not a claim that the rebuilt wheel is byte-identical to a previously published
  artifact. Full report: `/home/feisama/.local/state/molt-v1-rollback-20261119/formal-r110/rollback-rehearsal.json`.
- The user's regular service was left inactive; no commit, push, public
  publication, or migration of the user's state was performed. M4/M5 remain
  outside this release authorization.
- Final-archive revalidation: `packaging/release/verify-bundle.sh` passed against
  the final SHA above (25 bundled schemas, demo scenario, kernel init/audit,
  replay backup/restore semantic audit **1 passed**, systemd unit verification).
  Copied package-level CLI contract tests passed **5 passed**, and the full
  copied kernel client suite passed **3 passed** when run from the bundle data
  root. Source revalidation passed Python **377 passed, 5 skipped**, Rust
  fault-injection **20 + 1 + 5 passed**, Ruff, Clippy, fmt, 36 source schemas,
  scenario, doctor deps/repo-policy, shell syntax, systemd unit verification,
  and `git diff --check`. Logs: `/tmp/molt-r110-closeout-python.log`,
  `/tmp/molt-r110-closeout-rust.log`, `/tmp/molt-r110-closeout-clippy.log`;
  package verification directory:
  `/home/feisama/.local/state/molt-release-1.1.0-final2-20261119/verification`.

## Isolated wheel and CI review (2026-11-18)

- Current base: `10e22e4266d285aa4dd3b2c010e77bc0e7768150`, branch
  `refactor/v2-m1-kernel`, plus the uncommitted review changes. No commit or push
  was made during this review; the pre-existing evidence additions were retained.
- Current source fingerprint, excluding this evidence file:
  `4453cb9e95ec225e805f6248f41d1e213735c05412cbc12e378aae37d9ef08f0`.
- New development bundle:
  `/tmp/molt-review-final-release/molt-1.1.0rc1-linux-x86_64.tar.gz`, SHA-256
  `77845a69b969f8caaedb7e19e5b97180efc683c190e07b1599860b934e7fd09c`.
  The adjacent checksum passed. This is rc1 development evidence, not release R.
- Fixed the CI coverage gap where the packaged-kernel recovery test imported
  Python from the checkout. `packaging/release/verify-bundle.sh` installs the
  wheel and locked dependencies, copies the test outside the checkout, runs
  isolated Python, and points CLI operations at bundled data. The observed
  import was `/tmp/molt-wheel-final/venv/lib/python3.12/site-packages/molt/__init__.py`.
  Conflicting `PYTHONPATH`, `PYTHONHOME` and backend/socket environment variables
  were supplied and cleared successfully. An in-checkout output directory and
  an existing output directory were both rejected without replacing contents.
- Installed-wheel schema validation passed all 25 bundled records, the demo
  scenario passed, kernel init/audit passed, and replay backup/checkpoint/restore
  with semantic audit passed (**1 passed**, 7.39s). The service template was
  rendered with this bundle's binary and isolated state paths before
  `systemd-analyze verify`, avoiding any preinstalled user binary dependency.
- Rust fault-injection workspace tests: **20 + 1 + 5 passed**. Rust fmt/clippy,
  Ruff, shell syntax, `git diff --check`, 36 source-tree schema records, demo
  scenario, `doctor --deps` and `doctor --repo-policy` passed.
- Release-kernel UDS and CLI contract tests: **8 passed** (28.91s). Full Python
  suite, run serially with its own TMPDIR: **377 passed, 5 skipped** (63.46s).
  The initial concurrent run had one staging-cleanup assertion failure because
  another test process changed the shared `/tmp/molt-verify-*` set; the isolated
  cleanup test passed before the serial full rerun. No verifier or frozen task
  content was changed to hide that failure.
- CI now runs CLI contract tests as well as UDS tests, checks the archive digest,
  and invokes the same isolated bundle verifier. The workflow's commands were
  exercised locally; no remote GitHub Actions run is claimed.
- Prepared the proposed `1.1.0` release record, all-`1.1.x` v1 read-only window,
  and exact-release backup/restore binding in
  [`v2-release-1.1.0.md`](../operations/v2-release-1.1.0.md), included in this bundle.
  The package version and default remain `1.1.0rc1` and v1. Formal R acceptance,
  publication and independent default-switch approval remain open; M4/M5
  implementation is not authorized by this review.
- The regular user service was observed `ActiveState=inactive`, `MainPID=0`,
  `NRestarts=0`. This review did not install or start it. Earlier persistent
  systemd, disk-full and permission-error rehearsals were not repeated against
  this development archive and must not be attributed to its digest.
- Logs: `/tmp/molt-review-20261118-build-final.log`,
  `/tmp/molt-review-20261118-wheel-final.log`, `/tmp/molt-review-20261118-rust.log`,
  `/tmp/molt-review-20261118-clippy.log`, `/tmp/molt-review-20261118-uds.log`,
  `/tmp/molt-review-20261118-python-final.log`, `/tmp/molt-review-20261118-staging.log`,
  `/tmp/molt-review-20261118-policy.log`, `/tmp/molt-review-20261118-refusal.log`,
  `/tmp/molt-review-20261118-existing-dir.log`. The initial failed run is retained
  in `/tmp/molt-review-20261118-python.log`.

## Historical candidate identity

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
- Follow-up on the `refactor/v2-m1-kernel` worktree (base `6899104`): the full
  Python suite passed (**377 passed, 5 skipped**); kernel UDS tests passed
  (**3 passed**) and the extracted bundle recovery test passed (**1 passed**).
  Ruff, Rust formatting/clippy, all 36 schema records, demo scenario,
  `doctor --deps`, `doctor --repo-policy`, bundle documentation presence and
  `systemd-analyze verify` passed. This run does not replace the earlier
  systemd installation or filesystem failure rehearsals.
- Rebuilt bundle: `/tmp/molt-v2-transition-release/molt-1.1.0rc1-linux-x86_64.tar.gz`,
  SHA-256 `64f86dc054ac8f487fb376d3169bcba9f4f789fb44ce121512d59df339624cd3`.
  This development rebuild includes the recovery documentation; the earlier
  corrected candidate remains the reference for its recorded systemd and
  filesystem failure rehearsals, which were not repeated on this rebuild.
- Additional worktree bundle: `/tmp/molt-v2-worktree-release/molt-1.1.0rc1-linux-x86_64.tar.gz`,
  SHA-256 `e7ce673ab9980062570e9c761796be647bf5b01c783d5b9148d80d10c2b52baa`.
  It passed the extracted-kernel backup/restore test; it is a development build,
  not a new systemd or failure-path rehearsal.
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
