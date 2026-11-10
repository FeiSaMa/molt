from solution import double


def test_double_positive():
    assert double(4) == 8


def test_double_zero():
    assert double(0) == 0
