from solution import normalize

def test_constant():
    assert normalize([3, 3, 3]) == [0.0, 0.0, 0.0]

def test_empty():
    assert normalize([]) == []

def test_negative():
    assert normalize([-10, 0, 10]) == [0.0, 0.5, 1.0]
