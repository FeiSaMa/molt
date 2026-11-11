from solution import mean


def test_fractional_mean():
    assert mean([1, 2]) == 1.5


def test_integer_mean():
    assert mean([2, 4, 6]) == 4.0
