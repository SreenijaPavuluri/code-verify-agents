from solution import normalize

def test_basic():
    assert normalize([2, 4, 6]) == [0.0, 0.5, 1.0]
