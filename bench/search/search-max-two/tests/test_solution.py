from solution import max_two


def test_first_larger():
    assert max_two(7, 3) == 7


def test_second_larger():
    assert max_two(3, 7) == 7


def test_tie():
    assert max_two(4, 4) == 4
