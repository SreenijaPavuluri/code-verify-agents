from solution import parse_config

def test_equals_in_value():
    assert parse_config("url = http://x?a=b") == {"url": "http://x?a=b"}

def test_comments_and_blank():
    assert parse_config("# c\n\n  k = v  \n") == {"k": "v"}
