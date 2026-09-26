from solution import word_count

def test_repeat():
    assert word_count("a b a") == {"a": 2, "b": 1}
