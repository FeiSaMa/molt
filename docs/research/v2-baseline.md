# M0 retained baseline

Recorded on 2026-11-15 with Python 3.12.13
(`/tmp/molt-v2-baseline-venv/bin/python`) on branch `refactor/v2-scope-removal`.
The test run predates this document commit; the branch history is
`d720905` → `4a0c8ff` → `9e28b47` → `a419430` → `24bbbdb` → `34bd1ec` → this commit.

## Scope

Retained: `code-repair` and `svc-config` shells, their demo scenarios, the search
and holdout benches, and the core, fixture, deterministic, and Pi adapters.
Retained tests are the repository `tests/` suite after M0 scope removal.

Archived in `attic/`: probe/probe-c2 family, web/export, agent/upper/skills/mcp,
contracts, related assets/scripts, and their historical tests. Attic is excluded
from the wheel, sdist, ruff, and pytest.

The full v1 suite result (874 passed, 2 skipped, 2 failed before `d720905`) is a
historical record, not the M0 gate. The M0 gate is the retained suite below.

## Test baseline

Command, run from a normal host shell (not inside the Codex `workspace-write`
sandbox):

```bash
cd <repo>
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q -rs
```

Result:

```text
371 passed, 2 skipped in 111.44s (0:01:51)
```

Zero failures. The two skips are the opt-in live Pi tests:

| Test | Reason |
| --- | --- |
| `tests/test_demo_command.py::test_demo_live_mode` | 真实 Pi 调用默认跳过；设置 `MOLT_RUN_PI_LIVE=1` 启用 |
| `tests/test_pi_live.py::test_pi_live_smoke` | 同上 |

No `env:socket`, `env:bwrap`, `flaky`, or `real:v1` failure remains on the host.

One caveat for reviewers: running this suite inside the Codex
`workspace-write` sandbox can spuriously skip the four
`tests/test_pi_sandbox.py` integration tests with
`Failed to create NETLINK_ROUTE socket: Operation not permitted`.
The same tests pass on the host (`bwrap --unshare-net` works), so the host run
above is authoritative; do not record the sandbox-induced skips as host defects.

`tests/test_accept_blind_fallback.py` imported the archived `probes.accept`
module. It is not a retained test; the exact file is preserved at
`attic/tests/test_accept_blind_fallback.py` and is not collected.

## Verification

All commands used `PYTHONDONTWRITEBYTECODE=1` and exited 0:

```bash
uv run ruff check .
uv run python -m molt doctor --deps
uv run python -m molt doctor --repo-policy
uv run python -m molt schema validate --all
uv run python -m molt scenario validate --scenario scenarios/demo/scenario.json
uv run python -m molt demo --mode replay --state /tmp/molt-v2-demo-state
uv run python -m molt audit --scenario demo --state /tmp/molt-v2-demo-state
```

The replay demo completed in 6.782s under the 180s limit. Audit reported
`runs=30 results=4 decisions=1 events=46 report_events=4`.

Build exclusion check:

```bash
uv build --wheel --out-dir /tmp/molt-m0-wheel   # 0 attic entries
uv build --sdist --out-dir /tmp/molt-m0-sdist   # 0 attic entries after sdist exclude
```

## Conclusion

M0 is ready for review. It is not merged, and M1 has not started.
