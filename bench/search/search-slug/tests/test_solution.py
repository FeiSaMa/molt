from solution import slugify


def test_mixed_case():
    assert slugify(" Hello World ") == "hello-world"


def test_already_slug():
    assert slugify("Already-slug") == "already-slug"
