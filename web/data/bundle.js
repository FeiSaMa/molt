// 由 `molt export` 生成，请勿手改；页面只读 window.MOLT_VIEW。
window.MOLT_VIEW = {
  "archive": {
    "archives": [],
    "lineage": [
      {
        "adopted": false,
        "adopted_at": null,
        "archive_id": null,
        "generation": null,
        "genome_id": "sha256:6520c6095297d8f26cd23c59f3cd8d322fea13869e5dd1151f53a9bddc438cfa",
        "parent_id": null
      },
      {
        "adopted": false,
        "adopted_at": null,
        "archive_id": null,
        "generation": null,
        "genome_id": "sha256:6be9015147fea1f3038553e287d519aa58ff9c80b847d2269d12a06b21defb38",
        "parent_id": "sha256:6520c6095297d8f26cd23c59f3cd8d322fea13869e5dd1151f53a9bddc438cfa"
      }
    ]
  },
  "audit": {
    "checked": {
      "decisions": 1,
      "events": 14,
      "report_events": 4,
      "results": 4,
      "runs": 6
    },
    "mismatches": [],
    "ok": true
  },
  "events": {
    "report": [
      {
        "event_id": "ev-demo-result-baseline-report",
        "ext": {},
        "payload": {
          "result_id": "result-baseline-report"
        },
        "run_id": null,
        "schema_version": "1.0.0",
        "scope": "report",
        "ts": "2026-01-01T00:00:00Z",
        "type": "report.scored"
      },
      {
        "event_id": "ev-demo-result-candidate-report",
        "ext": {},
        "payload": {
          "result_id": "result-candidate-report"
        },
        "run_id": null,
        "schema_version": "1.0.0",
        "scope": "report",
        "ts": "2026-01-01T00:00:00Z",
        "type": "report.scored"
      },
      {
        "event_id": "ev-2797f70b6f2a483c",
        "ext": {},
        "payload": {
          "count": 1
        },
        "run_id": null,
        "schema_version": "1.0.0",
        "scope": "report",
        "ts": "2026-11-10T15:09:58Z",
        "type": "holdout.accessed"
      },
      {
        "event_id": "ev-292d062cca10412a",
        "ext": {},
        "payload": {
          "count": 1
        },
        "run_id": null,
        "schema_version": "1.0.0",
        "scope": "report",
        "ts": "2026-11-10T15:09:58Z",
        "type": "holdout.accessed"
      }
    ],
    "search": [
      {
        "event_id": "ev-demo-gate",
        "ext": {},
        "payload": {
          "decision_id": "demo-decision-1",
          "verdict": "adopt"
        },
        "run_id": null,
        "schema_version": "1.0.0",
        "scope": "search",
        "ts": "2026-01-01T00:00:00Z",
        "type": "gate.decided"
      },
      {
        "event_id": "ev-demo-proposal",
        "ext": {},
        "payload": {
          "parent_genome_id": "sha256:6520c6095297d8f26cd23c59f3cd8d322fea13869e5dd1151f53a9bddc438cfa",
          "proposal_id": "prop-b53bf8aeb34074bc"
        },
        "run_id": null,
        "schema_version": "1.0.0",
        "scope": "search",
        "ts": "2026-01-01T00:00:00Z",
        "type": "proposal.generated"
      },
      {
        "event_id": "ev-093e5ec164aa4cb9",
        "ext": {},
        "payload": {
          "outcome": "passed",
          "task_id": "smoke-add"
        },
        "run_id": "6be90151-smoke-add-a1-097fa4",
        "schema_version": "1.0.0",
        "scope": "search",
        "ts": "2026-11-10T15:09:58Z",
        "type": "run.finished"
      },
      {
        "event_id": "ev-0df71dac6dcb4521",
        "ext": {},
        "payload": {
          "outcome": "passed",
          "task_id": "smoke-even"
        },
        "run_id": "6be90151-smoke-even-a1-182653",
        "schema_version": "1.0.0",
        "scope": "search",
        "ts": "2026-11-10T15:09:58Z",
        "type": "run.finished"
      },
      {
        "event_id": "ev-4639f720aea84ac9",
        "ext": {},
        "payload": {
          "outcome": "passed",
          "task_id": "smoke-add"
        },
        "run_id": "6520c609-smoke-add-a1-3cc404",
        "schema_version": "1.0.0",
        "scope": "search",
        "ts": "2026-11-10T15:09:58Z",
        "type": "run.finished"
      },
      {
        "event_id": "ev-a0b295b9a1a44dff",
        "ext": {},
        "payload": {
          "genome_id": "sha256:6520c6095297d8f26cd23c59f3cd8d322fea13869e5dd1151f53a9bddc438cfa",
          "task_id": "smoke-even"
        },
        "run_id": "6520c609-smoke-even-a1-a0bd9d",
        "schema_version": "1.0.0",
        "scope": "search",
        "ts": "2026-11-10T15:09:58Z",
        "type": "run.started"
      },
      {
        "event_id": "ev-ad784917e0ca4894",
        "ext": {},
        "payload": {
          "outcome": "passed",
          "task_id": "smoke-even"
        },
        "run_id": "6520c609-smoke-even-a1-a0bd9d",
        "schema_version": "1.0.0",
        "scope": "search",
        "ts": "2026-11-10T15:09:58Z",
        "type": "run.finished"
      },
      {
        "event_id": "ev-b7de72fd80a140ae",
        "ext": {},
        "payload": {
          "genome_id": "sha256:6520c6095297d8f26cd23c59f3cd8d322fea13869e5dd1151f53a9bddc438cfa",
          "task_id": "smoke-add"
        },
        "run_id": "6520c609-smoke-add-a1-3cc404",
        "schema_version": "1.0.0",
        "scope": "search",
        "ts": "2026-11-10T15:09:58Z",
        "type": "run.started"
      },
      {
        "event_id": "ev-c730febd4d5c4168",
        "ext": {},
        "payload": {
          "genome_id": "sha256:6be9015147fea1f3038553e287d519aa58ff9c80b847d2269d12a06b21defb38",
          "task_id": "smoke-even"
        },
        "run_id": "6be90151-smoke-even-a1-182653",
        "schema_version": "1.0.0",
        "scope": "search",
        "ts": "2026-11-10T15:09:58Z",
        "type": "run.started"
      },
      {
        "event_id": "ev-ea36544187e74ea2",
        "ext": {},
        "payload": {
          "genome_id": "sha256:6be9015147fea1f3038553e287d519aa58ff9c80b847d2269d12a06b21defb38",
          "task_id": "smoke-add"
        },
        "run_id": "6be90151-smoke-add-a1-097fa4",
        "schema_version": "1.0.0",
        "scope": "search",
        "ts": "2026-11-10T15:09:58Z",
        "type": "run.started"
      }
    ]
  },
  "gate": {
    "decision_count": 1,
    "decisions": [
      {
        "baseline_genome_id": "sha256:6520c6095297d8f26cd23c59f3cd8d322fea13869e5dd1151f53a9bddc438cfa",
        "budget_check": {
          "execution_exceeded": false,
          "overhead_exceeded": false,
          "reason": "ok"
        },
        "candidate_genome_id": "sha256:6be9015147fea1f3038553e287d519aa58ff9c80b847d2269d12a06b21defb38",
        "created_at": "2026-01-01T00:00:00Z",
        "decision_id": "demo-decision-1",
        "deltas": {
          "cost_per_solved_usd": -0.92,
          "cost_total_usd": -1.84,
          "solved": 0
        },
        "ext": {
          "inputs": {
            "baseline_result_id": "result-baseline-search",
            "candidate_result_id": "result-candidate-search"
          },
          "overhead_spend_usd": 0.0
        },
        "invalid_ratio": {
          "baseline": 0.0,
          "candidate": 0.0,
          "threshold": 0.25
        },
        "k": 0,
        "limits_identical": true,
        "pairs": [
          {
            "baseline_cost_usd": 1.84,
            "baseline_passed": true,
            "candidate_cost_usd": 0.92,
            "candidate_passed": true,
            "cost_delta_usd": -0.92,
            "passed_delta": 0,
            "task_id": "smoke-add"
          },
          {
            "baseline_cost_usd": 1.84,
            "baseline_passed": true,
            "candidate_cost_usd": 0.92,
            "candidate_passed": true,
            "cost_delta_usd": -0.92,
            "passed_delta": 0,
            "task_id": "smoke-even"
          }
        ],
        "reason": "lower_cost_per_solved",
        "reasons": [
          "lower_cost_per_solved"
        ],
        "scores": {
          "baseline": {
            "cost_per_solved_usd": 1.84,
            "genome_id": "sha256:6520c6095297d8f26cd23c59f3cd8d322fea13869e5dd1151f53a9bddc438cfa",
            "solved": 2,
            "total": 2
          },
          "candidate": {
            "cost_per_solved_usd": 0.92,
            "genome_id": "sha256:6be9015147fea1f3038553e287d519aa58ff9c80b847d2269d12a06b21defb38",
            "solved": 2,
            "total": 2
          }
        },
        "verdict": "adopt"
      }
    ],
    "policy_sha256": "b16a355566149f0a9fc7c72c751c93b10d71ebeef890ff13aa3aede5ae1e3d1b",
    "reasons": [
      "lower_cost_per_solved"
    ]
  },
  "proposals": {
    "proposals": [
      {
        "cost_usd": 0.0,
        "created_at": "2026-01-01T00:00:00Z",
        "ext": {
          "cost_source": "none",
          "counted_as": "search_overhead",
          "diagnosis": "grid step: 三维枚举",
          "fallback": {
            "reason": null,
            "used": false
          },
          "history": {
            "entries": 2,
            "excluded": 0,
            "scenario_version": "demo-2"
          },
          "model": "grid",
          "prompt_sha256": "ea70273804712345edb9458daf57e6b42426c79f94cbe957a7751543f4cf3d8b",
          "response_sha256": "70738f64503e7ca424588ebd94ba0b9e80a0808bd4a9f7445ebde2507189b580",
          "source": "grid",
          "usage": {
            "cache_read": 0,
            "cache_write": 0,
            "input": 0,
            "output": 0,
            "reasoning": 0,
            "total": 0
          }
        },
        "parent_id": "sha256:6520c6095297d8f26cd23c59f3cd8d322fea13869e5dd1151f53a9bddc438cfa",
        "proposal_id": "prop-b53bf8aeb34074bc",
        "proposer_id": "grid",
        "rationale": "grid step: 三维枚举",
        "scenario_version": "demo-2",
        "schema_version": "1.0.0",
        "switches": {
          "context": "full",
          "thinking": "low",
          "tools": "default"
        }
      }
    ]
  },
  "redaction": {
    "canonical_hash": "d180e888fcc800e06c9ec21296a1faa7565fb3737f5385b2ef83077245388b67",
    "metrics_hash": "0130d9b889168c3175447693c8f28123f6bd1513b15fe4717123e18e9e8511f6",
    "post_metrics": {
      "keys": [
        "$.decisions",
        "$.decisions[0].baseline_genome_id",
        "$.decisions[0].budget_check",
        "$.decisions[0].budget_check.execution_exceeded",
        "$.decisions[0].budget_check.overhead_exceeded",
        "$.decisions[0].budget_check.reason",
        "$.decisions[0].candidate_genome_id",
        "$.decisions[0].created_at",
        "$.decisions[0].decision_id",
        "$.decisions[0].ext",
        "$.decisions[0].ext.inputs",
        "$.decisions[0].ext.inputs.baseline_result_id",
        "$.decisions[0].ext.inputs.candidate_result_id",
        "$.decisions[0].ext.overhead_spend_usd",
        "$.decisions[0].invalid_ratio",
        "$.decisions[0].invalid_ratio.baseline",
        "$.decisions[0].invalid_ratio.candidate",
        "$.decisions[0].invalid_ratio.threshold",
        "$.decisions[0].k",
        "$.decisions[0].limits_identical",
        "$.decisions[0].paired",
        "$.decisions[0].paired[0].baseline_cost_usd",
        "$.decisions[0].paired[0].baseline_passed",
        "$.decisions[0].paired[0].candidate_cost_usd",
        "$.decisions[0].paired[0].candidate_passed",
        "$.decisions[0].paired[0].task_id",
        "$.decisions[0].paired[1].baseline_cost_usd",
        "$.decisions[0].paired[1].baseline_passed",
        "$.decisions[0].paired[1].candidate_cost_usd",
        "$.decisions[0].paired[1].candidate_passed",
        "$.decisions[0].paired[1].task_id",
        "$.decisions[0].reason",
        "$.decisions[0].scenario_version",
        "$.decisions[0].schema_version",
        "$.decisions[0].scores",
        "$.decisions[0].scores.baseline",
        "$.decisions[0].scores.baseline.cost_per_solved_usd",
        "$.decisions[0].scores.baseline.genome_id",
        "$.decisions[0].scores.baseline.solved",
        "$.decisions[0].scores.baseline.total",
        "$.decisions[0].scores.candidate",
        "$.decisions[0].scores.candidate.cost_per_solved_usd",
        "$.decisions[0].scores.candidate.genome_id",
        "$.decisions[0].scores.candidate.solved",
        "$.decisions[0].scores.candidate.total",
        "$.decisions[0].shell_version",
        "$.decisions[0].verdict",
        "$.decisions[0].verifier_hash",
        "$.results",
        "$.results[0].ext",
        "$.results[0].generated_at",
        "$.results[0].genome_id",
        "$.results[0].result_id",
        "$.results[0].run_refs",
        "$.results[0].scenario_version",
        "$.results[0].schema_version",
        "$.results[0].scope",
        "$.results[0].scores",
        "$.results[0].scores.holdout",
        "$.results[0].scores.holdout.cost_per_solved_usd",
        "$.results[0].scores.holdout.cost_total_usd",
        "$.results[0].scores.holdout.invalid_ratio",
        "$.results[0].scores.holdout.pass_at_1_first_attempt",
        "$.results[0].scores.holdout.solved",
        "$.results[0].scores.holdout.total",
        "$.results[0].scores.search",
        "$.results[0].scores.search.cost_per_solved_usd",
        "$.results[0].scores.search.cost_total_usd",
        "$.results[0].scores.search.invalid_ratio",
        "$.results[0].scores.search.pass_at_1_first_attempt",
        "$.results[0].scores.search.solved",
        "$.results[0].scores.search.total",
        "$.results[0].seed_control",
        "$.results[1].ext",
        "$.results[1].generated_at",
        "$.results[1].genome_id",
        "$.results[1].result_id",
        "$.results[1].run_refs",
        "$.results[1].scenario_version",
        "$.results[1].schema_version",
        "$.results[1].scope",
        "$.results[1].scores",
        "$.results[1].scores.search",
        "$.results[1].scores.search.cost_per_solved_usd",
        "$.results[1].scores.search.cost_total_usd",
        "$.results[1].scores.search.invalid_ratio",
        "$.results[1].scores.search.pass_at_1_first_attempt",
        "$.results[1].scores.search.solved",
        "$.results[1].scores.search.total",
        "$.results[1].seed_control",
        "$.results[2].ext",
        "$.results[2].generated_at",
        "$.results[2].genome_id",
        "$.results[2].result_id",
        "$.results[2].run_refs",
        "$.results[2].scenario_version",
        "$.results[2].schema_version",
        "$.results[2].scope",
        "$.results[2].scores",
        "$.results[2].scores.holdout",
        "$.results[2].scores.holdout.cost_per_solved_usd",
        "$.results[2].scores.holdout.cost_total_usd",
        "$.results[2].scores.holdout.invalid_ratio",
        "$.results[2].scores.holdout.pass_at_1_first_attempt",
        "$.results[2].scores.holdout.solved",
        "$.results[2].scores.holdout.total",
        "$.results[2].scores.search",
        "$.results[2].scores.search.cost_per_solved_usd",
        "$.results[2].scores.search.cost_total_usd",
        "$.results[2].scores.search.invalid_ratio",
        "$.results[2].scores.search.pass_at_1_first_attempt",
        "$.results[2].scores.search.solved",
        "$.results[2].scores.search.total",
        "$.results[2].seed_control",
        "$.results[3].ext",
        "$.results[3].generated_at",
        "$.results[3].genome_id",
        "$.results[3].result_id",
        "$.results[3].run_refs",
        "$.results[3].scenario_version",
        "$.results[3].schema_version",
        "$.results[3].scope",
        "$.results[3].scores",
        "$.results[3].scores.search",
        "$.results[3].scores.search.cost_per_solved_usd",
        "$.results[3].scores.search.cost_total_usd",
        "$.results[3].scores.search.invalid_ratio",
        "$.results[3].scores.search.pass_at_1_first_attempt",
        "$.results[3].scores.search.solved",
        "$.results[3].scores.search.total",
        "$.results[3].seed_control",
        "$.runs",
        "$.runs[0].artifacts",
        "$.runs[0].artifacts.solution.py",
        "$.runs[0].attempt",
        "$.runs[0].ext",
        "$.runs[0].finished_at",
        "$.runs[0].genome_id",
        "$.runs[0].outcome",
        "$.runs[0].run_id",
        "$.runs[0].scenario_version",
        "$.runs[0].schema_version",
        "$.runs[0].scope",
        "$.runs[0].seed_control",
        "$.runs[0].shell_version",
        "$.runs[0].started_at",
        "$.runs[0].task_id",
        "$.runs[0].usage",
        "$.runs[0].usage.cost_usd",
        "$.runs[0].usage.tokens_in",
        "$.runs[0].usage.tokens_out",
        "$.runs[0].usage.turns",
        "$.runs[0].usage.wall_ms",
        "$.runs[0].verification",
        "$.runs[0].verification.matches",
        "$.runs[0].verification.tests_hash_after",
        "$.runs[0].verification.tests_hash_before",
        "$.runs[0].verifier_hash",
        "$.runs[1].artifacts",
        "$.runs[1].artifacts.solution.py",
        "$.runs[1].attempt",
        "$.runs[1].ext",
        "$.runs[1].finished_at",
        "$.runs[1].genome_id",
        "$.runs[1].outcome",
        "$.runs[1].run_id",
        "$.runs[1].scenario_version",
        "$.runs[1].schema_version",
        "$.runs[1].scope",
        "$.runs[1].seed_control",
        "$.runs[1].shell_version",
        "$.runs[1].started_at",
        "$.runs[1].task_id",
        "$.runs[1].usage",
        "$.runs[1].usage.cost_usd",
        "$.runs[1].usage.tokens_in",
        "$.runs[1].usage.tokens_out",
        "$.runs[1].usage.turns",
        "$.runs[1].usage.wall_ms",
        "$.runs[1].verification",
        "$.runs[1].verification.matches",
        "$.runs[1].verification.tests_hash_after",
        "$.runs[1].verification.tests_hash_before",
        "$.runs[1].verifier_hash",
        "$.runs[2].artifacts",
        "$.runs[2].artifacts.solution.py",
        "$.runs[2].attempt",
        "$.runs[2].ext",
        "$.runs[2].finished_at",
        "$.runs[2].genome_id",
        "$.runs[2].outcome",
        "$.runs[2].run_id",
        "$.runs[2].scenario_version",
        "$.runs[2].schema_version",
        "$.runs[2].scope",
        "$.runs[2].seed_control",
        "$.runs[2].shell_version",
        "$.runs[2].started_at",
        "$.runs[2].task_id",
        "$.runs[2].usage",
        "$.runs[2].usage.cost_usd",
        "$.runs[2].usage.tokens_in",
        "$.runs[2].usage.tokens_out",
        "$.runs[2].usage.turns",
        "$.runs[2].usage.wall_ms",
        "$.runs[2].verification",
        "$.runs[2].verification.matches",
        "$.runs[2].verification.tests_hash_after",
        "$.runs[2].verification.tests_hash_before",
        "$.runs[2].verifier_hash",
        "$.runs[3].artifacts",
        "$.runs[3].artifacts.solution.py",
        "$.runs[3].attempt",
        "$.runs[3].ext",
        "$.runs[3].finished_at",
        "$.runs[3].genome_id",
        "$.runs[3].outcome",
        "$.runs[3].run_id",
        "$.runs[3].scenario_version",
        "$.runs[3].schema_version",
        "$.runs[3].scope",
        "$.runs[3].seed_control",
        "$.runs[3].shell_version",
        "$.runs[3].started_at",
        "$.runs[3].task_id",
        "$.runs[3].usage",
        "$.runs[3].usage.cost_usd",
        "$.runs[3].usage.tokens_in",
        "$.runs[3].usage.tokens_out",
        "$.runs[3].usage.turns",
        "$.runs[3].usage.wall_ms",
        "$.runs[3].verification",
        "$.runs[3].verification.matches",
        "$.runs[3].verification.tests_hash_after",
        "$.runs[3].verification.tests_hash_before",
        "$.runs[3].verifier_hash",
        "$.runs[4].artifacts",
        "$.runs[4].artifacts.solution.py",
        "$.runs[4].attempt",
        "$.runs[4].ext",
        "$.runs[4].finished_at",
        "$.runs[4].genome_id",
        "$.runs[4].outcome",
        "$.runs[4].run_id",
        "$.runs[4].scenario_version",
        "$.runs[4].schema_version",
        "$.runs[4].scope",
        "$.runs[4].seed_control",
        "$.runs[4].shell_version",
        "$.runs[4].started_at",
        "$.runs[4].task_id",
        "$.runs[4].usage",
        "$.runs[4].usage.cost_usd",
        "$.runs[4].usage.tokens_in",
        "$.runs[4].usage.tokens_out",
        "$.runs[4].usage.turns",
        "$.runs[4].usage.wall_ms",
        "$.runs[4].verification",
        "$.runs[4].verification.matches",
        "$.runs[4].verification.tests_hash_after",
        "$.runs[4].verification.tests_hash_before",
        "$.runs[4].verifier_hash",
        "$.runs[5].artifacts",
        "$.runs[5].artifacts.solution.py",
        "$.runs[5].attempt",
        "$.runs[5].ext",
        "$.runs[5].finished_at",
        "$.runs[5].genome_id",
        "$.runs[5].outcome",
        "$.runs[5].run_id",
        "$.runs[5].scenario_version",
        "$.runs[5].schema_version",
        "$.runs[5].scope",
        "$.runs[5].seed_control",
        "$.runs[5].shell_version",
        "$.runs[5].started_at",
        "$.runs[5].task_id",
        "$.runs[5].usage",
        "$.runs[5].usage.cost_usd",
        "$.runs[5].usage.tokens_in",
        "$.runs[5].usage.tokens_out",
        "$.runs[5].usage.turns",
        "$.runs[5].usage.wall_ms",
        "$.runs[5].verification",
        "$.runs[5].verification.matches",
        "$.runs[5].verification.tests_hash_after",
        "$.runs[5].verification.tests_hash_before",
        "$.runs[5].verifier_hash",
        "$.scenario_id",
        "$.scenario_version"
      ],
      "numbers": [
        [
          "$.decisions[0].ext.overhead_spend_usd",
          0.0
        ],
        [
          "$.decisions[0].invalid_ratio.baseline",
          0.0
        ],
        [
          "$.decisions[0].invalid_ratio.candidate",
          0.0
        ],
        [
          "$.decisions[0].invalid_ratio.threshold",
          0.25
        ],
        [
          "$.decisions[0].k",
          0.0
        ],
        [
          "$.decisions[0].paired[0].baseline_cost_usd",
          1.84
        ],
        [
          "$.decisions[0].paired[0].candidate_cost_usd",
          0.92
        ],
        [
          "$.decisions[0].paired[1].baseline_cost_usd",
          1.84
        ],
        [
          "$.decisions[0].paired[1].candidate_cost_usd",
          0.92
        ],
        [
          "$.decisions[0].scores.baseline.cost_per_solved_usd",
          1.84
        ],
        [
          "$.decisions[0].scores.baseline.solved",
          2.0
        ],
        [
          "$.decisions[0].scores.baseline.total",
          2.0
        ],
        [
          "$.decisions[0].scores.candidate.cost_per_solved_usd",
          0.92
        ],
        [
          "$.decisions[0].scores.candidate.solved",
          2.0
        ],
        [
          "$.decisions[0].scores.candidate.total",
          2.0
        ],
        [
          "$.results[0].scores.holdout.cost_per_solved_usd",
          1.84
        ],
        [
          "$.results[0].scores.holdout.cost_total_usd",
          1.84
        ],
        [
          "$.results[0].scores.holdout.invalid_ratio",
          0.0
        ],
        [
          "$.results[0].scores.holdout.pass_at_1_first_attempt",
          1.0
        ],
        [
          "$.results[0].scores.holdout.solved",
          1.0
        ],
        [
          "$.results[0].scores.holdout.total",
          1.0
        ],
        [
          "$.results[0].scores.search.cost_per_solved_usd",
          1.84
        ],
        [
          "$.results[0].scores.search.cost_total_usd",
          3.68
        ],
        [
          "$.results[0].scores.search.invalid_ratio",
          0.0
        ],
        [
          "$.results[0].scores.search.pass_at_1_first_attempt",
          1.0
        ],
        [
          "$.results[0].scores.search.solved",
          2.0
        ],
        [
          "$.results[0].scores.search.total",
          2.0
        ],
        [
          "$.results[1].scores.search.cost_per_solved_usd",
          1.84
        ],
        [
          "$.results[1].scores.search.cost_total_usd",
          3.68
        ],
        [
          "$.results[1].scores.search.invalid_ratio",
          0.0
        ],
        [
          "$.results[1].scores.search.pass_at_1_first_attempt",
          1.0
        ],
        [
          "$.results[1].scores.search.solved",
          2.0
        ],
        [
          "$.results[1].scores.search.total",
          2.0
        ],
        [
          "$.results[2].scores.holdout.cost_per_solved_usd",
          0.92
        ],
        [
          "$.results[2].scores.holdout.cost_total_usd",
          0.92
        ],
        [
          "$.results[2].scores.holdout.invalid_ratio",
          0.0
        ],
        [
          "$.results[2].scores.holdout.pass_at_1_first_attempt",
          1.0
        ],
        [
          "$.results[2].scores.holdout.solved",
          1.0
        ],
        [
          "$.results[2].scores.holdout.total",
          1.0
        ],
        [
          "$.results[2].scores.search.cost_per_solved_usd",
          0.92
        ],
        [
          "$.results[2].scores.search.cost_total_usd",
          1.84
        ],
        [
          "$.results[2].scores.search.invalid_ratio",
          0.0
        ],
        [
          "$.results[2].scores.search.pass_at_1_first_attempt",
          1.0
        ],
        [
          "$.results[2].scores.search.solved",
          2.0
        ],
        [
          "$.results[2].scores.search.total",
          2.0
        ],
        [
          "$.results[3].scores.search.cost_per_solved_usd",
          0.92
        ],
        [
          "$.results[3].scores.search.cost_total_usd",
          1.84
        ],
        [
          "$.results[3].scores.search.invalid_ratio",
          0.0
        ],
        [
          "$.results[3].scores.search.pass_at_1_first_attempt",
          1.0
        ],
        [
          "$.results[3].scores.search.solved",
          2.0
        ],
        [
          "$.results[3].scores.search.total",
          2.0
        ],
        [
          "$.runs[0].attempt",
          1.0
        ],
        [
          "$.runs[0].usage.cost_usd",
          1.84
        ],
        [
          "$.runs[0].usage.tokens_in",
          1150.0
        ],
        [
          "$.runs[0].usage.tokens_out",
          800.0
        ],
        [
          "$.runs[0].usage.turns",
          4.0
        ],
        [
          "$.runs[0].usage.wall_ms",
          1840.0
        ],
        [
          "$.runs[1].attempt",
          1.0
        ],
        [
          "$.runs[1].usage.cost_usd",
          1.84
        ],
        [
          "$.runs[1].usage.tokens_in",
          1150.0
        ],
        [
          "$.runs[1].usage.tokens_out",
          800.0
        ],
        [
          "$.runs[1].usage.turns",
          4.0
        ],
        [
          "$.runs[1].usage.wall_ms",
          1840.0
        ],
        [
          "$.runs[2].attempt",
          1.0
        ],
        [
          "$.runs[2].usage.cost_usd",
          1.84
        ],
        [
          "$.runs[2].usage.tokens_in",
          1150.0
        ],
        [
          "$.runs[2].usage.tokens_out",
          800.0
        ],
        [
          "$.runs[2].usage.turns",
          4.0
        ],
        [
          "$.runs[2].usage.wall_ms",
          1840.0
        ],
        [
          "$.runs[3].attempt",
          1.0
        ],
        [
          "$.runs[3].usage.cost_usd",
          0.92
        ],
        [
          "$.runs[3].usage.tokens_in",
          1150.0
        ],
        [
          "$.runs[3].usage.tokens_out",
          400.0
        ],
        [
          "$.runs[3].usage.turns",
          2.0
        ],
        [
          "$.runs[3].usage.wall_ms",
          920.0
        ],
        [
          "$.runs[4].attempt",
          1.0
        ],
        [
          "$.runs[4].usage.cost_usd",
          0.92
        ],
        [
          "$.runs[4].usage.tokens_in",
          1150.0
        ],
        [
          "$.runs[4].usage.tokens_out",
          400.0
        ],
        [
          "$.runs[4].usage.turns",
          2.0
        ],
        [
          "$.runs[4].usage.wall_ms",
          920.0
        ],
        [
          "$.runs[5].attempt",
          1.0
        ],
        [
          "$.runs[5].usage.cost_usd",
          0.92
        ],
        [
          "$.runs[5].usage.tokens_in",
          1150.0
        ],
        [
          "$.runs[5].usage.tokens_out",
          400.0
        ],
        [
          "$.runs[5].usage.turns",
          2.0
        ],
        [
          "$.runs[5].usage.wall_ms",
          920.0
        ]
      ],
      "shape": [
        [
          "$",
          "object"
        ],
        [
          "$.decisions",
          "array[1]"
        ],
        [
          "$.decisions[0]",
          "object"
        ],
        [
          "$.decisions[0].baseline_genome_id",
          "string"
        ],
        [
          "$.decisions[0].budget_check",
          "object"
        ],
        [
          "$.decisions[0].budget_check.execution_exceeded",
          "bool:False"
        ],
        [
          "$.decisions[0].budget_check.overhead_exceeded",
          "bool:False"
        ],
        [
          "$.decisions[0].budget_check.reason",
          "string"
        ],
        [
          "$.decisions[0].candidate_genome_id",
          "string"
        ],
        [
          "$.decisions[0].created_at",
          "string"
        ],
        [
          "$.decisions[0].decision_id",
          "string"
        ],
        [
          "$.decisions[0].ext",
          "object"
        ],
        [
          "$.decisions[0].ext.inputs",
          "object"
        ],
        [
          "$.decisions[0].ext.inputs.baseline_result_id",
          "string"
        ],
        [
          "$.decisions[0].ext.inputs.candidate_result_id",
          "string"
        ],
        [
          "$.decisions[0].ext.overhead_spend_usd",
          "number"
        ],
        [
          "$.decisions[0].invalid_ratio",
          "object"
        ],
        [
          "$.decisions[0].invalid_ratio.baseline",
          "number"
        ],
        [
          "$.decisions[0].invalid_ratio.candidate",
          "number"
        ],
        [
          "$.decisions[0].invalid_ratio.threshold",
          "number"
        ],
        [
          "$.decisions[0].k",
          "number"
        ],
        [
          "$.decisions[0].limits_identical",
          "bool:True"
        ],
        [
          "$.decisions[0].paired",
          "array[2]"
        ],
        [
          "$.decisions[0].paired[0]",
          "object"
        ],
        [
          "$.decisions[0].paired[0].baseline_cost_usd",
          "number"
        ],
        [
          "$.decisions[0].paired[0].baseline_passed",
          "bool:True"
        ],
        [
          "$.decisions[0].paired[0].candidate_cost_usd",
          "number"
        ],
        [
          "$.decisions[0].paired[0].candidate_passed",
          "bool:True"
        ],
        [
          "$.decisions[0].paired[0].task_id",
          "string"
        ],
        [
          "$.decisions[0].paired[1]",
          "object"
        ],
        [
          "$.decisions[0].paired[1].baseline_cost_usd",
          "number"
        ],
        [
          "$.decisions[0].paired[1].baseline_passed",
          "bool:True"
        ],
        [
          "$.decisions[0].paired[1].candidate_cost_usd",
          "number"
        ],
        [
          "$.decisions[0].paired[1].candidate_passed",
          "bool:True"
        ],
        [
          "$.decisions[0].paired[1].task_id",
          "string"
        ],
        [
          "$.decisions[0].reason",
          "string"
        ],
        [
          "$.decisions[0].scenario_version",
          "string"
        ],
        [
          "$.decisions[0].schema_version",
          "string"
        ],
        [
          "$.decisions[0].scores",
          "object"
        ],
        [
          "$.decisions[0].scores.baseline",
          "object"
        ],
        [
          "$.decisions[0].scores.baseline.cost_per_solved_usd",
          "number"
        ],
        [
          "$.decisions[0].scores.baseline.genome_id",
          "string"
        ],
        [
          "$.decisions[0].scores.baseline.solved",
          "number"
        ],
        [
          "$.decisions[0].scores.baseline.total",
          "number"
        ],
        [
          "$.decisions[0].scores.candidate",
          "object"
        ],
        [
          "$.decisions[0].scores.candidate.cost_per_solved_usd",
          "number"
        ],
        [
          "$.decisions[0].scores.candidate.genome_id",
          "string"
        ],
        [
          "$.decisions[0].scores.candidate.solved",
          "number"
        ],
        [
          "$.decisions[0].scores.candidate.total",
          "number"
        ],
        [
          "$.decisions[0].shell_version",
          "string"
        ],
        [
          "$.decisions[0].verdict",
          "string"
        ],
        [
          "$.decisions[0].verifier_hash",
          "string"
        ],
        [
          "$.results",
          "array[4]"
        ],
        [
          "$.results[0]",
          "object"
        ],
        [
          "$.results[0].ext",
          "object"
        ],
        [
          "$.results[0].generated_at",
          "string"
        ],
        [
          "$.results[0].genome_id",
          "string"
        ],
        [
          "$.results[0].result_id",
          "string"
        ],
        [
          "$.results[0].run_refs",
          "array[3]"
        ],
        [
          "$.results[0].run_refs[0]",
          "string"
        ],
        [
          "$.results[0].run_refs[1]",
          "string"
        ],
        [
          "$.results[0].run_refs[2]",
          "string"
        ],
        [
          "$.results[0].scenario_version",
          "string"
        ],
        [
          "$.results[0].schema_version",
          "string"
        ],
        [
          "$.results[0].scope",
          "string"
        ],
        [
          "$.results[0].scores",
          "object"
        ],
        [
          "$.results[0].scores.holdout",
          "object"
        ],
        [
          "$.results[0].scores.holdout.cost_per_solved_usd",
          "number"
        ],
        [
          "$.results[0].scores.holdout.cost_total_usd",
          "number"
        ],
        [
          "$.results[0].scores.holdout.invalid_ratio",
          "number"
        ],
        [
          "$.results[0].scores.holdout.pass_at_1_first_attempt",
          "number"
        ],
        [
          "$.results[0].scores.holdout.solved",
          "number"
        ],
        [
          "$.results[0].scores.holdout.total",
          "number"
        ],
        [
          "$.results[0].scores.search",
          "object"
        ],
        [
          "$.results[0].scores.search.cost_per_solved_usd",
          "number"
        ],
        [
          "$.results[0].scores.search.cost_total_usd",
          "number"
        ],
        [
          "$.results[0].scores.search.invalid_ratio",
          "number"
        ],
        [
          "$.results[0].scores.search.pass_at_1_first_attempt",
          "number"
        ],
        [
          "$.results[0].scores.search.solved",
          "number"
        ],
        [
          "$.results[0].scores.search.total",
          "number"
        ],
        [
          "$.results[0].seed_control",
          "string"
        ],
        [
          "$.results[1]",
          "object"
        ],
        [
          "$.results[1].ext",
          "object"
        ],
        [
          "$.results[1].generated_at",
          "string"
        ],
        [
          "$.results[1].genome_id",
          "string"
        ],
        [
          "$.results[1].result_id",
          "string"
        ],
        [
          "$.results[1].run_refs",
          "array[2]"
        ],
        [
          "$.results[1].run_refs[0]",
          "string"
        ],
        [
          "$.results[1].run_refs[1]",
          "string"
        ],
        [
          "$.results[1].scenario_version",
          "string"
        ],
        [
          "$.results[1].schema_version",
          "string"
        ],
        [
          "$.results[1].scope",
          "string"
        ],
        [
          "$.results[1].scores",
          "object"
        ],
        [
          "$.results[1].scores.search",
          "object"
        ],
        [
          "$.results[1].scores.search.cost_per_solved_usd",
          "number"
        ],
        [
          "$.results[1].scores.search.cost_total_usd",
          "number"
        ],
        [
          "$.results[1].scores.search.invalid_ratio",
          "number"
        ],
        [
          "$.results[1].scores.search.pass_at_1_first_attempt",
          "number"
        ],
        [
          "$.results[1].scores.search.solved",
          "number"
        ],
        [
          "$.results[1].scores.search.total",
          "number"
        ],
        [
          "$.results[1].seed_control",
          "string"
        ],
        [
          "$.results[2]",
          "object"
        ],
        [
          "$.results[2].ext",
          "object"
        ],
        [
          "$.results[2].generated_at",
          "string"
        ],
        [
          "$.results[2].genome_id",
          "string"
        ],
        [
          "$.results[2].result_id",
          "string"
        ],
        [
          "$.results[2].run_refs",
          "array[3]"
        ],
        [
          "$.results[2].run_refs[0]",
          "string"
        ],
        [
          "$.results[2].run_refs[1]",
          "string"
        ],
        [
          "$.results[2].run_refs[2]",
          "string"
        ],
        [
          "$.results[2].scenario_version",
          "string"
        ],
        [
          "$.results[2].schema_version",
          "string"
        ],
        [
          "$.results[2].scope",
          "string"
        ],
        [
          "$.results[2].scores",
          "object"
        ],
        [
          "$.results[2].scores.holdout",
          "object"
        ],
        [
          "$.results[2].scores.holdout.cost_per_solved_usd",
          "number"
        ],
        [
          "$.results[2].scores.holdout.cost_total_usd",
          "number"
        ],
        [
          "$.results[2].scores.holdout.invalid_ratio",
          "number"
        ],
        [
          "$.results[2].scores.holdout.pass_at_1_first_attempt",
          "number"
        ],
        [
          "$.results[2].scores.holdout.solved",
          "number"
        ],
        [
          "$.results[2].scores.holdout.total",
          "number"
        ],
        [
          "$.results[2].scores.search",
          "object"
        ],
        [
          "$.results[2].scores.search.cost_per_solved_usd",
          "number"
        ],
        [
          "$.results[2].scores.search.cost_total_usd",
          "number"
        ],
        [
          "$.results[2].scores.search.invalid_ratio",
          "number"
        ],
        [
          "$.results[2].scores.search.pass_at_1_first_attempt",
          "number"
        ],
        [
          "$.results[2].scores.search.solved",
          "number"
        ],
        [
          "$.results[2].scores.search.total",
          "number"
        ],
        [
          "$.results[2].seed_control",
          "string"
        ],
        [
          "$.results[3]",
          "object"
        ],
        [
          "$.results[3].ext",
          "object"
        ],
        [
          "$.results[3].generated_at",
          "string"
        ],
        [
          "$.results[3].genome_id",
          "string"
        ],
        [
          "$.results[3].result_id",
          "string"
        ],
        [
          "$.results[3].run_refs",
          "array[2]"
        ],
        [
          "$.results[3].run_refs[0]",
          "string"
        ],
        [
          "$.results[3].run_refs[1]",
          "string"
        ],
        [
          "$.results[3].scenario_version",
          "string"
        ],
        [
          "$.results[3].schema_version",
          "string"
        ],
        [
          "$.results[3].scope",
          "string"
        ],
        [
          "$.results[3].scores",
          "object"
        ],
        [
          "$.results[3].scores.search",
          "object"
        ],
        [
          "$.results[3].scores.search.cost_per_solved_usd",
          "number"
        ],
        [
          "$.results[3].scores.search.cost_total_usd",
          "number"
        ],
        [
          "$.results[3].scores.search.invalid_ratio",
          "number"
        ],
        [
          "$.results[3].scores.search.pass_at_1_first_attempt",
          "number"
        ],
        [
          "$.results[3].scores.search.solved",
          "number"
        ],
        [
          "$.results[3].scores.search.total",
          "number"
        ],
        [
          "$.results[3].seed_control",
          "string"
        ],
        [
          "$.runs",
          "array[6]"
        ],
        [
          "$.runs[0]",
          "object"
        ],
        [
          "$.runs[0].artifacts",
          "object"
        ],
        [
          "$.runs[0].artifacts.solution.py",
          "string"
        ],
        [
          "$.runs[0].attempt",
          "number"
        ],
        [
          "$.runs[0].ext",
          "object"
        ],
        [
          "$.runs[0].finished_at",
          "string"
        ],
        [
          "$.runs[0].genome_id",
          "string"
        ],
        [
          "$.runs[0].outcome",
          "string"
        ],
        [
          "$.runs[0].run_id",
          "string"
        ],
        [
          "$.runs[0].scenario_version",
          "string"
        ],
        [
          "$.runs[0].schema_version",
          "string"
        ],
        [
          "$.runs[0].scope",
          "string"
        ],
        [
          "$.runs[0].seed_control",
          "string"
        ],
        [
          "$.runs[0].shell_version",
          "string"
        ],
        [
          "$.runs[0].started_at",
          "string"
        ],
        [
          "$.runs[0].task_id",
          "string"
        ],
        [
          "$.runs[0].usage",
          "object"
        ],
        [
          "$.runs[0].usage.cost_usd",
          "number"
        ],
        [
          "$.runs[0].usage.tokens_in",
          "number"
        ],
        [
          "$.runs[0].usage.tokens_out",
          "number"
        ],
        [
          "$.runs[0].usage.turns",
          "number"
        ],
        [
          "$.runs[0].usage.wall_ms",
          "number"
        ],
        [
          "$.runs[0].verification",
          "object"
        ],
        [
          "$.runs[0].verification.matches",
          "bool:True"
        ],
        [
          "$.runs[0].verification.tests_hash_after",
          "string"
        ],
        [
          "$.runs[0].verification.tests_hash_before",
          "string"
        ],
        [
          "$.runs[0].verifier_hash",
          "string"
        ],
        [
          "$.runs[1]",
          "object"
        ],
        [
          "$.runs[1].artifacts",
          "object"
        ],
        [
          "$.runs[1].artifacts.solution.py",
          "string"
        ],
        [
          "$.runs[1].attempt",
          "number"
        ],
        [
          "$.runs[1].ext",
          "object"
        ],
        [
          "$.runs[1].finished_at",
          "string"
        ],
        [
          "$.runs[1].genome_id",
          "string"
        ],
        [
          "$.runs[1].outcome",
          "string"
        ],
        [
          "$.runs[1].run_id",
          "string"
        ],
        [
          "$.runs[1].scenario_version",
          "string"
        ],
        [
          "$.runs[1].schema_version",
          "string"
        ],
        [
          "$.runs[1].scope",
          "string"
        ],
        [
          "$.runs[1].seed_control",
          "string"
        ],
        [
          "$.runs[1].shell_version",
          "string"
        ],
        [
          "$.runs[1].started_at",
          "string"
        ],
        [
          "$.runs[1].task_id",
          "string"
        ],
        [
          "$.runs[1].usage",
          "object"
        ],
        [
          "$.runs[1].usage.cost_usd",
          "number"
        ],
        [
          "$.runs[1].usage.tokens_in",
          "number"
        ],
        [
          "$.runs[1].usage.tokens_out",
          "number"
        ],
        [
          "$.runs[1].usage.turns",
          "number"
        ],
        [
          "$.runs[1].usage.wall_ms",
          "number"
        ],
        [
          "$.runs[1].verification",
          "object"
        ],
        [
          "$.runs[1].verification.matches",
          "bool:True"
        ],
        [
          "$.runs[1].verification.tests_hash_after",
          "string"
        ],
        [
          "$.runs[1].verification.tests_hash_before",
          "string"
        ],
        [
          "$.runs[1].verifier_hash",
          "string"
        ],
        [
          "$.runs[2]",
          "object"
        ],
        [
          "$.runs[2].artifacts",
          "object"
        ],
        [
          "$.runs[2].artifacts.solution.py",
          "string"
        ],
        [
          "$.runs[2].attempt",
          "number"
        ],
        [
          "$.runs[2].ext",
          "object"
        ],
        [
          "$.runs[2].finished_at",
          "string"
        ],
        [
          "$.runs[2].genome_id",
          "string"
        ],
        [
          "$.runs[2].outcome",
          "string"
        ],
        [
          "$.runs[2].run_id",
          "string"
        ],
        [
          "$.runs[2].scenario_version",
          "string"
        ],
        [
          "$.runs[2].schema_version",
          "string"
        ],
        [
          "$.runs[2].scope",
          "string"
        ],
        [
          "$.runs[2].seed_control",
          "string"
        ],
        [
          "$.runs[2].shell_version",
          "string"
        ],
        [
          "$.runs[2].started_at",
          "string"
        ],
        [
          "$.runs[2].task_id",
          "string"
        ],
        [
          "$.runs[2].usage",
          "object"
        ],
        [
          "$.runs[2].usage.cost_usd",
          "number"
        ],
        [
          "$.runs[2].usage.tokens_in",
          "number"
        ],
        [
          "$.runs[2].usage.tokens_out",
          "number"
        ],
        [
          "$.runs[2].usage.turns",
          "number"
        ],
        [
          "$.runs[2].usage.wall_ms",
          "number"
        ],
        [
          "$.runs[2].verification",
          "object"
        ],
        [
          "$.runs[2].verification.matches",
          "bool:True"
        ],
        [
          "$.runs[2].verification.tests_hash_after",
          "string"
        ],
        [
          "$.runs[2].verification.tests_hash_before",
          "string"
        ],
        [
          "$.runs[2].verifier_hash",
          "string"
        ],
        [
          "$.runs[3]",
          "object"
        ],
        [
          "$.runs[3].artifacts",
          "object"
        ],
        [
          "$.runs[3].artifacts.solution.py",
          "string"
        ],
        [
          "$.runs[3].attempt",
          "number"
        ],
        [
          "$.runs[3].ext",
          "object"
        ],
        [
          "$.runs[3].finished_at",
          "string"
        ],
        [
          "$.runs[3].genome_id",
          "string"
        ],
        [
          "$.runs[3].outcome",
          "string"
        ],
        [
          "$.runs[3].run_id",
          "string"
        ],
        [
          "$.runs[3].scenario_version",
          "string"
        ],
        [
          "$.runs[3].schema_version",
          "string"
        ],
        [
          "$.runs[3].scope",
          "string"
        ],
        [
          "$.runs[3].seed_control",
          "string"
        ],
        [
          "$.runs[3].shell_version",
          "string"
        ],
        [
          "$.runs[3].started_at",
          "string"
        ],
        [
          "$.runs[3].task_id",
          "string"
        ],
        [
          "$.runs[3].usage",
          "object"
        ],
        [
          "$.runs[3].usage.cost_usd",
          "number"
        ],
        [
          "$.runs[3].usage.tokens_in",
          "number"
        ],
        [
          "$.runs[3].usage.tokens_out",
          "number"
        ],
        [
          "$.runs[3].usage.turns",
          "number"
        ],
        [
          "$.runs[3].usage.wall_ms",
          "number"
        ],
        [
          "$.runs[3].verification",
          "object"
        ],
        [
          "$.runs[3].verification.matches",
          "bool:True"
        ],
        [
          "$.runs[3].verification.tests_hash_after",
          "string"
        ],
        [
          "$.runs[3].verification.tests_hash_before",
          "string"
        ],
        [
          "$.runs[3].verifier_hash",
          "string"
        ],
        [
          "$.runs[4]",
          "object"
        ],
        [
          "$.runs[4].artifacts",
          "object"
        ],
        [
          "$.runs[4].artifacts.solution.py",
          "string"
        ],
        [
          "$.runs[4].attempt",
          "number"
        ],
        [
          "$.runs[4].ext",
          "object"
        ],
        [
          "$.runs[4].finished_at",
          "string"
        ],
        [
          "$.runs[4].genome_id",
          "string"
        ],
        [
          "$.runs[4].outcome",
          "string"
        ],
        [
          "$.runs[4].run_id",
          "string"
        ],
        [
          "$.runs[4].scenario_version",
          "string"
        ],
        [
          "$.runs[4].schema_version",
          "string"
        ],
        [
          "$.runs[4].scope",
          "string"
        ],
        [
          "$.runs[4].seed_control",
          "string"
        ],
        [
          "$.runs[4].shell_version",
          "string"
        ],
        [
          "$.runs[4].started_at",
          "string"
        ],
        [
          "$.runs[4].task_id",
          "string"
        ],
        [
          "$.runs[4].usage",
          "object"
        ],
        [
          "$.runs[4].usage.cost_usd",
          "number"
        ],
        [
          "$.runs[4].usage.tokens_in",
          "number"
        ],
        [
          "$.runs[4].usage.tokens_out",
          "number"
        ],
        [
          "$.runs[4].usage.turns",
          "number"
        ],
        [
          "$.runs[4].usage.wall_ms",
          "number"
        ],
        [
          "$.runs[4].verification",
          "object"
        ],
        [
          "$.runs[4].verification.matches",
          "bool:True"
        ],
        [
          "$.runs[4].verification.tests_hash_after",
          "string"
        ],
        [
          "$.runs[4].verification.tests_hash_before",
          "string"
        ],
        [
          "$.runs[4].verifier_hash",
          "string"
        ],
        [
          "$.runs[5]",
          "object"
        ],
        [
          "$.runs[5].artifacts",
          "object"
        ],
        [
          "$.runs[5].artifacts.solution.py",
          "string"
        ],
        [
          "$.runs[5].attempt",
          "number"
        ],
        [
          "$.runs[5].ext",
          "object"
        ],
        [
          "$.runs[5].finished_at",
          "string"
        ],
        [
          "$.runs[5].genome_id",
          "string"
        ],
        [
          "$.runs[5].outcome",
          "string"
        ],
        [
          "$.runs[5].run_id",
          "string"
        ],
        [
          "$.runs[5].scenario_version",
          "string"
        ],
        [
          "$.runs[5].schema_version",
          "string"
        ],
        [
          "$.runs[5].scope",
          "string"
        ],
        [
          "$.runs[5].seed_control",
          "string"
        ],
        [
          "$.runs[5].shell_version",
          "string"
        ],
        [
          "$.runs[5].started_at",
          "string"
        ],
        [
          "$.runs[5].task_id",
          "string"
        ],
        [
          "$.runs[5].usage",
          "object"
        ],
        [
          "$.runs[5].usage.cost_usd",
          "number"
        ],
        [
          "$.runs[5].usage.tokens_in",
          "number"
        ],
        [
          "$.runs[5].usage.tokens_out",
          "number"
        ],
        [
          "$.runs[5].usage.turns",
          "number"
        ],
        [
          "$.runs[5].usage.wall_ms",
          "number"
        ],
        [
          "$.runs[5].verification",
          "object"
        ],
        [
          "$.runs[5].verification.matches",
          "bool:True"
        ],
        [
          "$.runs[5].verification.tests_hash_after",
          "string"
        ],
        [
          "$.runs[5].verification.tests_hash_before",
          "string"
        ],
        [
          "$.runs[5].verifier_hash",
          "string"
        ],
        [
          "$.scenario_id",
          "string"
        ],
        [
          "$.scenario_version",
          "string"
        ]
      ]
    },
    "pre_metrics": {
      "keys": [
        "$.decisions",
        "$.decisions[0].baseline_genome_id",
        "$.decisions[0].budget_check",
        "$.decisions[0].budget_check.execution_exceeded",
        "$.decisions[0].budget_check.overhead_exceeded",
        "$.decisions[0].budget_check.reason",
        "$.decisions[0].candidate_genome_id",
        "$.decisions[0].created_at",
        "$.decisions[0].decision_id",
        "$.decisions[0].ext",
        "$.decisions[0].ext.inputs",
        "$.decisions[0].ext.inputs.baseline_result_id",
        "$.decisions[0].ext.inputs.candidate_result_id",
        "$.decisions[0].ext.overhead_spend_usd",
        "$.decisions[0].invalid_ratio",
        "$.decisions[0].invalid_ratio.baseline",
        "$.decisions[0].invalid_ratio.candidate",
        "$.decisions[0].invalid_ratio.threshold",
        "$.decisions[0].k",
        "$.decisions[0].limits_identical",
        "$.decisions[0].paired",
        "$.decisions[0].paired[0].baseline_cost_usd",
        "$.decisions[0].paired[0].baseline_passed",
        "$.decisions[0].paired[0].candidate_cost_usd",
        "$.decisions[0].paired[0].candidate_passed",
        "$.decisions[0].paired[0].task_id",
        "$.decisions[0].paired[1].baseline_cost_usd",
        "$.decisions[0].paired[1].baseline_passed",
        "$.decisions[0].paired[1].candidate_cost_usd",
        "$.decisions[0].paired[1].candidate_passed",
        "$.decisions[0].paired[1].task_id",
        "$.decisions[0].reason",
        "$.decisions[0].scenario_version",
        "$.decisions[0].schema_version",
        "$.decisions[0].scores",
        "$.decisions[0].scores.baseline",
        "$.decisions[0].scores.baseline.cost_per_solved_usd",
        "$.decisions[0].scores.baseline.genome_id",
        "$.decisions[0].scores.baseline.solved",
        "$.decisions[0].scores.baseline.total",
        "$.decisions[0].scores.candidate",
        "$.decisions[0].scores.candidate.cost_per_solved_usd",
        "$.decisions[0].scores.candidate.genome_id",
        "$.decisions[0].scores.candidate.solved",
        "$.decisions[0].scores.candidate.total",
        "$.decisions[0].shell_version",
        "$.decisions[0].verdict",
        "$.decisions[0].verifier_hash",
        "$.results",
        "$.results[0].ext",
        "$.results[0].generated_at",
        "$.results[0].genome_id",
        "$.results[0].result_id",
        "$.results[0].run_refs",
        "$.results[0].scenario_version",
        "$.results[0].schema_version",
        "$.results[0].scope",
        "$.results[0].scores",
        "$.results[0].scores.holdout",
        "$.results[0].scores.holdout.cost_per_solved_usd",
        "$.results[0].scores.holdout.cost_total_usd",
        "$.results[0].scores.holdout.invalid_ratio",
        "$.results[0].scores.holdout.pass_at_1_first_attempt",
        "$.results[0].scores.holdout.solved",
        "$.results[0].scores.holdout.total",
        "$.results[0].scores.search",
        "$.results[0].scores.search.cost_per_solved_usd",
        "$.results[0].scores.search.cost_total_usd",
        "$.results[0].scores.search.invalid_ratio",
        "$.results[0].scores.search.pass_at_1_first_attempt",
        "$.results[0].scores.search.solved",
        "$.results[0].scores.search.total",
        "$.results[0].seed_control",
        "$.results[1].ext",
        "$.results[1].generated_at",
        "$.results[1].genome_id",
        "$.results[1].result_id",
        "$.results[1].run_refs",
        "$.results[1].scenario_version",
        "$.results[1].schema_version",
        "$.results[1].scope",
        "$.results[1].scores",
        "$.results[1].scores.search",
        "$.results[1].scores.search.cost_per_solved_usd",
        "$.results[1].scores.search.cost_total_usd",
        "$.results[1].scores.search.invalid_ratio",
        "$.results[1].scores.search.pass_at_1_first_attempt",
        "$.results[1].scores.search.solved",
        "$.results[1].scores.search.total",
        "$.results[1].seed_control",
        "$.results[2].ext",
        "$.results[2].generated_at",
        "$.results[2].genome_id",
        "$.results[2].result_id",
        "$.results[2].run_refs",
        "$.results[2].scenario_version",
        "$.results[2].schema_version",
        "$.results[2].scope",
        "$.results[2].scores",
        "$.results[2].scores.holdout",
        "$.results[2].scores.holdout.cost_per_solved_usd",
        "$.results[2].scores.holdout.cost_total_usd",
        "$.results[2].scores.holdout.invalid_ratio",
        "$.results[2].scores.holdout.pass_at_1_first_attempt",
        "$.results[2].scores.holdout.solved",
        "$.results[2].scores.holdout.total",
        "$.results[2].scores.search",
        "$.results[2].scores.search.cost_per_solved_usd",
        "$.results[2].scores.search.cost_total_usd",
        "$.results[2].scores.search.invalid_ratio",
        "$.results[2].scores.search.pass_at_1_first_attempt",
        "$.results[2].scores.search.solved",
        "$.results[2].scores.search.total",
        "$.results[2].seed_control",
        "$.results[3].ext",
        "$.results[3].generated_at",
        "$.results[3].genome_id",
        "$.results[3].result_id",
        "$.results[3].run_refs",
        "$.results[3].scenario_version",
        "$.results[3].schema_version",
        "$.results[3].scope",
        "$.results[3].scores",
        "$.results[3].scores.search",
        "$.results[3].scores.search.cost_per_solved_usd",
        "$.results[3].scores.search.cost_total_usd",
        "$.results[3].scores.search.invalid_ratio",
        "$.results[3].scores.search.pass_at_1_first_attempt",
        "$.results[3].scores.search.solved",
        "$.results[3].scores.search.total",
        "$.results[3].seed_control",
        "$.runs",
        "$.runs[0].artifacts",
        "$.runs[0].artifacts.solution.py",
        "$.runs[0].attempt",
        "$.runs[0].ext",
        "$.runs[0].finished_at",
        "$.runs[0].genome_id",
        "$.runs[0].outcome",
        "$.runs[0].run_id",
        "$.runs[0].scenario_version",
        "$.runs[0].schema_version",
        "$.runs[0].scope",
        "$.runs[0].seed_control",
        "$.runs[0].shell_version",
        "$.runs[0].started_at",
        "$.runs[0].task_id",
        "$.runs[0].usage",
        "$.runs[0].usage.cost_usd",
        "$.runs[0].usage.tokens_in",
        "$.runs[0].usage.tokens_out",
        "$.runs[0].usage.turns",
        "$.runs[0].usage.wall_ms",
        "$.runs[0].verification",
        "$.runs[0].verification.matches",
        "$.runs[0].verification.tests_hash_after",
        "$.runs[0].verification.tests_hash_before",
        "$.runs[0].verifier_hash",
        "$.runs[1].artifacts",
        "$.runs[1].artifacts.solution.py",
        "$.runs[1].attempt",
        "$.runs[1].ext",
        "$.runs[1].finished_at",
        "$.runs[1].genome_id",
        "$.runs[1].outcome",
        "$.runs[1].run_id",
        "$.runs[1].scenario_version",
        "$.runs[1].schema_version",
        "$.runs[1].scope",
        "$.runs[1].seed_control",
        "$.runs[1].shell_version",
        "$.runs[1].started_at",
        "$.runs[1].task_id",
        "$.runs[1].usage",
        "$.runs[1].usage.cost_usd",
        "$.runs[1].usage.tokens_in",
        "$.runs[1].usage.tokens_out",
        "$.runs[1].usage.turns",
        "$.runs[1].usage.wall_ms",
        "$.runs[1].verification",
        "$.runs[1].verification.matches",
        "$.runs[1].verification.tests_hash_after",
        "$.runs[1].verification.tests_hash_before",
        "$.runs[1].verifier_hash",
        "$.runs[2].artifacts",
        "$.runs[2].artifacts.solution.py",
        "$.runs[2].attempt",
        "$.runs[2].ext",
        "$.runs[2].finished_at",
        "$.runs[2].genome_id",
        "$.runs[2].outcome",
        "$.runs[2].run_id",
        "$.runs[2].scenario_version",
        "$.runs[2].schema_version",
        "$.runs[2].scope",
        "$.runs[2].seed_control",
        "$.runs[2].shell_version",
        "$.runs[2].started_at",
        "$.runs[2].task_id",
        "$.runs[2].usage",
        "$.runs[2].usage.cost_usd",
        "$.runs[2].usage.tokens_in",
        "$.runs[2].usage.tokens_out",
        "$.runs[2].usage.turns",
        "$.runs[2].usage.wall_ms",
        "$.runs[2].verification",
        "$.runs[2].verification.matches",
        "$.runs[2].verification.tests_hash_after",
        "$.runs[2].verification.tests_hash_before",
        "$.runs[2].verifier_hash",
        "$.runs[3].artifacts",
        "$.runs[3].artifacts.solution.py",
        "$.runs[3].attempt",
        "$.runs[3].ext",
        "$.runs[3].finished_at",
        "$.runs[3].genome_id",
        "$.runs[3].outcome",
        "$.runs[3].run_id",
        "$.runs[3].scenario_version",
        "$.runs[3].schema_version",
        "$.runs[3].scope",
        "$.runs[3].seed_control",
        "$.runs[3].shell_version",
        "$.runs[3].started_at",
        "$.runs[3].task_id",
        "$.runs[3].usage",
        "$.runs[3].usage.cost_usd",
        "$.runs[3].usage.tokens_in",
        "$.runs[3].usage.tokens_out",
        "$.runs[3].usage.turns",
        "$.runs[3].usage.wall_ms",
        "$.runs[3].verification",
        "$.runs[3].verification.matches",
        "$.runs[3].verification.tests_hash_after",
        "$.runs[3].verification.tests_hash_before",
        "$.runs[3].verifier_hash",
        "$.runs[4].artifacts",
        "$.runs[4].artifacts.solution.py",
        "$.runs[4].attempt",
        "$.runs[4].ext",
        "$.runs[4].finished_at",
        "$.runs[4].genome_id",
        "$.runs[4].outcome",
        "$.runs[4].run_id",
        "$.runs[4].scenario_version",
        "$.runs[4].schema_version",
        "$.runs[4].scope",
        "$.runs[4].seed_control",
        "$.runs[4].shell_version",
        "$.runs[4].started_at",
        "$.runs[4].task_id",
        "$.runs[4].usage",
        "$.runs[4].usage.cost_usd",
        "$.runs[4].usage.tokens_in",
        "$.runs[4].usage.tokens_out",
        "$.runs[4].usage.turns",
        "$.runs[4].usage.wall_ms",
        "$.runs[4].verification",
        "$.runs[4].verification.matches",
        "$.runs[4].verification.tests_hash_after",
        "$.runs[4].verification.tests_hash_before",
        "$.runs[4].verifier_hash",
        "$.runs[5].artifacts",
        "$.runs[5].artifacts.solution.py",
        "$.runs[5].attempt",
        "$.runs[5].ext",
        "$.runs[5].finished_at",
        "$.runs[5].genome_id",
        "$.runs[5].outcome",
        "$.runs[5].run_id",
        "$.runs[5].scenario_version",
        "$.runs[5].schema_version",
        "$.runs[5].scope",
        "$.runs[5].seed_control",
        "$.runs[5].shell_version",
        "$.runs[5].started_at",
        "$.runs[5].task_id",
        "$.runs[5].usage",
        "$.runs[5].usage.cost_usd",
        "$.runs[5].usage.tokens_in",
        "$.runs[5].usage.tokens_out",
        "$.runs[5].usage.turns",
        "$.runs[5].usage.wall_ms",
        "$.runs[5].verification",
        "$.runs[5].verification.matches",
        "$.runs[5].verification.tests_hash_after",
        "$.runs[5].verification.tests_hash_before",
        "$.runs[5].verifier_hash",
        "$.scenario_id",
        "$.scenario_version"
      ],
      "numbers": [
        [
          "$.decisions[0].ext.overhead_spend_usd",
          0.0
        ],
        [
          "$.decisions[0].invalid_ratio.baseline",
          0.0
        ],
        [
          "$.decisions[0].invalid_ratio.candidate",
          0.0
        ],
        [
          "$.decisions[0].invalid_ratio.threshold",
          0.25
        ],
        [
          "$.decisions[0].k",
          0.0
        ],
        [
          "$.decisions[0].paired[0].baseline_cost_usd",
          1.84
        ],
        [
          "$.decisions[0].paired[0].candidate_cost_usd",
          0.92
        ],
        [
          "$.decisions[0].paired[1].baseline_cost_usd",
          1.84
        ],
        [
          "$.decisions[0].paired[1].candidate_cost_usd",
          0.92
        ],
        [
          "$.decisions[0].scores.baseline.cost_per_solved_usd",
          1.84
        ],
        [
          "$.decisions[0].scores.baseline.solved",
          2.0
        ],
        [
          "$.decisions[0].scores.baseline.total",
          2.0
        ],
        [
          "$.decisions[0].scores.candidate.cost_per_solved_usd",
          0.92
        ],
        [
          "$.decisions[0].scores.candidate.solved",
          2.0
        ],
        [
          "$.decisions[0].scores.candidate.total",
          2.0
        ],
        [
          "$.results[0].scores.holdout.cost_per_solved_usd",
          1.84
        ],
        [
          "$.results[0].scores.holdout.cost_total_usd",
          1.84
        ],
        [
          "$.results[0].scores.holdout.invalid_ratio",
          0.0
        ],
        [
          "$.results[0].scores.holdout.pass_at_1_first_attempt",
          1.0
        ],
        [
          "$.results[0].scores.holdout.solved",
          1.0
        ],
        [
          "$.results[0].scores.holdout.total",
          1.0
        ],
        [
          "$.results[0].scores.search.cost_per_solved_usd",
          1.84
        ],
        [
          "$.results[0].scores.search.cost_total_usd",
          3.68
        ],
        [
          "$.results[0].scores.search.invalid_ratio",
          0.0
        ],
        [
          "$.results[0].scores.search.pass_at_1_first_attempt",
          1.0
        ],
        [
          "$.results[0].scores.search.solved",
          2.0
        ],
        [
          "$.results[0].scores.search.total",
          2.0
        ],
        [
          "$.results[1].scores.search.cost_per_solved_usd",
          1.84
        ],
        [
          "$.results[1].scores.search.cost_total_usd",
          3.68
        ],
        [
          "$.results[1].scores.search.invalid_ratio",
          0.0
        ],
        [
          "$.results[1].scores.search.pass_at_1_first_attempt",
          1.0
        ],
        [
          "$.results[1].scores.search.solved",
          2.0
        ],
        [
          "$.results[1].scores.search.total",
          2.0
        ],
        [
          "$.results[2].scores.holdout.cost_per_solved_usd",
          0.92
        ],
        [
          "$.results[2].scores.holdout.cost_total_usd",
          0.92
        ],
        [
          "$.results[2].scores.holdout.invalid_ratio",
          0.0
        ],
        [
          "$.results[2].scores.holdout.pass_at_1_first_attempt",
          1.0
        ],
        [
          "$.results[2].scores.holdout.solved",
          1.0
        ],
        [
          "$.results[2].scores.holdout.total",
          1.0
        ],
        [
          "$.results[2].scores.search.cost_per_solved_usd",
          0.92
        ],
        [
          "$.results[2].scores.search.cost_total_usd",
          1.84
        ],
        [
          "$.results[2].scores.search.invalid_ratio",
          0.0
        ],
        [
          "$.results[2].scores.search.pass_at_1_first_attempt",
          1.0
        ],
        [
          "$.results[2].scores.search.solved",
          2.0
        ],
        [
          "$.results[2].scores.search.total",
          2.0
        ],
        [
          "$.results[3].scores.search.cost_per_solved_usd",
          0.92
        ],
        [
          "$.results[3].scores.search.cost_total_usd",
          1.84
        ],
        [
          "$.results[3].scores.search.invalid_ratio",
          0.0
        ],
        [
          "$.results[3].scores.search.pass_at_1_first_attempt",
          1.0
        ],
        [
          "$.results[3].scores.search.solved",
          2.0
        ],
        [
          "$.results[3].scores.search.total",
          2.0
        ],
        [
          "$.runs[0].attempt",
          1.0
        ],
        [
          "$.runs[0].usage.cost_usd",
          1.84
        ],
        [
          "$.runs[0].usage.tokens_in",
          1150.0
        ],
        [
          "$.runs[0].usage.tokens_out",
          800.0
        ],
        [
          "$.runs[0].usage.turns",
          4.0
        ],
        [
          "$.runs[0].usage.wall_ms",
          1840.0
        ],
        [
          "$.runs[1].attempt",
          1.0
        ],
        [
          "$.runs[1].usage.cost_usd",
          1.84
        ],
        [
          "$.runs[1].usage.tokens_in",
          1150.0
        ],
        [
          "$.runs[1].usage.tokens_out",
          800.0
        ],
        [
          "$.runs[1].usage.turns",
          4.0
        ],
        [
          "$.runs[1].usage.wall_ms",
          1840.0
        ],
        [
          "$.runs[2].attempt",
          1.0
        ],
        [
          "$.runs[2].usage.cost_usd",
          1.84
        ],
        [
          "$.runs[2].usage.tokens_in",
          1150.0
        ],
        [
          "$.runs[2].usage.tokens_out",
          800.0
        ],
        [
          "$.runs[2].usage.turns",
          4.0
        ],
        [
          "$.runs[2].usage.wall_ms",
          1840.0
        ],
        [
          "$.runs[3].attempt",
          1.0
        ],
        [
          "$.runs[3].usage.cost_usd",
          0.92
        ],
        [
          "$.runs[3].usage.tokens_in",
          1150.0
        ],
        [
          "$.runs[3].usage.tokens_out",
          400.0
        ],
        [
          "$.runs[3].usage.turns",
          2.0
        ],
        [
          "$.runs[3].usage.wall_ms",
          920.0
        ],
        [
          "$.runs[4].attempt",
          1.0
        ],
        [
          "$.runs[4].usage.cost_usd",
          0.92
        ],
        [
          "$.runs[4].usage.tokens_in",
          1150.0
        ],
        [
          "$.runs[4].usage.tokens_out",
          400.0
        ],
        [
          "$.runs[4].usage.turns",
          2.0
        ],
        [
          "$.runs[4].usage.wall_ms",
          920.0
        ],
        [
          "$.runs[5].attempt",
          1.0
        ],
        [
          "$.runs[5].usage.cost_usd",
          0.92
        ],
        [
          "$.runs[5].usage.tokens_in",
          1150.0
        ],
        [
          "$.runs[5].usage.tokens_out",
          400.0
        ],
        [
          "$.runs[5].usage.turns",
          2.0
        ],
        [
          "$.runs[5].usage.wall_ms",
          920.0
        ]
      ],
      "shape": [
        [
          "$",
          "object"
        ],
        [
          "$.decisions",
          "array[1]"
        ],
        [
          "$.decisions[0]",
          "object"
        ],
        [
          "$.decisions[0].baseline_genome_id",
          "string"
        ],
        [
          "$.decisions[0].budget_check",
          "object"
        ],
        [
          "$.decisions[0].budget_check.execution_exceeded",
          "bool:False"
        ],
        [
          "$.decisions[0].budget_check.overhead_exceeded",
          "bool:False"
        ],
        [
          "$.decisions[0].budget_check.reason",
          "string"
        ],
        [
          "$.decisions[0].candidate_genome_id",
          "string"
        ],
        [
          "$.decisions[0].created_at",
          "string"
        ],
        [
          "$.decisions[0].decision_id",
          "string"
        ],
        [
          "$.decisions[0].ext",
          "object"
        ],
        [
          "$.decisions[0].ext.inputs",
          "object"
        ],
        [
          "$.decisions[0].ext.inputs.baseline_result_id",
          "string"
        ],
        [
          "$.decisions[0].ext.inputs.candidate_result_id",
          "string"
        ],
        [
          "$.decisions[0].ext.overhead_spend_usd",
          "number"
        ],
        [
          "$.decisions[0].invalid_ratio",
          "object"
        ],
        [
          "$.decisions[0].invalid_ratio.baseline",
          "number"
        ],
        [
          "$.decisions[0].invalid_ratio.candidate",
          "number"
        ],
        [
          "$.decisions[0].invalid_ratio.threshold",
          "number"
        ],
        [
          "$.decisions[0].k",
          "number"
        ],
        [
          "$.decisions[0].limits_identical",
          "bool:True"
        ],
        [
          "$.decisions[0].paired",
          "array[2]"
        ],
        [
          "$.decisions[0].paired[0]",
          "object"
        ],
        [
          "$.decisions[0].paired[0].baseline_cost_usd",
          "number"
        ],
        [
          "$.decisions[0].paired[0].baseline_passed",
          "bool:True"
        ],
        [
          "$.decisions[0].paired[0].candidate_cost_usd",
          "number"
        ],
        [
          "$.decisions[0].paired[0].candidate_passed",
          "bool:True"
        ],
        [
          "$.decisions[0].paired[0].task_id",
          "string"
        ],
        [
          "$.decisions[0].paired[1]",
          "object"
        ],
        [
          "$.decisions[0].paired[1].baseline_cost_usd",
          "number"
        ],
        [
          "$.decisions[0].paired[1].baseline_passed",
          "bool:True"
        ],
        [
          "$.decisions[0].paired[1].candidate_cost_usd",
          "number"
        ],
        [
          "$.decisions[0].paired[1].candidate_passed",
          "bool:True"
        ],
        [
          "$.decisions[0].paired[1].task_id",
          "string"
        ],
        [
          "$.decisions[0].reason",
          "string"
        ],
        [
          "$.decisions[0].scenario_version",
          "string"
        ],
        [
          "$.decisions[0].schema_version",
          "string"
        ],
        [
          "$.decisions[0].scores",
          "object"
        ],
        [
          "$.decisions[0].scores.baseline",
          "object"
        ],
        [
          "$.decisions[0].scores.baseline.cost_per_solved_usd",
          "number"
        ],
        [
          "$.decisions[0].scores.baseline.genome_id",
          "string"
        ],
        [
          "$.decisions[0].scores.baseline.solved",
          "number"
        ],
        [
          "$.decisions[0].scores.baseline.total",
          "number"
        ],
        [
          "$.decisions[0].scores.candidate",
          "object"
        ],
        [
          "$.decisions[0].scores.candidate.cost_per_solved_usd",
          "number"
        ],
        [
          "$.decisions[0].scores.candidate.genome_id",
          "string"
        ],
        [
          "$.decisions[0].scores.candidate.solved",
          "number"
        ],
        [
          "$.decisions[0].scores.candidate.total",
          "number"
        ],
        [
          "$.decisions[0].shell_version",
          "string"
        ],
        [
          "$.decisions[0].verdict",
          "string"
        ],
        [
          "$.decisions[0].verifier_hash",
          "string"
        ],
        [
          "$.results",
          "array[4]"
        ],
        [
          "$.results[0]",
          "object"
        ],
        [
          "$.results[0].ext",
          "object"
        ],
        [
          "$.results[0].generated_at",
          "string"
        ],
        [
          "$.results[0].genome_id",
          "string"
        ],
        [
          "$.results[0].result_id",
          "string"
        ],
        [
          "$.results[0].run_refs",
          "array[3]"
        ],
        [
          "$.results[0].run_refs[0]",
          "string"
        ],
        [
          "$.results[0].run_refs[1]",
          "string"
        ],
        [
          "$.results[0].run_refs[2]",
          "string"
        ],
        [
          "$.results[0].scenario_version",
          "string"
        ],
        [
          "$.results[0].schema_version",
          "string"
        ],
        [
          "$.results[0].scope",
          "string"
        ],
        [
          "$.results[0].scores",
          "object"
        ],
        [
          "$.results[0].scores.holdout",
          "object"
        ],
        [
          "$.results[0].scores.holdout.cost_per_solved_usd",
          "number"
        ],
        [
          "$.results[0].scores.holdout.cost_total_usd",
          "number"
        ],
        [
          "$.results[0].scores.holdout.invalid_ratio",
          "number"
        ],
        [
          "$.results[0].scores.holdout.pass_at_1_first_attempt",
          "number"
        ],
        [
          "$.results[0].scores.holdout.solved",
          "number"
        ],
        [
          "$.results[0].scores.holdout.total",
          "number"
        ],
        [
          "$.results[0].scores.search",
          "object"
        ],
        [
          "$.results[0].scores.search.cost_per_solved_usd",
          "number"
        ],
        [
          "$.results[0].scores.search.cost_total_usd",
          "number"
        ],
        [
          "$.results[0].scores.search.invalid_ratio",
          "number"
        ],
        [
          "$.results[0].scores.search.pass_at_1_first_attempt",
          "number"
        ],
        [
          "$.results[0].scores.search.solved",
          "number"
        ],
        [
          "$.results[0].scores.search.total",
          "number"
        ],
        [
          "$.results[0].seed_control",
          "string"
        ],
        [
          "$.results[1]",
          "object"
        ],
        [
          "$.results[1].ext",
          "object"
        ],
        [
          "$.results[1].generated_at",
          "string"
        ],
        [
          "$.results[1].genome_id",
          "string"
        ],
        [
          "$.results[1].result_id",
          "string"
        ],
        [
          "$.results[1].run_refs",
          "array[2]"
        ],
        [
          "$.results[1].run_refs[0]",
          "string"
        ],
        [
          "$.results[1].run_refs[1]",
          "string"
        ],
        [
          "$.results[1].scenario_version",
          "string"
        ],
        [
          "$.results[1].schema_version",
          "string"
        ],
        [
          "$.results[1].scope",
          "string"
        ],
        [
          "$.results[1].scores",
          "object"
        ],
        [
          "$.results[1].scores.search",
          "object"
        ],
        [
          "$.results[1].scores.search.cost_per_solved_usd",
          "number"
        ],
        [
          "$.results[1].scores.search.cost_total_usd",
          "number"
        ],
        [
          "$.results[1].scores.search.invalid_ratio",
          "number"
        ],
        [
          "$.results[1].scores.search.pass_at_1_first_attempt",
          "number"
        ],
        [
          "$.results[1].scores.search.solved",
          "number"
        ],
        [
          "$.results[1].scores.search.total",
          "number"
        ],
        [
          "$.results[1].seed_control",
          "string"
        ],
        [
          "$.results[2]",
          "object"
        ],
        [
          "$.results[2].ext",
          "object"
        ],
        [
          "$.results[2].generated_at",
          "string"
        ],
        [
          "$.results[2].genome_id",
          "string"
        ],
        [
          "$.results[2].result_id",
          "string"
        ],
        [
          "$.results[2].run_refs",
          "array[3]"
        ],
        [
          "$.results[2].run_refs[0]",
          "string"
        ],
        [
          "$.results[2].run_refs[1]",
          "string"
        ],
        [
          "$.results[2].run_refs[2]",
          "string"
        ],
        [
          "$.results[2].scenario_version",
          "string"
        ],
        [
          "$.results[2].schema_version",
          "string"
        ],
        [
          "$.results[2].scope",
          "string"
        ],
        [
          "$.results[2].scores",
          "object"
        ],
        [
          "$.results[2].scores.holdout",
          "object"
        ],
        [
          "$.results[2].scores.holdout.cost_per_solved_usd",
          "number"
        ],
        [
          "$.results[2].scores.holdout.cost_total_usd",
          "number"
        ],
        [
          "$.results[2].scores.holdout.invalid_ratio",
          "number"
        ],
        [
          "$.results[2].scores.holdout.pass_at_1_first_attempt",
          "number"
        ],
        [
          "$.results[2].scores.holdout.solved",
          "number"
        ],
        [
          "$.results[2].scores.holdout.total",
          "number"
        ],
        [
          "$.results[2].scores.search",
          "object"
        ],
        [
          "$.results[2].scores.search.cost_per_solved_usd",
          "number"
        ],
        [
          "$.results[2].scores.search.cost_total_usd",
          "number"
        ],
        [
          "$.results[2].scores.search.invalid_ratio",
          "number"
        ],
        [
          "$.results[2].scores.search.pass_at_1_first_attempt",
          "number"
        ],
        [
          "$.results[2].scores.search.solved",
          "number"
        ],
        [
          "$.results[2].scores.search.total",
          "number"
        ],
        [
          "$.results[2].seed_control",
          "string"
        ],
        [
          "$.results[3]",
          "object"
        ],
        [
          "$.results[3].ext",
          "object"
        ],
        [
          "$.results[3].generated_at",
          "string"
        ],
        [
          "$.results[3].genome_id",
          "string"
        ],
        [
          "$.results[3].result_id",
          "string"
        ],
        [
          "$.results[3].run_refs",
          "array[2]"
        ],
        [
          "$.results[3].run_refs[0]",
          "string"
        ],
        [
          "$.results[3].run_refs[1]",
          "string"
        ],
        [
          "$.results[3].scenario_version",
          "string"
        ],
        [
          "$.results[3].schema_version",
          "string"
        ],
        [
          "$.results[3].scope",
          "string"
        ],
        [
          "$.results[3].scores",
          "object"
        ],
        [
          "$.results[3].scores.search",
          "object"
        ],
        [
          "$.results[3].scores.search.cost_per_solved_usd",
          "number"
        ],
        [
          "$.results[3].scores.search.cost_total_usd",
          "number"
        ],
        [
          "$.results[3].scores.search.invalid_ratio",
          "number"
        ],
        [
          "$.results[3].scores.search.pass_at_1_first_attempt",
          "number"
        ],
        [
          "$.results[3].scores.search.solved",
          "number"
        ],
        [
          "$.results[3].scores.search.total",
          "number"
        ],
        [
          "$.results[3].seed_control",
          "string"
        ],
        [
          "$.runs",
          "array[6]"
        ],
        [
          "$.runs[0]",
          "object"
        ],
        [
          "$.runs[0].artifacts",
          "object"
        ],
        [
          "$.runs[0].artifacts.solution.py",
          "string"
        ],
        [
          "$.runs[0].attempt",
          "number"
        ],
        [
          "$.runs[0].ext",
          "object"
        ],
        [
          "$.runs[0].finished_at",
          "string"
        ],
        [
          "$.runs[0].genome_id",
          "string"
        ],
        [
          "$.runs[0].outcome",
          "string"
        ],
        [
          "$.runs[0].run_id",
          "string"
        ],
        [
          "$.runs[0].scenario_version",
          "string"
        ],
        [
          "$.runs[0].schema_version",
          "string"
        ],
        [
          "$.runs[0].scope",
          "string"
        ],
        [
          "$.runs[0].seed_control",
          "string"
        ],
        [
          "$.runs[0].shell_version",
          "string"
        ],
        [
          "$.runs[0].started_at",
          "string"
        ],
        [
          "$.runs[0].task_id",
          "string"
        ],
        [
          "$.runs[0].usage",
          "object"
        ],
        [
          "$.runs[0].usage.cost_usd",
          "number"
        ],
        [
          "$.runs[0].usage.tokens_in",
          "number"
        ],
        [
          "$.runs[0].usage.tokens_out",
          "number"
        ],
        [
          "$.runs[0].usage.turns",
          "number"
        ],
        [
          "$.runs[0].usage.wall_ms",
          "number"
        ],
        [
          "$.runs[0].verification",
          "object"
        ],
        [
          "$.runs[0].verification.matches",
          "bool:True"
        ],
        [
          "$.runs[0].verification.tests_hash_after",
          "string"
        ],
        [
          "$.runs[0].verification.tests_hash_before",
          "string"
        ],
        [
          "$.runs[0].verifier_hash",
          "string"
        ],
        [
          "$.runs[1]",
          "object"
        ],
        [
          "$.runs[1].artifacts",
          "object"
        ],
        [
          "$.runs[1].artifacts.solution.py",
          "string"
        ],
        [
          "$.runs[1].attempt",
          "number"
        ],
        [
          "$.runs[1].ext",
          "object"
        ],
        [
          "$.runs[1].finished_at",
          "string"
        ],
        [
          "$.runs[1].genome_id",
          "string"
        ],
        [
          "$.runs[1].outcome",
          "string"
        ],
        [
          "$.runs[1].run_id",
          "string"
        ],
        [
          "$.runs[1].scenario_version",
          "string"
        ],
        [
          "$.runs[1].schema_version",
          "string"
        ],
        [
          "$.runs[1].scope",
          "string"
        ],
        [
          "$.runs[1].seed_control",
          "string"
        ],
        [
          "$.runs[1].shell_version",
          "string"
        ],
        [
          "$.runs[1].started_at",
          "string"
        ],
        [
          "$.runs[1].task_id",
          "string"
        ],
        [
          "$.runs[1].usage",
          "object"
        ],
        [
          "$.runs[1].usage.cost_usd",
          "number"
        ],
        [
          "$.runs[1].usage.tokens_in",
          "number"
        ],
        [
          "$.runs[1].usage.tokens_out",
          "number"
        ],
        [
          "$.runs[1].usage.turns",
          "number"
        ],
        [
          "$.runs[1].usage.wall_ms",
          "number"
        ],
        [
          "$.runs[1].verification",
          "object"
        ],
        [
          "$.runs[1].verification.matches",
          "bool:True"
        ],
        [
          "$.runs[1].verification.tests_hash_after",
          "string"
        ],
        [
          "$.runs[1].verification.tests_hash_before",
          "string"
        ],
        [
          "$.runs[1].verifier_hash",
          "string"
        ],
        [
          "$.runs[2]",
          "object"
        ],
        [
          "$.runs[2].artifacts",
          "object"
        ],
        [
          "$.runs[2].artifacts.solution.py",
          "string"
        ],
        [
          "$.runs[2].attempt",
          "number"
        ],
        [
          "$.runs[2].ext",
          "object"
        ],
        [
          "$.runs[2].finished_at",
          "string"
        ],
        [
          "$.runs[2].genome_id",
          "string"
        ],
        [
          "$.runs[2].outcome",
          "string"
        ],
        [
          "$.runs[2].run_id",
          "string"
        ],
        [
          "$.runs[2].scenario_version",
          "string"
        ],
        [
          "$.runs[2].schema_version",
          "string"
        ],
        [
          "$.runs[2].scope",
          "string"
        ],
        [
          "$.runs[2].seed_control",
          "string"
        ],
        [
          "$.runs[2].shell_version",
          "string"
        ],
        [
          "$.runs[2].started_at",
          "string"
        ],
        [
          "$.runs[2].task_id",
          "string"
        ],
        [
          "$.runs[2].usage",
          "object"
        ],
        [
          "$.runs[2].usage.cost_usd",
          "number"
        ],
        [
          "$.runs[2].usage.tokens_in",
          "number"
        ],
        [
          "$.runs[2].usage.tokens_out",
          "number"
        ],
        [
          "$.runs[2].usage.turns",
          "number"
        ],
        [
          "$.runs[2].usage.wall_ms",
          "number"
        ],
        [
          "$.runs[2].verification",
          "object"
        ],
        [
          "$.runs[2].verification.matches",
          "bool:True"
        ],
        [
          "$.runs[2].verification.tests_hash_after",
          "string"
        ],
        [
          "$.runs[2].verification.tests_hash_before",
          "string"
        ],
        [
          "$.runs[2].verifier_hash",
          "string"
        ],
        [
          "$.runs[3]",
          "object"
        ],
        [
          "$.runs[3].artifacts",
          "object"
        ],
        [
          "$.runs[3].artifacts.solution.py",
          "string"
        ],
        [
          "$.runs[3].attempt",
          "number"
        ],
        [
          "$.runs[3].ext",
          "object"
        ],
        [
          "$.runs[3].finished_at",
          "string"
        ],
        [
          "$.runs[3].genome_id",
          "string"
        ],
        [
          "$.runs[3].outcome",
          "string"
        ],
        [
          "$.runs[3].run_id",
          "string"
        ],
        [
          "$.runs[3].scenario_version",
          "string"
        ],
        [
          "$.runs[3].schema_version",
          "string"
        ],
        [
          "$.runs[3].scope",
          "string"
        ],
        [
          "$.runs[3].seed_control",
          "string"
        ],
        [
          "$.runs[3].shell_version",
          "string"
        ],
        [
          "$.runs[3].started_at",
          "string"
        ],
        [
          "$.runs[3].task_id",
          "string"
        ],
        [
          "$.runs[3].usage",
          "object"
        ],
        [
          "$.runs[3].usage.cost_usd",
          "number"
        ],
        [
          "$.runs[3].usage.tokens_in",
          "number"
        ],
        [
          "$.runs[3].usage.tokens_out",
          "number"
        ],
        [
          "$.runs[3].usage.turns",
          "number"
        ],
        [
          "$.runs[3].usage.wall_ms",
          "number"
        ],
        [
          "$.runs[3].verification",
          "object"
        ],
        [
          "$.runs[3].verification.matches",
          "bool:True"
        ],
        [
          "$.runs[3].verification.tests_hash_after",
          "string"
        ],
        [
          "$.runs[3].verification.tests_hash_before",
          "string"
        ],
        [
          "$.runs[3].verifier_hash",
          "string"
        ],
        [
          "$.runs[4]",
          "object"
        ],
        [
          "$.runs[4].artifacts",
          "object"
        ],
        [
          "$.runs[4].artifacts.solution.py",
          "string"
        ],
        [
          "$.runs[4].attempt",
          "number"
        ],
        [
          "$.runs[4].ext",
          "object"
        ],
        [
          "$.runs[4].finished_at",
          "string"
        ],
        [
          "$.runs[4].genome_id",
          "string"
        ],
        [
          "$.runs[4].outcome",
          "string"
        ],
        [
          "$.runs[4].run_id",
          "string"
        ],
        [
          "$.runs[4].scenario_version",
          "string"
        ],
        [
          "$.runs[4].schema_version",
          "string"
        ],
        [
          "$.runs[4].scope",
          "string"
        ],
        [
          "$.runs[4].seed_control",
          "string"
        ],
        [
          "$.runs[4].shell_version",
          "string"
        ],
        [
          "$.runs[4].started_at",
          "string"
        ],
        [
          "$.runs[4].task_id",
          "string"
        ],
        [
          "$.runs[4].usage",
          "object"
        ],
        [
          "$.runs[4].usage.cost_usd",
          "number"
        ],
        [
          "$.runs[4].usage.tokens_in",
          "number"
        ],
        [
          "$.runs[4].usage.tokens_out",
          "number"
        ],
        [
          "$.runs[4].usage.turns",
          "number"
        ],
        [
          "$.runs[4].usage.wall_ms",
          "number"
        ],
        [
          "$.runs[4].verification",
          "object"
        ],
        [
          "$.runs[4].verification.matches",
          "bool:True"
        ],
        [
          "$.runs[4].verification.tests_hash_after",
          "string"
        ],
        [
          "$.runs[4].verification.tests_hash_before",
          "string"
        ],
        [
          "$.runs[4].verifier_hash",
          "string"
        ],
        [
          "$.runs[5]",
          "object"
        ],
        [
          "$.runs[5].artifacts",
          "object"
        ],
        [
          "$.runs[5].artifacts.solution.py",
          "string"
        ],
        [
          "$.runs[5].attempt",
          "number"
        ],
        [
          "$.runs[5].ext",
          "object"
        ],
        [
          "$.runs[5].finished_at",
          "string"
        ],
        [
          "$.runs[5].genome_id",
          "string"
        ],
        [
          "$.runs[5].outcome",
          "string"
        ],
        [
          "$.runs[5].run_id",
          "string"
        ],
        [
          "$.runs[5].scenario_version",
          "string"
        ],
        [
          "$.runs[5].schema_version",
          "string"
        ],
        [
          "$.runs[5].scope",
          "string"
        ],
        [
          "$.runs[5].seed_control",
          "string"
        ],
        [
          "$.runs[5].shell_version",
          "string"
        ],
        [
          "$.runs[5].started_at",
          "string"
        ],
        [
          "$.runs[5].task_id",
          "string"
        ],
        [
          "$.runs[5].usage",
          "object"
        ],
        [
          "$.runs[5].usage.cost_usd",
          "number"
        ],
        [
          "$.runs[5].usage.tokens_in",
          "number"
        ],
        [
          "$.runs[5].usage.tokens_out",
          "number"
        ],
        [
          "$.runs[5].usage.turns",
          "number"
        ],
        [
          "$.runs[5].usage.wall_ms",
          "number"
        ],
        [
          "$.runs[5].verification",
          "object"
        ],
        [
          "$.runs[5].verification.matches",
          "bool:True"
        ],
        [
          "$.runs[5].verification.tests_hash_after",
          "string"
        ],
        [
          "$.runs[5].verification.tests_hash_before",
          "string"
        ],
        [
          "$.runs[5].verifier_hash",
          "string"
        ],
        [
          "$.scenario_id",
          "string"
        ],
        [
          "$.scenario_version",
          "string"
        ]
      ]
    },
    "redaction_version": "1",
    "replacements": 0,
    "rules": [
      "api_key_sk",
      "github_token",
      "bearer_token",
      "key_value_secret",
      "home_path"
    ],
    "source_hash": "d180e888fcc800e06c9ec21296a1faa7565fb3737f5385b2ef83077245388b67"
  },
  "report_results": {
    "report_runs_total": {
      "by_status": {
        "passed": 2
      },
      "cost_usd": 2.76,
      "count": 2,
      "solved": 2,
      "tokens": {
        "in": 2300,
        "out": 1200
      },
      "wall_ms": 2760
    },
    "results": [
      {
        "generated_at": "2026-01-01T00:00:00Z",
        "genome_id": "sha256:6520c6095297d8f26cd23c59f3cd8d322fea13869e5dd1151f53a9bddc438cfa",
        "report_runs": {
          "by_status": {
            "passed": 1
          },
          "cost_usd": 1.84,
          "count": 1,
          "solved": 1,
          "tokens": {
            "in": 1150,
            "out": 800
          },
          "wall_ms": 1840
        },
        "result_id": "result-baseline-report",
        "scope": "report",
        "scores": {
          "holdout": {
            "cost_per_solved_usd": 1.84,
            "cost_total_usd": 1.84,
            "invalid_ratio": 0.0,
            "pass_at_1_first_attempt": 1.0,
            "solved": 1,
            "total": 1
          },
          "search": {
            "cost_per_solved_usd": 1.84,
            "cost_total_usd": 3.68,
            "invalid_ratio": 0.0,
            "pass_at_1_first_attempt": 1.0,
            "solved": 2,
            "total": 2
          }
        }
      },
      {
        "generated_at": "2026-01-01T00:00:00Z",
        "genome_id": "sha256:6be9015147fea1f3038553e287d519aa58ff9c80b847d2269d12a06b21defb38",
        "report_runs": {
          "by_status": {
            "passed": 1
          },
          "cost_usd": 0.92,
          "count": 1,
          "solved": 1,
          "tokens": {
            "in": 1150,
            "out": 400
          },
          "wall_ms": 920
        },
        "result_id": "result-candidate-report",
        "scope": "report",
        "scores": {
          "holdout": {
            "cost_per_solved_usd": 0.92,
            "cost_total_usd": 0.92,
            "invalid_ratio": 0.0,
            "pass_at_1_first_attempt": 1.0,
            "solved": 1,
            "total": 1
          },
          "search": {
            "cost_per_solved_usd": 0.92,
            "cost_total_usd": 1.84,
            "invalid_ratio": 0.0,
            "pass_at_1_first_attempt": 1.0,
            "solved": 2,
            "total": 2
          }
        }
      }
    ],
    "scope": "report"
  },
  "runs": {
    "runs": [
      {
        "attempt": 1,
        "cost_usd": 1.84,
        "finished_at": "2026-11-10T15:09:58Z",
        "genome_id": "sha256:6520c6095297d8f26cd23c59f3cd8d322fea13869e5dd1151f53a9bddc438cfa",
        "provenance": {
          "kind": "fixture",
          "live": false,
          "note": "B1 fixture runtime：确定性回放，不是 live 模型调用",
          "runtime_id": "fixture"
        },
        "run_id": "6520c609-smoke-add-a1-3cc404",
        "scope": "search",
        "solved": true,
        "started_at": "2026-11-10T15:09:58Z",
        "status": "passed",
        "task_id": "smoke-add",
        "tokens": {
          "in": 1150,
          "out": 800
        },
        "turns": 4,
        "verifier": {
          "hash": "8e72c3eddc06391abae742de18ddd85d8a1b75576f57fa56ebca60acf0ee497b",
          "matches": true,
          "tests_hash_after": "b87e9114b4199d9455ade662d10ce1863f7308c514bfbab10b27ac5a53eaba35",
          "tests_hash_before": "b87e9114b4199d9455ade662d10ce1863f7308c514bfbab10b27ac5a53eaba35"
        },
        "wall_ms": 1840
      },
      {
        "attempt": 1,
        "cost_usd": 1.84,
        "finished_at": "2026-11-10T15:09:58Z",
        "genome_id": "sha256:6520c6095297d8f26cd23c59f3cd8d322fea13869e5dd1151f53a9bddc438cfa",
        "provenance": {
          "kind": "fixture",
          "live": false,
          "note": "B1 fixture runtime：确定性回放，不是 live 模型调用",
          "runtime_id": "fixture"
        },
        "run_id": "6520c609-smoke-even-a1-a0bd9d",
        "scope": "search",
        "solved": true,
        "started_at": "2026-11-10T15:09:58Z",
        "status": "passed",
        "task_id": "smoke-even",
        "tokens": {
          "in": 1150,
          "out": 800
        },
        "turns": 4,
        "verifier": {
          "hash": "8e72c3eddc06391abae742de18ddd85d8a1b75576f57fa56ebca60acf0ee497b",
          "matches": true,
          "tests_hash_after": "912f7f66209aa3873aeb29a4f42c14033068095f0093fcaee3fe3689566cc493",
          "tests_hash_before": "912f7f66209aa3873aeb29a4f42c14033068095f0093fcaee3fe3689566cc493"
        },
        "wall_ms": 1840
      },
      {
        "attempt": 1,
        "cost_usd": 0.92,
        "finished_at": "2026-11-10T15:09:58Z",
        "genome_id": "sha256:6be9015147fea1f3038553e287d519aa58ff9c80b847d2269d12a06b21defb38",
        "provenance": {
          "kind": "fixture",
          "live": false,
          "note": "B1 fixture runtime：确定性回放，不是 live 模型调用",
          "runtime_id": "fixture"
        },
        "run_id": "6be90151-smoke-add-a1-097fa4",
        "scope": "search",
        "solved": true,
        "started_at": "2026-11-10T15:09:58Z",
        "status": "passed",
        "task_id": "smoke-add",
        "tokens": {
          "in": 1150,
          "out": 400
        },
        "turns": 2,
        "verifier": {
          "hash": "8e72c3eddc06391abae742de18ddd85d8a1b75576f57fa56ebca60acf0ee497b",
          "matches": true,
          "tests_hash_after": "b87e9114b4199d9455ade662d10ce1863f7308c514bfbab10b27ac5a53eaba35",
          "tests_hash_before": "b87e9114b4199d9455ade662d10ce1863f7308c514bfbab10b27ac5a53eaba35"
        },
        "wall_ms": 920
      },
      {
        "attempt": 1,
        "cost_usd": 0.92,
        "finished_at": "2026-11-10T15:09:58Z",
        "genome_id": "sha256:6be9015147fea1f3038553e287d519aa58ff9c80b847d2269d12a06b21defb38",
        "provenance": {
          "kind": "fixture",
          "live": false,
          "note": "B1 fixture runtime：确定性回放，不是 live 模型调用",
          "runtime_id": "fixture"
        },
        "run_id": "6be90151-smoke-even-a1-182653",
        "scope": "search",
        "solved": true,
        "started_at": "2026-11-10T15:09:58Z",
        "status": "passed",
        "task_id": "smoke-even",
        "tokens": {
          "in": 1150,
          "out": 400
        },
        "turns": 2,
        "verifier": {
          "hash": "8e72c3eddc06391abae742de18ddd85d8a1b75576f57fa56ebca60acf0ee497b",
          "matches": true,
          "tests_hash_after": "912f7f66209aa3873aeb29a4f42c14033068095f0093fcaee3fe3689566cc493",
          "tests_hash_before": "912f7f66209aa3873aeb29a4f42c14033068095f0093fcaee3fe3689566cc493"
        },
        "wall_ms": 920
      }
    ]
  },
  "scenario": {
    "bench_hash": "0cfbf279bea72e1283b7dafe8c144a78b6934ff5e0f85b74d6f581315fb81dc8",
    "budget": {
      "execution_budget_usd": 5.0,
      "search_overhead_budget_usd": 2.0
    },
    "claim": {
      "holdout_export": "aggregate-only",
      "live_model_calls": false,
      "provenance": "runs[].provenance.kind ∈ {fixture, live, replay}；fixture 不是 live",
      "reproducibility": "指标一致；不承诺字节级复现（run_id 含随机后缀）",
      "runtime": "fixture"
    },
    "ext": {
      "note": "demo 只用 fixture runtime；不产生真实模型调用"
    },
    "holdout": {
      "detail": "not-exported",
      "task_count": 1
    },
    "isolation": {
      "network": "none"
    },
    "not_claimed": [
      "live 模型的质量或成本",
      "跨 scenario_version 的比较",
      "holdout 逐题明细",
      "字节级证据复现"
    ],
    "policy": {
      "budget": {
        "execution_budget_usd": 5.0,
        "search_overhead_budget_usd": 2.0
      },
      "invalid_ratio_max": 0.25,
      "k": 0
    },
    "scenario_id": "demo",
    "scenario_version": "demo-2",
    "schema_version": "1.0.0",
    "shell": {
      "shell_id": "code-repair",
      "shell_version": "1.0.0"
    },
    "task_counts": {
      "report": 1,
      "search": 2,
      "total": 3
    },
    "task_sets": {
      "search": [
        "smoke-add",
        "smoke-even"
      ]
    },
    "title": "B1 demo：两题搜索集 + 一题留出集"
  },
  "search_results": {
    "results": [
      {
        "generated_at": "2026-01-01T00:00:00Z",
        "genome_id": "sha256:6520c6095297d8f26cd23c59f3cd8d322fea13869e5dd1151f53a9bddc438cfa",
        "result_id": "result-baseline-search",
        "scope": "search",
        "scores": {
          "search": {
            "cost_per_solved_usd": 1.84,
            "cost_total_usd": 3.68,
            "invalid_ratio": 0.0,
            "pass_at_1_first_attempt": 1.0,
            "solved": 2,
            "total": 2
          }
        }
      },
      {
        "generated_at": "2026-01-01T00:00:00Z",
        "genome_id": "sha256:6be9015147fea1f3038553e287d519aa58ff9c80b847d2269d12a06b21defb38",
        "result_id": "result-candidate-search",
        "scope": "search",
        "scores": {
          "search": {
            "cost_per_solved_usd": 0.92,
            "cost_total_usd": 1.84,
            "invalid_ratio": 0.0,
            "pass_at_1_first_attempt": 1.0,
            "solved": 2,
            "total": 2
          }
        }
      }
    ],
    "scope": "search"
  },
  "snapshots": {
    "snapshots": []
  },
  "variants": {
    "variants": [
      {
        "adopted": false,
        "budget": {
          "attempts": 1,
          "tokens": 200000,
          "turns": 64,
          "wall_ms": 120000
        },
        "id": "sha256:6520c6095297d8f26cd23c59f3cd8d322fea13869e5dd1151f53a9bddc438cfa",
        "parent": null,
        "runtime": {
          "runtime_id": "fixture",
          "version": "fixture-1"
        },
        "scenario_version": "demo-2",
        "switches": {
          "context": "full",
          "thinking": "high",
          "tools": "default"
        }
      },
      {
        "adopted": false,
        "budget": {
          "attempts": 1,
          "tokens": 200000,
          "turns": 64,
          "wall_ms": 120000
        },
        "id": "sha256:6be9015147fea1f3038553e287d519aa58ff9c80b847d2269d12a06b21defb38",
        "parent": "sha256:6520c6095297d8f26cd23c59f3cd8d322fea13869e5dd1151f53a9bddc438cfa",
        "runtime": {
          "runtime_id": "fixture",
          "version": "fixture-1"
        },
        "scenario_version": "demo-2",
        "switches": {
          "context": "full",
          "thinking": "low",
          "tools": "default"
        }
      }
    ]
  }
};
