from solution import is_even


def test_even_holdout():
    assert is_even(10)
    assert not is_even(11)
