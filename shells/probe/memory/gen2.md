# Failures observed in the previous run

## task-a

- case_id: case_01
  input: ["Alpha", "bb", "ALPHA", "alpha"]
  expected: ["bb", "ALPHA"]
  actual: ["Alpha", "bb", "ALPHA", "alpha"]
  run: r1

- case_id: case_02
  input: ["B", "a", "A", "b"]
  expected: ["A", "B"]
  actual: ["B", "a", "A", "b"]
  run: r1

- case_id: case_03
  input: ["bb", "aa", "bb"]
  expected: ["aa", "bb"]
  actual: ["bb", "aa"]
  run: r1

## task-b

- case_id: case_01
  input: ["Gamma", "dd", "GAMMA", "gamma"], k=2
  expected: ["dd", "GAMMA"]
  actual: ["GAMMA", "Gamma"]
  run: r1

- case_id: case_02
  input: ["D", "c", "d"], k=2
  expected: ["c", "D"]
  actual: ["D", "c"]
  run: r1

- case_id: case_03
  input: ["eee", "dd", "eee"], k=3
  expected: ["dd", "eee"]
  actual: ["eee", "dd"]
  run: r2
