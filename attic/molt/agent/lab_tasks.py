"""New coding fixtures for the chat lab.

These fixtures are intentionally **not** part of the demo/probe evidence chain.
They exist only to exercise a real two-round coding loop inside an isolated
workspace: run tests, apply a proposed patch, diff, run tests again.

The mock provider never pretends to be a real model: its patch is the
deterministic ``expected_fix`` below and the run provenance records
``source="mock-deterministic"``.

Difficulty tiers (title prefix):

* 中等 —— 局部规则错误，读失败断言即可定位，修复 5–20 行。
* 难   —— 需要理解不变量（时序、优先级、原子性），修复 20–50 行。
* 极难 —— 需要重写核心算法（记忆化匹配、完整解析器），修复 50 行以上。

Every task keeps its defect inside a single file so the existing lab protocol
(one JSON patch, one file) can repair it; tests are stdlib ``unittest`` only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from textwrap import dedent
from typing import Any


def _src(text: str) -> str:
    return dedent(text).lstrip("\n")


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


# ---------------------------------------------------------------------------
# 中等：4 道
# ---------------------------------------------------------------------------

MERGE_FILES = {
    "intervals.py": _src(
        r"""
        def merge(intervals):
            ordered = sorted(intervals, key=lambda item: item[0])
            merged = []
            for start, end in ordered:
                if merged and start == merged[-1][1]:
                    merged[-1] = (merged[-1][0], end)
                else:
                    merged.append((start, end))
            return merged
        """
    ),
    "test_intervals.py": _src(
        r"""
        import unittest

        from intervals import merge


        class MergeTest(unittest.TestCase):
            def test_empty(self):
                self.assertEqual(merge([]), [])

            def test_single(self):
                self.assertEqual(merge([(3, 5)]), [(3, 5)])

            def test_touching(self):
                self.assertEqual(merge([(1, 2), (2, 3)]), [(1, 3)])

            def test_overlapping(self):
                self.assertEqual(merge([(1, 4), (2, 3), (5, 6)]), [(1, 4), (5, 6)])

            def test_nested_keeps_longest_end(self):
                self.assertEqual(merge([(1, 10), (2, 3), (4, 5)]), [(1, 10)])

            def test_unsorted_chain(self):
                self.assertEqual(
                    merge([(8, 9), (1, 3), (2, 6), (10, 12)]),
                    [(1, 6), (8, 9), (10, 12)],
                )


        if __name__ == "__main__":
            unittest.main()
        """
    ),
}


DURATION_FILES = {
    "duration.py": _src(
        r"""
        import re

        UNITS = {"d": 86400, "h": 3600, "m": 60, "s": 1}
        PATTERN = re.compile(r"(\d+)([dhms])")


        def parse_duration(text):
            if not text:
                return 0
            total = 0
            for value, unit in PATTERN.findall(text):
                total += int(value) * UNITS[unit]
            return total
        """
    ),
    "test_duration.py": _src(
        r"""
        import unittest

        from duration import parse_duration


        class DurationTest(unittest.TestCase):
            def test_hours_minutes(self):
                self.assertEqual(parse_duration("1h30m"), 5400)

            def test_days_hours(self):
                self.assertEqual(parse_duration("2d4h"), 187200)

            def test_seconds_only(self):
                self.assertEqual(parse_duration("90s"), 90)

            def test_minutes(self):
                self.assertEqual(parse_duration("90m"), 5400)

            def test_missing_unit(self):
                with self.assertRaises(ValueError):
                    parse_duration("45")

            def test_trailing_garbage(self):
                with self.assertRaises(ValueError):
                    parse_duration("1h30")

            def test_unknown_unit(self):
                with self.assertRaises(ValueError):
                    parse_duration("1x")

            def test_duplicate_unit(self):
                with self.assertRaises(ValueError):
                    parse_duration("1h1h")

            def test_wrong_order(self):
                with self.assertRaises(ValueError):
                    parse_duration("30m1h")

            def test_zero_value(self):
                with self.assertRaises(ValueError):
                    parse_duration("0s")

            def test_empty(self):
                with self.assertRaises(ValueError):
                    parse_duration("")

            def test_spaces(self):
                with self.assertRaises(ValueError):
                    parse_duration("1h 30m")


        if __name__ == "__main__":
            unittest.main()
        """
    ),
}


CSV_FILES = {
    "csvish.py": _src(
        r"""
        def parse_line(line):
            if not line:
                return []
            return [field.strip() for field in line.split(",")]
        """
    ),
    "test_csvish.py": _src(
        r"""
        import unittest

        from csvish import parse_line


        class CsvLineTest(unittest.TestCase):
            def test_plain_fields(self):
                self.assertEqual(parse_line("a,b,c"), ["a", "b", "c"])

            def test_empty_fields(self):
                self.assertEqual(parse_line("a,,b"), ["a", "", "b"])
                self.assertEqual(parse_line("a,"), ["a", ""])

            def test_quoted_comma(self):
                self.assertEqual(parse_line('a, "b,c", d'), ["a", "b,c", "d"])

            def test_escaped_quote(self):
                self.assertEqual(
                    parse_line('"he said ""hi""' + '",x'),
                    ['he said "hi"', "x"],
                )

            def test_empty_quoted_field(self):
                self.assertEqual(parse_line('""'), [""])

            def test_outer_spaces_are_trimmed(self):
                self.assertEqual(parse_line(' "a" , b '), ["a", "b"])

            def test_unclosed_quote(self):
                with self.assertRaises(ValueError):
                    parse_line('"unclosed')

            def test_garbage_after_quote(self):
                with self.assertRaises(ValueError):
                    parse_line('a,"b"c')


        if __name__ == "__main__":
            unittest.main()
        """
    ),
}


RETRY_FILES = {
    "retry.py": _src(
        r"""
        import time


        def run_with_retries(operation, attempts, base=0.1, factor=2.0, cap=1.0, sleep=time.sleep):
            delays = []
            for index in range(attempts):
                try:
                    return operation()
                except Exception:
                    delay = min(cap, base * factor ** index)
                    sleep(delay)
                    delays.append(delay)
            raise RuntimeError("retries exhausted")
        """
    ),
    "test_retry.py": _src(
        r"""
        import unittest

        from retry import run_with_retries


        class RetryTest(unittest.TestCase):
            def test_success_first_try(self):
                delays = []
                calls = []

                def operation():
                    calls.append(1)
                    return "ok"

                self.assertEqual(run_with_retries(operation, 3, sleep=delays.append), "ok")
                self.assertEqual(len(calls), 1)
                self.assertEqual(delays, [])

            def test_recovers_after_one_failure(self):
                delays = []
                calls = []

                def operation():
                    calls.append(1)
                    if len(calls) == 1:
                        raise OSError("boom")
                    return 7

                self.assertEqual(
                    run_with_retries(
                        operation,
                        3,
                        base=0.2,
                        factor=3.0,
                        cap=1.0,
                        sleep=delays.append,
                    ),
                    7,
                )
                self.assertEqual(len(calls), 2)
                self.assertEqual(delays, [0.2])

            def test_exhausted_reraises_last_error_without_final_sleep(self):
                delays = []

                def operation():
                    raise OSError("still broken")

                with self.assertRaises(OSError):
                    run_with_retries(
                        operation,
                        3,
                        base=0.25,
                        factor=3.0,
                        cap=1.0,
                        sleep=delays.append,
                    )
                self.assertEqual(delays, [0.25, 0.75])

            def test_single_attempt_does_not_sleep(self):
                delays = []

                def operation():
                    raise ValueError("no retry")

                with self.assertRaises(ValueError):
                    run_with_retries(operation, 1, sleep=delays.append)
                self.assertEqual(delays, [])

            def test_attempts_must_be_positive(self):
                with self.assertRaises(ValueError):
                    run_with_retries(lambda: 1, 0, sleep=lambda _: None)

            def test_cap_applies(self):
                delays = []

                def operation():
                    raise OSError("x")

                with self.assertRaises(OSError):
                    run_with_retries(
                        operation,
                        4,
                        base=0.5,
                        factor=3.0,
                        cap=1.0,
                        sleep=delays.append,
                    )
                self.assertEqual(delays, [0.5, 1.0, 1.0])


        if __name__ == "__main__":
            unittest.main()
        """
    ),
}


# ---------------------------------------------------------------------------
# 难：4 道
# ---------------------------------------------------------------------------

CACHE_FILES = {
    "cache.py": _src(
        r"""
        import time
        from collections import OrderedDict


        class TTLCache:
            def __init__(self, capacity, ttl, clock=time.monotonic):
                self.capacity = capacity
                self.ttl = ttl
                self.clock = clock
                self._data = OrderedDict()

            def get(self, key):
                item = self._data.get(key)
                if item is None:
                    return None
                value, expires_at = item
                if self.clock() >= expires_at:
                    del self._data[key]
                    return None
                return value

            def put(self, key, value):
                if self.capacity <= 0:
                    return
                if key not in self._data and len(self._data) >= self.capacity:
                    self._data.popitem(last=False)
                self._data[key] = (value, self.clock() + self.ttl)

            def __len__(self):
                return len(self._data)
        """
    ),
    "test_cache.py": _src(
        r"""
        import unittest

        from cache import TTLCache


        class FakeClock:
            def __init__(self):
                self.now = 0.0

            def __call__(self):
                return self.now


        class TTLCacheTest(unittest.TestCase):
            def setUp(self):
                self.clock = FakeClock()
                self.cache = TTLCache(2, ttl=10, clock=self.clock)

            def test_get_refreshes_recency(self):
                self.cache.put("a", 1)
                self.cache.put("b", 2)
                self.assertEqual(self.cache.get("a"), 1)
                self.cache.put("c", 3)
                self.assertIsNone(self.cache.get("b"))
                self.assertEqual(self.cache.get("a"), 1)
                self.assertEqual(self.cache.get("c"), 3)

            def test_update_refreshes_recency_and_ttl(self):
                self.cache.put("a", 1)
                self.cache.put("b", 2)
                self.clock.now = 5.0
                self.cache.put("a", 9)
                self.cache.put("c", 3)
                self.assertIsNone(self.cache.get("b"))
                self.clock.now = 11.0
                self.assertEqual(self.cache.get("a"), 9)
                self.clock.now = 16.0
                self.assertIsNone(self.cache.get("a"))

            def test_expiry_boundary(self):
                self.cache.put("a", 1)
                self.clock.now = 10.0
                self.assertIsNone(self.cache.get("a"))

            def test_capacity_one(self):
                cache = TTLCache(1, ttl=10, clock=self.clock)
                cache.put("a", 1)
                cache.put("b", 2)
                self.assertIsNone(cache.get("a"))
                self.assertEqual(cache.get("b"), 2)

            def test_capacity_zero(self):
                cache = TTLCache(0, ttl=10, clock=self.clock)
                cache.put("a", 1)
                self.assertIsNone(cache.get("a"))
                self.assertEqual(len(cache), 0)

            def test_missing(self):
                self.assertIsNone(self.cache.get("nope"))


        if __name__ == "__main__":
            unittest.main()
        """
    ),
}


TOPO_FILES = {
    "topo.py": _src(
        r"""
        def topo_sort(graph):
            seen = set()
            order = []

            def visit(node):
                if node in seen:
                    return
                seen.add(node)
                for dependency in graph.get(node, ()):
                    visit(dependency)
                order.append(node)

            for node in graph:
                visit(node)
            return order
        """
    ),
    "test_topo.py": _src(
        r"""
        import unittest

        from topo import topo_sort


        class TopoSortTest(unittest.TestCase):
            def test_dependencies_come_first(self):
                graph = {"app": ["db", "cache"], "db": ["os"], "cache": ["os"], "os": []}
                self.assertEqual(topo_sort(graph), ["os", "cache", "db", "app"])

            def test_lexicographic_tie_break(self):
                graph = {"b": [], "a": [], "c": ["a", "b"]}
                self.assertEqual(topo_sort(graph), ["a", "b", "c"])

            def test_external_dependency_is_a_node(self):
                graph = {"z": ["y"], "x": []}
                self.assertEqual(topo_sort(graph), ["x", "y", "z"])

            def test_duplicate_edges_are_ignored(self):
                graph = {"b": ["a", "a"], "a": []}
                self.assertEqual(topo_sort(graph), ["a", "b"])

            def test_cycle_is_rejected(self):
                with self.assertRaises(ValueError):
                    topo_sort({"a": ["b"], "b": ["c"], "c": ["a"]})

            def test_empty(self):
                self.assertEqual(topo_sort({}), [])


        if __name__ == "__main__":
            unittest.main()
        """
    ),
}


EXPR_FILES = {
    "expr.py": _src(
        r"""
        import re

        TOKEN = re.compile(r"\d+|[()+\-*/]")


        def evaluate(text):
            tokens = TOKEN.findall(text)
            if not tokens:
                raise ValueError("empty expression")
            value, position = _expr(tokens, 0)
            if position != len(tokens):
                raise ValueError("unexpected token")
            return value


        def _expr(tokens, position):
            value, position = _term(tokens, position)
            while position < len(tokens) and tokens[position] in "+-*/":
                operator = tokens[position]
                right, position = _term(tokens, position + 1)
                if operator == "+":
                    value += right
                elif operator == "-":
                    value -= right
                elif operator == "*":
                    value *= right
                else:
                    if right == 0:
                        raise ZeroDivisionError("division by zero")
                    value /= right
            return value, position


        def _term(tokens, position):
            if position >= len(tokens):
                raise ValueError("unexpected end")
            token = tokens[position]
            if token == "-":
                value, position = _term(tokens, position + 1)
                return -value, position
            if token == "(":
                value, position = _expr(tokens, position + 1)
                if position >= len(tokens) or tokens[position] != ")":
                    raise ValueError("missing closing parenthesis")
                return value, position + 1
            if re.fullmatch(r"\d+", token):
                return int(token), position + 1
            raise ValueError("unexpected token")
        """
    ),
    "test_expr.py": _src(
        r"""
        import unittest

        from expr import evaluate


        class EvaluateTest(unittest.TestCase):
            def test_multiplication_before_addition(self):
                self.assertEqual(evaluate("2+3*4"), 14)

            def test_multiplication_before_addition_reversed(self):
                self.assertEqual(evaluate("2*3+4"), 10)

            def test_long_precedence_chain(self):
                self.assertEqual(evaluate("1+2*3*4"), 25)

            def test_subtraction_with_product(self):
                self.assertEqual(evaluate("10-2*3"), 4)

            def test_parentheses(self):
                self.assertEqual(evaluate("(1+2)*3"), 9)
                self.assertEqual(evaluate("2*(3+4)"), 14)

            def test_unary_minus(self):
                self.assertEqual(evaluate("2*-3"), -6)
                self.assertEqual(evaluate("-(2+3)"), -5)

            def test_left_associative(self):
                self.assertEqual(evaluate("10-4-3"), 3)
                self.assertEqual(evaluate("8/2/2"), 2)

            def test_whitespace(self):
                self.assertEqual(evaluate(" 2 + 3 "), 5)

            def test_division_by_zero(self):
                with self.assertRaises(ZeroDivisionError):
                    evaluate("1/0")

            def test_malformed(self):
                for text in ("2+", "abc", "(1+2", "1 2"):
                    with self.assertRaises(ValueError):
                        evaluate(text)


        if __name__ == "__main__":
            unittest.main()
        """
    ),
}


LEDGER_FILES = {
    "ledger.py": _src(
        r"""
        class Ledger:
            def __init__(self, balances=None):
                self.balances = dict(balances or {})
                self.applied = set()

            def balance(self, account):
                return self.balances.get(account, 0)

            def apply(self, tx_id, entries, allow_overdraft=False):
                for account, delta in entries:
                    self.balances[account] = self.balance(account) + delta
                    if not allow_overdraft and self.balances[account] < 0:
                        raise ValueError("insufficient funds")
                self.applied.add(tx_id)
                return True
        """
    ),
    "test_ledger.py": _src(
        r"""
        import unittest

        from ledger import Ledger


        class LedgerTest(unittest.TestCase):
            def test_transfer(self):
                ledger = Ledger({"cash": 1000, "bank": 0})
                self.assertTrue(ledger.apply("t1", [("cash", -200), ("bank", 200)]))
                self.assertEqual(ledger.balance("cash"), 800)
                self.assertEqual(ledger.balance("bank"), 200)

            def test_duplicate_is_idempotent(self):
                ledger = Ledger({"cash": 1000, "bank": 0})
                ledger.apply("t1", [("cash", -100), ("bank", 100)])
                self.assertFalse(ledger.apply("t1", [("cash", -100), ("bank", 100)]))
                self.assertEqual(ledger.balance("cash"), 900)
                self.assertEqual(ledger.balance("bank"), 100)

            def test_overdraft_rolls_back_all_entries(self):
                ledger = Ledger({"cash": 1000, "bank": 0})
                with self.assertRaises(ValueError):
                    ledger.apply("t2", [("bank", 5000), ("cash", -5000)])
                self.assertEqual(ledger.balance("cash"), 1000)
                self.assertEqual(ledger.balance("bank"), 0)

            def test_allow_overdraft(self):
                ledger = Ledger({"cash": 100})
                ledger.apply("t3", [("cash", -150), ("credit", 150)], allow_overdraft=True)
                self.assertEqual(ledger.balance("cash"), -50)

            def test_unbalanced_entries_are_rejected(self):
                ledger = Ledger({"cash": 100})
                with self.assertRaises(ValueError):
                    ledger.apply("t4", [("cash", -10)])
                self.assertEqual(ledger.balance("cash"), 100)

            def test_unknown_account_starts_at_zero(self):
                ledger = Ledger({"b": 100})
                ledger.apply("t5", [("a", 50), ("b", -50)])
                self.assertEqual(ledger.balance("a"), 50)
                self.assertEqual(ledger.balance("b"), 50)


        if __name__ == "__main__":
            unittest.main()
        """
    ),
}


# ---------------------------------------------------------------------------
# 极难：2 道
# ---------------------------------------------------------------------------

GLOB_FILES = {
    "globmatch.py": _src(
        r"""
        def match(pattern, text):
            if not pattern:
                return not text
            if pattern[0] == "*":
                return match(pattern[1:], text) or (bool(text) and match(pattern, text[1:]))
            if not text:
                return False
            if pattern[0] == "?" or pattern[0] == text[0]:
                return match(pattern[1:], text[1:])
            return False
        """
    ),
    "test_globmatch.py": _src(
        r"""
        import signal
        import unittest

        from globmatch import match


        class GlobTest(unittest.TestCase):
            def test_star(self):
                self.assertTrue(match("a*b", "acccb"))
                self.assertFalse(match("a*b", "accc"))

            def test_question(self):
                self.assertTrue(match("a?c", "abc"))
                self.assertFalse(match("a?c", "ac"))

            def test_plain(self):
                self.assertTrue(match("abc", "abc"))
                self.assertFalse(match("abc", "abd"))

            def test_class(self):
                self.assertTrue(match("[abc]x", "bx"))
                self.assertFalse(match("[abc]x", "dx"))

            def test_range(self):
                self.assertTrue(match("[a-c]x", "bx"))
                self.assertFalse(match("[a-c]x", "dx"))

            def test_negated_class(self):
                self.assertTrue(match("[!a-c]x", "dx"))
                self.assertFalse(match("[!a-c]x", "bx"))

            def test_escape(self):
                self.assertTrue(match(r"\*", "*"))
                self.assertFalse(match(r"\*", "x"))
                self.assertTrue(match(r"a\?b", "a?b"))

            def test_unclosed_class(self):
                with self.assertRaises(ValueError):
                    match("[abc", "a")

            def test_empty(self):
                self.assertTrue(match("", ""))
                self.assertFalse(match("", "a"))

            def test_double_star_collapses(self):
                self.assertTrue(match("a**b", "ab"))
                self.assertTrue(match("a**b", "axxb"))

            def test_pathological_pattern_is_fast(self):
                def handler(signum, frame):
                    raise TimeoutError("glob 匹配过慢")

                previous = signal.signal(signal.SIGALRM, handler)
                signal.alarm(5)
                try:
                    self.assertFalse(match("*a" * 12 + "b", "a" * 300))
                finally:
                    signal.alarm(0)
                    signal.signal(signal.SIGALRM, previous)


        if __name__ == "__main__":
            unittest.main()
        """
    ),
}


MINIJSON_FILES = {
    "minijson.py": _src(
        r"""
        def loads(text):
            value, index = _value(text, 0)
            return value


        def _value(text, index):
            index = _skip_space(text, index)
            if index >= len(text):
                raise ValueError("unexpected end")
            char = text[index]
            if char == '"':
                return _string(text, index)
            if char == "{":
                return _object(text, index)
            if char == "[":
                return _array(text, index)
            if text.startswith("true", index):
                return True, index + 4
            if text.startswith("false", index):
                return False, index + 5
            if text.startswith("null", index):
                return None, index + 4
            return _number(text, index)


        def _skip_space(text, index):
            while index < len(text) and text[index] in " \t\r\n":
                index += 1
            return index


        def _string(text, index):
            index += 1
            start = index
            while index < len(text) and text[index] != '"':
                index += 1
            if index >= len(text):
                raise ValueError("unterminated string")
            return text[start:index], index + 1


        def _number(text, index):
            start = index
            while index < len(text) and text[index] in "-0123456789":
                index += 1
            if start == index:
                raise ValueError("invalid number")
            return int(text[start:index]), index


        def _array(text, index):
            index = _skip_space(text, index + 1)
            items = []
            if index < len(text) and text[index] == "]":
                return items, index + 1
            while True:
                value, index = _value(text, index)
                items.append(value)
                index = _skip_space(text, index)
                if index < len(text) and text[index] == ",":
                    index = _skip_space(text, index + 1)
                    continue
                if index < len(text) and text[index] == "]":
                    return items, index + 1
                raise ValueError("invalid array")


        def _object(text, index):
            index = _skip_space(text, index + 1)
            obj = {}
            if index < len(text) and text[index] == "}":
                return obj, index + 1
            while True:
                if index >= len(text) or text[index] != '"':
                    raise ValueError("invalid object key")
                key, index = _string(text, index)
                index = _skip_space(text, index)
                if index >= len(text) or text[index] != ":":
                    raise ValueError("missing colon")
                value, index = _value(text, index + 1)
                obj[key] = value
                index = _skip_space(text, index)
                if index < len(text) and text[index] == ",":
                    index = _skip_space(text, index + 1)
                    continue
                if index < len(text) and text[index] == "}":
                    return obj, index + 1
                raise ValueError("invalid object")
        """
    ),
    "test_minijson.py": _src(
        r"""
        import unittest

        from minijson import loads


        class MiniJsonTest(unittest.TestCase):
            def test_scalars(self):
                self.assertTrue(loads("true"))
                self.assertFalse(loads("false"))
                self.assertIsNone(loads("null"))

            def test_numbers(self):
                self.assertEqual(loads("0"), 0)
                self.assertEqual(loads("-42"), -42)
                self.assertEqual(loads("2.5"), 2.5)
                self.assertEqual(loads("-3e2"), -300.0)
                self.assertEqual(loads("1E+3"), 1000.0)

            def test_strings(self):
                self.assertEqual(loads('"hello"'), "hello")
                self.assertEqual(loads(r'"a\nb"'), "a\nb")
                self.assertEqual(loads(r'"a\tb"'), "a\tb")
                self.assertEqual(loads(r'"quote: \"x\""'), 'quote: "x"')
                self.assertEqual(loads(r'"\u0041"'), "A")

            def test_unicode_surrogate_pair(self):
                self.assertEqual(loads(r'"\ud83d\ude00"'), "\U0001F600")

            def test_containers(self):
                value = loads('{"a": [1, 2.5, -3e2], "b": {"c": true}, "d": null}')
                self.assertEqual(value, {"a": [1, 2.5, -300.0], "b": {"c": True}, "d": None})

            def test_empty_containers(self):
                self.assertEqual(loads("[]"), [])
                self.assertEqual(loads("{}"), {})

            def test_whitespace(self):
                self.assertEqual(loads('  \n\t{ "a" : [ 1 , 2 ] }  '), {"a": [1, 2]})

            def test_trailing_garbage(self):
                with self.assertRaises(ValueError):
                    loads("1 2")

            def test_leading_zero(self):
                with self.assertRaises(ValueError):
                    loads("01")

            def test_unterminated_structures(self):
                for text in ('"abc', "[1, 2", '{"a": 1', '{"a" 1}'):
                    with self.assertRaises(ValueError):
                        loads(text)

            def test_invalid_escape(self):
                with self.assertRaises(ValueError):
                    loads(r'"\q"')

            def test_control_character_in_string(self):
                with self.assertRaises(ValueError):
                    loads('"a\nb"')

            def test_empty_input(self):
                with self.assertRaises(ValueError):
                    loads("   ")


        if __name__ == "__main__":
            unittest.main()
        """
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
    "interval-merge-v1": LabTask(
        task_id="interval-merge-v1",
        title="中等 · 合并重叠区间（嵌套区间取最大终点）",
        description=(
            "round1 只把「首尾相接」的区间合并，重叠与嵌套区间处理错误，嵌套时还会丢掉更长的终点；"
            "修复 merge 的合并条件与终点取值，round2 覆盖空集、相接、重叠、嵌套与乱序链。"
        ),
        files=dict(MERGE_FILES),
        test_file="test_intervals.py",
        expected_fix={
            "file": "intervals.py",
            "old": (
                "        if merged and start == merged[-1][1]:\n"
                "            merged[-1] = (merged[-1][0], end)\n"
            ),
            "new": (
                "        if merged and start <= merged[-1][1]:\n"
                "            merged[-1] = (merged[-1][0], max(merged[-1][1], end))\n"
            ),
        },
    ),
    "duration-parse-v1": LabTask(
        task_id="duration-parse-v1",
        title="中等 · 时长解析严格校验（顺序 / 重复 / 尾巴垃圾）",
        description=(
            "round1 用 findall 宽松匹配，导致「1h30」「1x」被静默接受、空串返回 0、重复单位与错误顺序也能通过；"
            "修复后必须整串匹配，拒绝缺单位、未知单位、重复单位、乱序、零值与空白。"
        ),
        files=dict(DURATION_FILES),
        test_file="test_duration.py",
        expected_fix={
            "file": "duration.py",
            "content": _src(
                r"""
                import re

                UNITS = {"d": 86400, "h": 3600, "m": 60, "s": 1}
                PATTERN = re.compile(r"(\d+)([dhms])")
                ORDER = "dhms"


                def parse_duration(text):
                    if not text:
                        raise ValueError("empty duration")
                    matches = list(PATTERN.finditer(text))
                    if not matches or "".join(match.group(0) for match in matches) != text:
                        raise ValueError("invalid duration")
                    total = 0
                    previous = -1
                    seen = set()
                    for match in matches:
                        value = int(match.group(1))
                        unit = match.group(2)
                        rank = ORDER.index(unit)
                        if value <= 0 or unit in seen or rank <= previous:
                            raise ValueError("invalid duration")
                        seen.add(unit)
                        previous = rank
                        total += value * UNITS[unit]
                    return total
                """
            ),
        },
    ),
    "csv-line-v1": LabTask(
        task_id="csv-line-v1",
        title="中等 · CSV 行解析（引号、转义、逗号）",
        description=(
            "round1 直接按逗号 split，引号内的逗号、成对双引号转义、引号字段外的空白全部处理错误；"
            "修复为状态机，并拒绝未闭合引号与闭合引号后的多余字符。"
        ),
        files=dict(CSV_FILES),
        test_file="test_csvish.py",
        expected_fix={
            "file": "csvish.py",
            "content": _src(
                r"""
                def parse_line(line):
                    if line == "":
                        return []
                    fields = []
                    index = 0
                    length = len(line)
                    while True:
                        while index < length and line[index] in " \t":
                            index += 1
                        if index < length and line[index] == '"':
                            index += 1
                            parts = []
                            while True:
                                if index >= length:
                                    raise ValueError("unterminated quote")
                                char = line[index]
                                if char == '"':
                                    if index + 1 < length and line[index + 1] == '"':
                                        parts.append('"')
                                        index += 2
                                        continue
                                    index += 1
                                    break
                                parts.append(char)
                                index += 1
                            while index < length and line[index] in " \t":
                                index += 1
                            if index < length and line[index] != ",":
                                raise ValueError("garbage after quote")
                            fields.append("".join(parts))
                        else:
                            start = index
                            while index < length and line[index] != ",":
                                index += 1
                            fields.append(line[start:index].strip())
                        if index >= length:
                            break
                        index += 1
                        if index == length:
                            fields.append("")
                            break
                    return fields
                """
            ),
        },
    ),
    "retry-plan-v1": LabTask(
        task_id="retry-plan-v1",
        title="中等 · 重试与指数退避（最后一次不 sleep、保留原异常）",
        description=(
            "round1 在最后一次失败后仍然 sleep，并把原始异常替换成 RuntimeError，attempts=0 也不校验；"
            "修复后只在还有下一次尝试时按 min(cap, base*factor**n) sleep，耗尽时重新抛出最后一个异常。"
        ),
        files=dict(RETRY_FILES),
        test_file="test_retry.py",
        expected_fix={
            "file": "retry.py",
            "content": _src(
                r"""
                import time


                def run_with_retries(operation, attempts, base=0.1, factor=2.0, cap=1.0, sleep=time.sleep):
                    if attempts < 1:
                        raise ValueError("attempts must be >= 1")
                    for index in range(attempts):
                        try:
                            return operation()
                        except Exception:
                            if index == attempts - 1:
                                raise
                            sleep(min(cap, base * factor ** index))
                    raise RuntimeError("unreachable")
                """
            ),
        },
    ),
    "ttl-lru-cache-v1": LabTask(
        task_id="ttl-lru-cache-v1",
        title="难 · TTL + LRU 缓存（命中刷新时序、更新续期）",
        description=(
            "round1 的 get 命中后不移动到队尾、put 更新已有键也不刷新时序与过期时间，"
            "容量满时会淘汰刚被访问过的键；修复后命中/更新都 move_to_end，更新同时续期，"
            "过期判定保持 clock() >= expires_at。"
        ),
        files=dict(CACHE_FILES),
        test_file="test_cache.py",
        expected_fix={
            "file": "cache.py",
            "content": _src(
                r"""
                import time
                from collections import OrderedDict


                class TTLCache:
                    def __init__(self, capacity, ttl, clock=time.monotonic):
                        self.capacity = capacity
                        self.ttl = ttl
                        self.clock = clock
                        self._data = OrderedDict()

                    def get(self, key):
                        item = self._data.get(key)
                        if item is None:
                            return None
                        value, expires_at = item
                        if self.clock() >= expires_at:
                            del self._data[key]
                            return None
                        self._data.move_to_end(key)
                        return value

                    def put(self, key, value):
                        if self.capacity <= 0:
                            return
                        if key in self._data:
                            self._data.move_to_end(key)
                        elif len(self._data) >= self.capacity:
                            self._data.popitem(last=False)
                        self._data[key] = (value, self.clock() + self.ttl)

                    def __len__(self):
                        return len(self._data)
                """
            ),
        },
    ),
    "topo-order-v1": LabTask(
        task_id="topo-order-v1",
        title="难 · 拓扑排序：字典序稳定输出 + 环检测",
        description=(
            "round1 用 DFS 后序输出，结果合法但不是字典序最小，环也只被 seen 挡住而不会报错；"
            "修复为 Kahn + 最小堆：依赖先于被依赖、同层取字典序最小、重复边只算一次、有环抛 ValueError。"
        ),
        files=dict(TOPO_FILES),
        test_file="test_topo.py",
        expected_fix={
            "file": "topo.py",
            "content": _src(
                r"""
                import heapq


                def topo_sort(graph):
                    nodes = set(graph)
                    for dependencies in graph.values():
                        nodes.update(dependencies)
                    indegree = {node: 0 for node in nodes}
                    dependents = {node: [] for node in nodes}
                    for node, dependencies in graph.items():
                        for dependency in dependencies:
                            indegree[node] += 1
                            dependents[dependency].append(node)
                    ready = [node for node in nodes if indegree[node] == 0]
                    heapq.heapify(ready)
                    order = []
                    while ready:
                        node = heapq.heappop(ready)
                        order.append(node)
                        for child in dependents[node]:
                            indegree[child] -= 1
                            if indegree[child] == 0:
                                heapq.heappush(ready, child)
                    if len(order) != len(nodes):
                        raise ValueError("cycle detected")
                    return order
                """
            ),
        },
    ),
    "expr-eval-v1": LabTask(
        task_id="expr-eval-v1",
        title="难 · 四则表达式求值（优先级、括号、一元负号）",
        description=(
            "round1 把 + - * / 放在同一层从左到右算，2+3*4 会得到 20；"
            "修复为三层递归下降（expr → term → factor），保持左结合、支持一元负号与括号，"
            "除零抛 ZeroDivisionError，残缺表达式抛 ValueError。"
        ),
        files=dict(EXPR_FILES),
        test_file="test_expr.py",
        expected_fix={
            "file": "expr.py",
            "content": _src(
                r"""
                import re

                TOKEN = re.compile(r"\d+|[()+\-*/]")


                def evaluate(text):
                    tokens = TOKEN.findall(text)
                    if not tokens:
                        raise ValueError("empty expression")
                    value, position = _expr(tokens, 0)
                    if position != len(tokens):
                        raise ValueError("unexpected token")
                    return value


                def _expr(tokens, position):
                    value, position = _term(tokens, position)
                    while position < len(tokens) and tokens[position] in "+-":
                        operator = tokens[position]
                        right, position = _term(tokens, position + 1)
                        value = value + right if operator == "+" else value - right
                    return value, position


                def _term(tokens, position):
                    value, position = _factor(tokens, position)
                    while position < len(tokens) and tokens[position] in "*/":
                        operator = tokens[position]
                        right, position = _factor(tokens, position + 1)
                        if operator == "*":
                            value *= right
                        else:
                            if right == 0:
                                raise ZeroDivisionError("division by zero")
                            value /= right
                    return value, position


                def _factor(tokens, position):
                    if position >= len(tokens):
                        raise ValueError("unexpected end")
                    token = tokens[position]
                    if token == "-":
                        value, position = _factor(tokens, position + 1)
                        return -value, position
                    if token == "(":
                        value, position = _expr(tokens, position + 1)
                        if position >= len(tokens) or tokens[position] != ")":
                            raise ValueError("missing closing parenthesis")
                        return value, position + 1
                    if re.fullmatch(r"\d+", token):
                        return int(token), position + 1
                    raise ValueError("unexpected token")
                """
            ),
        },
    ),
    "ledger-apply-v1": LabTask(
        task_id="ledger-apply-v1",
        title="难 · 复式记账：幂等键、余额校验与原子回滚",
        description=(
            "round1 逐条改余额，透支抛错时前面的分录已经生效，重复 tx_id 会再次入账，也不检查分录是否平衡；"
            "修复后先校验幂等与 sum(delta)==0，在副本上计算全部余额，全部合法才一次性提交。"
        ),
        files=dict(LEDGER_FILES),
        test_file="test_ledger.py",
        expected_fix={
            "file": "ledger.py",
            "content": _src(
                r"""
                class Ledger:
                    def __init__(self, balances=None):
                        self.balances = dict(balances or {})
                        self.applied = set()

                    def balance(self, account):
                        return self.balances.get(account, 0)

                    def apply(self, tx_id, entries, allow_overdraft=False):
                        if tx_id in self.applied:
                            return False
                        if sum(delta for _, delta in entries) != 0:
                            raise ValueError("entries must sum to zero")
                        projected = dict(self.balances)
                        for account, delta in entries:
                            projected[account] = projected.get(account, 0) + delta
                            if not allow_overdraft and projected[account] < 0:
                                raise ValueError("insufficient funds")
                        self.balances = projected
                        self.applied.add(tx_id)
                        return True
                """
            ),
        },
    ),
    "glob-match-v1": LabTask(
        task_id="glob-match-v1",
        title="极难 · glob 匹配器（字符类 / 转义 / 记忆化）",
        description=(
            "round1 只认 * 与 ?，[abc]/[a-c]/[!a-c] 被当成普通字符，反斜杠不转义，未闭合字符类不报错，"
            "多星号还会指数回溯；修复为编译 token + 动态规划，支持字符类、范围、取反、转义，"
            "未闭合字符类抛 ValueError，病态 pattern 保持线性。"
        ),
        files=dict(GLOB_FILES),
        test_file="test_globmatch.py",
        expected_fix={
            "file": "globmatch.py",
            "content": _src(
                r"""
                def match(pattern, text):
                    tokens = _compile(pattern)
                    length = len(text)
                    previous = [False] * (length + 1)
                    previous[0] = True
                    for kind, value in tokens:
                        current = [False] * (length + 1)
                        if kind == "star":
                            current[0] = previous[0]
                            for index in range(1, length + 1):
                                current[index] = previous[index] or current[index - 1]
                        else:
                            for index in range(1, length + 1):
                                current[index] = previous[index - 1] and _matches_one(
                                    kind, value, text[index - 1]
                                )
                        previous = current
                    return previous[length]


                def _matches_one(kind, value, char):
                    if kind == "any":
                        return True
                    if kind == "lit":
                        return char == value
                    negated, ranges = value
                    inside = any(start <= char <= end for start, end in ranges)
                    return inside != negated


                def _compile(pattern):
                    tokens = []
                    index = 0
                    while index < len(pattern):
                        char = pattern[index]
                        if char == "*":
                            tokens.append(("star", None))
                            index += 1
                        elif char == "?":
                            tokens.append(("any", None))
                            index += 1
                        elif char == "\\":
                            if index + 1 >= len(pattern):
                                raise ValueError("dangling escape")
                            tokens.append(("lit", pattern[index + 1]))
                            index += 2
                        elif char == "[":
                            index += 1
                            negated = False
                            if index < len(pattern) and pattern[index] == "!":
                                negated = True
                                index += 1
                            ranges = []
                            while index < len(pattern) and pattern[index] != "]":
                                if (
                                    index + 2 < len(pattern)
                                    and pattern[index + 1] == "-"
                                    and pattern[index + 2] != "]"
                                ):
                                    ranges.append((pattern[index], pattern[index + 2]))
                                    index += 3
                                else:
                                    ranges.append((pattern[index], pattern[index]))
                                    index += 1
                            if index >= len(pattern):
                                raise ValueError("unclosed class")
                            index += 1
                            tokens.append(("class", (negated, ranges)))
                        else:
                            tokens.append(("lit", char))
                            index += 1
                    return tokens
                """
            ),
        },
    ),
    "mini-json-v1": LabTask(
        task_id="mini-json-v1",
        title="极难 · 迷你 JSON 解析器（转义、浮点、错误位置）",
        description=(
            "round1 只做最小解析：字符串不处理转义、数字只认整数、忽略尾随垃圾、允许前导零与裸控制字符；"
            "修复为完整递归下降：\\uXXXX 与代理对、\\n/\\t/\\\" 等转义、浮点与指数、"
            "尾随内容、前导零、未闭合结构与非法转义全部抛 ValueError。"
        ),
        files=dict(MINIJSON_FILES),
        test_file="test_minijson.py",
        expected_fix={
            "file": "minijson.py",
            "content": _src(
                r"""
                import re

                NUMBER = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")
                ESCAPES = {
                    '"': '"',
                    "\\": "\\",
                    "/": "/",
                    "b": "\b",
                    "f": "\f",
                    "n": "\n",
                    "r": "\r",
                    "t": "\t",
                }


                def loads(text):
                    value, index = _parse_value(text, 0)
                    index = _skip_space(text, index)
                    if index != len(text):
                        raise ValueError("trailing content")
                    return value


                def _parse_value(text, index):
                    index = _skip_space(text, index)
                    if index >= len(text):
                        raise ValueError("unexpected end")
                    char = text[index]
                    if char == '"':
                        return _parse_string(text, index)
                    if char == "{":
                        return _parse_object(text, index)
                    if char == "[":
                        return _parse_array(text, index)
                    for literal, value in (("true", True), ("false", False), ("null", None)):
                        if text.startswith(literal, index):
                            return value, index + len(literal)
                    return _parse_number(text, index)


                def _skip_space(text, index):
                    while index < len(text) and text[index] in " \t\r\n":
                        index += 1
                    return index


                def _parse_string(text, index):
                    index += 1
                    parts = []
                    while True:
                        if index >= len(text):
                            raise ValueError("unterminated string")
                        char = text[index]
                        if char == '"':
                            return "".join(parts), index + 1
                        if char == "\\":
                            index += 1
                            if index >= len(text):
                                raise ValueError("unterminated escape")
                            escape = text[index]
                            if escape in ESCAPES:
                                parts.append(ESCAPES[escape])
                                index += 1
                            elif escape == "u":
                                code, index = _parse_hex4(text, index + 1)
                                if 0xD800 <= code <= 0xDBFF:
                                    if (
                                        index + 1 >= len(text)
                                        or text[index] != "\\"
                                        or text[index + 1] != "u"
                                    ):
                                        raise ValueError("missing low surrogate")
                                    low, index = _parse_hex4(text, index + 2)
                                    if not 0xDC00 <= low <= 0xDFFF:
                                        raise ValueError("invalid low surrogate")
                                    code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
                                elif 0xDC00 <= code <= 0xDFFF:
                                    raise ValueError("unexpected low surrogate")
                                parts.append(chr(code))
                            else:
                                raise ValueError("invalid escape")
                        else:
                            if ord(char) < 0x20:
                                raise ValueError("control character in string")
                            parts.append(char)
                            index += 1


                def _parse_hex4(text, index):
                    if index + 4 > len(text):
                        raise ValueError("short unicode escape")
                    digits = text[index : index + 4]
                    if any(char not in "0123456789abcdefABCDEF" for char in digits):
                        raise ValueError("invalid unicode escape")
                    return int(digits, 16), index + 4


                def _parse_number(text, index):
                    match = NUMBER.match(text, index)
                    if not match:
                        raise ValueError("invalid number")
                    token = match.group(0)
                    if "." in token or "e" in token or "E" in token:
                        return float(token), match.end()
                    return int(token), match.end()


                def _parse_array(text, index):
                    index = _skip_space(text, index + 1)
                    items = []
                    if index < len(text) and text[index] == "]":
                        return items, index + 1
                    while True:
                        value, index = _parse_value(text, index)
                        items.append(value)
                        index = _skip_space(text, index)
                        if index < len(text) and text[index] == ",":
                            index += 1
                            continue
                        if index < len(text) and text[index] == "]":
                            return items, index + 1
                        raise ValueError("invalid array")


                def _parse_object(text, index):
                    index = _skip_space(text, index + 1)
                    obj = {}
                    if index < len(text) and text[index] == "}":
                        return obj, index + 1
                    while True:
                        index = _skip_space(text, index)
                        if index >= len(text) or text[index] != '"':
                            raise ValueError("invalid object key")
                        key, index = _parse_string(text, index)
                        index = _skip_space(text, index)
                        if index >= len(text) or text[index] != ":":
                            raise ValueError("missing colon")
                        value, index = _parse_value(text, index + 1)
                        obj[key] = value
                        index = _skip_space(text, index)
                        if index < len(text) and text[index] == ",":
                            index += 1
                            continue
                        if index < len(text) and text[index] == "}":
                            return obj, index + 1
                        raise ValueError("invalid object")
                """
            ),
        },
    ),
}


DEFAULT_TASK = "calc-v1"


def get_task(task_id: str) -> LabTask | None:
    return TASKS.get(task_id)


def public_tasks() -> list[dict[str, Any]]:
    return [TASKS[key].public() for key in sorted(TASKS)]
