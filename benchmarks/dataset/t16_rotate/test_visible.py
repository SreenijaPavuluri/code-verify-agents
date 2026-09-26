from solution import rotate_right

def test_rotate_one():
    assert rotate_right([1, 2, 3, 4], 1) == [4, 1, 2, 3]
