# Sources: probe-order-2 (probe-6)

四个失败用例来自 `web/data/probe/state.json` 的 `run1`；以下是实际送入反思的 prompt 原文。

```text
下面是同一代码库上一轮失败用例。请只用不超过 5 行描述这个库在分组、去重、排序上的规则。不要提及参数名、返回值类型、函数名或文件路径。不要写代码。

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

- case_id: case_04
  input: ["aaa", "bb", "bb"]
  expected: ["bb", "aaa"]
  actual: ["aaa", "bb"]
  run: r1

```
