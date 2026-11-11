from solution import total


def test_total_values():
    assert total([1, 2, 3]) == 6


def test_total_empty():
    assert total([]) == 0
