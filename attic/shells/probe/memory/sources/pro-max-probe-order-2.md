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

