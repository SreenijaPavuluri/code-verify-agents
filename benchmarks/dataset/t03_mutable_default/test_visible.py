from solution import add_tag

def test_independent_calls():
    assert add_tag("a") == ["a"]
    assert add_tag("b") == ["b"]
