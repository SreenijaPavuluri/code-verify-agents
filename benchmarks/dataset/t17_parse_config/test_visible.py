from solution import parse_config

def test_basic():
    assert parse_config("a = 1\nb=2") == {"a": "1", "b": "2"}
