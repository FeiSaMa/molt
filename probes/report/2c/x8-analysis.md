# x8 attribution: baseline-solvable + negative transfer

## 1. Why the baseline passed

Baseline `v0-pro-max` produced two passing runs:

- r1: `return len({name.casefold() for name in names})` — a count (int).
- r2: `return len({name.lower() for name in names})` — a count (int).

The hidden x8 cases all compare an integer:

- `case_01 ["Hotel","ii","HOTEL"] -> 2`
- `case_02 ["cc","bb"] -> 2`
- `case_03 ["dd","dd"] -> 1`

Both baseline implementations return `2/2/1`, so the baseline is 2/2.

## 2. Why the memory runs failed

Both memory-group x8 runs changed the function signature and return type:

```python
def distinct_count(names: list[str], k: int | None = None) -> list[str]:
```

- r1 returns a list of group representatives; when `k` is given it returns `result[:k]`.
- r2 returns a list of representatives, reversed, optionally sliced by `k`.

The checker expects an integer (`2`, `2`, `1`) and receives a list. `actual != expected` for all three cases; the runs are `failed`, not invalid. The failure is a type/semantics regression, not a missing edit: both memory runs did change `distinct_count`.

## 3. Likely memory trigger

The gen2 memory contains task-b (`probe-order-x3`) entries with `k=2` and list outputs:

- `input: ["Gamma","dd","GAMMA","gamma"], k=2`
- `expected: ["dd","GAMMA"]`
- `input: ["D","c","d"], k=2`
- `expected: ["c","D"]`

Those entries describe a list-returning, k-sliced function. The memory x8 solutions added exactly a `k` parameter and a list return, which is consistent with cross-task pattern transfer from task-b. **Causality is [unverified]**: the memory text is the only available evidence, and we did not run a controlled ablation.

## Conclusion

x8 is **baseline-solvable + memory negative transfer**. It must not be counted as positive evidence for memory effectiveness.
