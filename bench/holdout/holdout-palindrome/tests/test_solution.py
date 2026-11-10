from solution import is_palindrome


def test_palindrome():
    assert is_palindrome("level")
    assert is_palindrome("Never odd or even")


def test_not_palindrome():
    assert not is_palindrome("molt")
