from solution import first_n


def test_first_two():
    assert first_n([10, 20, 30, 40], 2) == [10, 20]


def test_first_zero():
    assert first_n([1], 0) == []


def test_more_than_length():
    assert first_n([1, 2], 5) == [1, 2]
