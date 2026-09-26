from solution import rotate_right

def test_wraparound():
    assert rotate_right([1, 2, 3], 5) == [2, 3, 1]

def test_zero():
    assert rotate_right([1, 2, 3], 0) == [1, 2, 3]

def test_empty():
    assert rotate_right([], 3) == []
