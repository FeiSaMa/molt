from solution import unique


def test_preserves_first_seen_order():
    assert unique([3, 1, 3, 2]) == [3, 1, 2]


def test_empty():
    assert unique([]) == []
