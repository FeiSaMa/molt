from solution import is_even


def test_even():
    assert is_even(2)
    assert is_even(0)


def test_odd():
    assert not is_even(3)
