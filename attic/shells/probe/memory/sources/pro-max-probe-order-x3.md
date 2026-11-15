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

