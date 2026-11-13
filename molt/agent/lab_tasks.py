"""New coding fixtures for the chat lab.

These fixtures are intentionally **not** part of the demo/probe evidence chain.
They exist only to exercise a real two-round coding loop inside an isolated
workspace: run tests, apply a proposed patch, diff, run tests again.

The mock provider never pretends to be a real model: its patch is the
deterministic ``expected_fix`` below and the run provenance records
``source="mock-deterministic"``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LabTask:
    task_id: str
    title: str
    description: str
    files: dict[str, str] = field(default_factory=dict)
    test_file: str = "test_calc.py"
    expected_fix: dict[str, str] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "files": sorted(self.files),
            "test_file": self.test_file,
            "rounds": 2,
        }


CALC_FILES = {
    "calc.py": (
        "def add(a, b):\n"
        "    return a - b\n"
        "\n\n"
        "def mul(a, b):\n"
        "    return a * b\n"
    ),
    "test_calc.py": (
        "import unittest\n"
        "\n"
        "from calc import add, mul\n"
        "\n"
        "\n"
        "class CalcTest(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(2, 3), 5)\n"
        "\n"
        "    def test_mul(self):\n"
        "        self.assertEqual(mul(3, 4), 12)\n"
        "\n"
        "\n"
        "if __name__ == \"__main__\":\n"
        "    unittest.main()\n"
    ),
}


SLOW_FILES = {
    **CALC_FILES,
    "test_slow.py": (
        "import time\n"
        "import unittest\n"
        "\n"
        "from calc import add\n"
        "\n"
        "\n"
        "class SlowTest(unittest.TestCase):\n"
        "    def test_add_slow(self):\n"
        "        time.sleep(2)\n"
        "        self.assertEqual(add(2, 3), 5)\n"
    ),
}

TASKS: dict[str, LabTask] = {
    "calc-v1": LabTask(
        task_id="calc-v1",
        title="修复 calc.add 并跑两轮测试",
        description=(
            "calc.add 把加法写成了减法。round1 会真实失败；evolution 只替换这一行；"
            "round2 必须真实通过。该 fixture 与 demo/probe 历史数据无关。"
        ),
        files=dict(CALC_FILES),
        test_file="test_calc.py",
        expected_fix={
            "file": "calc.py",
            "old": "    return a - b\n",
            "new": "    return a + b\n",
        },
    ),
    "slow-v1": LabTask(
        task_id="slow-v1",
        title="慢测试：验证取消与两轮 loop",
        description="round1 的慢测试会先 sleep 2s 再失败；用于验收取消与真实进程终止。",
        files=dict(SLOW_FILES),
        test_file="test_slow.py",
        expected_fix={
            "file": "calc.py",
            "old": "    return a - b\n",
            "new": "    return a + b\n",
        },
    ),
}

DEFAULT_TASK = "calc-v1"


def get_task(task_id: str) -> LabTask | None:
    return TASKS.get(task_id)


def public_tasks() -> list[dict[str, Any]]:
    return [TASKS[key].public() for key in sorted(TASKS)]
