from solution import flatten

def test_nested():
    assert flatten([1, [2, 3], 4]) == [1, 2, 3, 4]
