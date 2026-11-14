# molt.upper (B1)

Upper-layer assets, registry, deterministic retrieval, REPLAY evaluation, and the
append-only state graph. This layer is independent of `molt/core` gate decisions;
lower ids are references only (`lower_*` fields).

## Canonical JSON / hashes

All upper hashes use one rule (see `molt/upper/types.py::canonical_json`):

```text
canonical_json = json.dumps(obj, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False, allow_nan=False)
context_hash   = sha256(UTF-8(canonical_json(manifest without context_hash)))
node_id        = sha256(UTF-8(canonical_json(node payload without node_id)))
```

NaN/Inf are rejected by `allow_nan=False`. Empty values must be explicit `null`;
callers must not omit keys. Manifest lists are normalized (sorted) by
`build_context_manifest`, so logically identical manifests with different dict
key order produce the same hash.

Seed assets carry two separate hashes:

- `seed_identity_hash = sha256(canonical_json({source_path_hash, content_hash,
  extracted_summary, classification, importer_version}))`, used for import
  dedupe/same-id;
- `record_hash` = full semantic asset record hash (canonical JSON).

`context_hash` is reserved for graph context nodes/manifests
(`graph.compute_context_hash`). Older states that wrote `context_hash` on an
asset record are read as `record_hash`; new writes only emit `record_hash`.

Graph context nodes use the same canonical rule for their manifest
`context_hash`; node ids hash semantic fields only, excluding `created_at` and
`author`, so a repeated synthetic build has the same node id.

## Seed import / synthetic fixture

`molt/upper/seed.py` imports a fixed small selection read-only from
`feisama-base` (8 lessons, 4 skills, `opencode/AGENTS.md`, 2 agent rules). It
never executes a seed, copies home, or writes source files. Deny-name wins over
allowlist; sensitive matches skip the source and record only the category, never
the matched text. Assets record `origin="seed_import"`,
`eval_state="not_evaluated"`, `provenance="seed_import/reconstructed"`,
`verified_lineage=false`, `date_unknown` (frontmatter date only; mtime is not a
training time), and `source_path_hash` instead of absolute paths.

`molt/upper/fixture.py` builds a deterministic ops/dev synthetic graph over seed
assets: two independent tips, context nodes, and one not-evaluated candidate.
Synthetic/seed nodes are `not_evaluated`; no evaluation/decision record or core
verdict is produced. The C1b REPLAY demo is a separate graph and marks its real
nodes `origin="real_run"`, `eval_state="replayed"`.

## Graph (crash-atomic current file)

- `upper/nodes.jsonl` is append-only; nodes are immutable. There is **no merge
  API, no CRDT, and no delete API** for nodes or branches.
- `upper/current.json` is the single authoritative branch/active pointer file:
  `revision` (monotonic), `active_branch_id`, `context_node_id`,
  `context_hash`, `branches` (label/permission_scope/approval_level/tip),
  `updated_at`, `last_event_seq`. `branches.json` / `active.json` from older
  states are migrated or ignored on first open and never written again.
- Pointer/tip changes use an intent/commit protocol:
  1. append `intent` event with event_id, op, old/new node/active, target
     revision, payload hash, event_seq;
  2. write `current.json` with `revision+1` (temp + flush + fsync +
     `os.replace` + directory fsync);
  3. append `commit` event referencing the intent.
  On startup, intents without commit are reconciled: matching current
  revision/payload hash → append recovery commit; otherwise append abort and
  keep the old current. `upper/events.jsonl` records have `type` of
  `intent|commit|abort` and `op` of `fork|commit|switch|rollback` (plus the
  candidate/root/append audit events).
- `switch` moves a branch tip to any existing committed node. The latest D6
  refinement is implemented as **move-head only**: `rollback` moves the branch
  tip and active pointer to the target historical node and appends a rollback
  event; it does not create or delete nodes.
- `commit_candidate` appends a committed `decision` node (the candidate remains
  as evidence) and moves the branch tip/active pointer to the decision node.
  The commit is bound to an explicit `evaluation_id` + `approval`: the record in
  `<state>/upper/evaluations/<evaluation_id>.json` must exist and parse, have
  `status="passed"`, resolve to a `keep` decision, carry the same non-empty
  `approved_by` and an ISO8601 `approved_at`, and its `asset_id`/context binding
  must match the candidate. Any mismatch raises `ApprovalError` before
  `current.json`/events are written.
- `fcntl.flock(upper/lock)` serializes writers; readers take `LOCK_SH` around
  reloads so a UI never sees a half-applied commit (one retry on
  `GraphIntegrityError`). JSONL appends use `O_APPEND` + `fsync`.
- D6 approval gate: switch/rollback/commit validate every target asset ref
  against the registry: `status in {approved, active}`, non-empty
  `approved_by`/`approved_at`; MCP profiles additionally require
  `approval_level="strict"` (and a strict branch). `fork` does not validate or
  activate.

## Evaluation port

`molt/upper/eval_port.py` owns `EvaluationRequest` / `EvaluationResult` and
`evaluate(request)`. It prefers `molt.upper_eval.api.evaluate` (third macro),
otherwise launches the fixed CLI `python -m molt.upper_eval.evaluate` with a
request/out file in its own temp dir and a killable process group. If the
third macro is unavailable the result is `status="blocked"`,
`error.code="upper_eval_unavailable"`; no metrics are fabricated. The optional
B1 adapter is explicit (`adapter="local_replay"`, `provenance.adapter`), only
replays recorded solutions, and must not be presented as the third macro's
evaluator. Graph/demo layers persist references; evaluation metrics come from
the returned result.

## CLI

```bash
python -m molt.upper.graph_cli graph-show   --state /tmp/opencode/molt-upper-state
python -m molt.upper.graph_cli graph-log    --state /tmp/opencode/molt-upper-state
python -m molt.upper.graph_cli graph-branch --state /tmp/opencode/molt-upper-state --label ops
python -m molt.upper.graph_cli graph-switch --state /tmp/opencode/molt-upper-state \
  --branch-id br-... --target-node-id <sha256> --expected-tip <sha256>
```

Only `--state` accepts a filesystem path (validated outside the repository);
node/branch arguments are ids. All commands print JSON.

## Future capabilities

`context_budget`, `compaction`, and `vector_rag` are typed placeholders only
(`status="unimplemented"`). They are not executed. Later experiments require
real long-context tasks, `max_context > 200k`, window failures, or a dedicated
SoL-Pi reproduction. C1b currently has a few short rules and no window-pressure
or compaction-benefit data. Retrieval stays deterministic exact/prefix; no
embedding is introduced.
