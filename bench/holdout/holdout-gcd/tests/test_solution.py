from solution import gcd


def test_common_factor():
    assert gcd(12, 18) == 6


def test_coprime():
    assert gcd(7, 13) == 1


def test_equal():
    assert gcd(9, 9) == 9
