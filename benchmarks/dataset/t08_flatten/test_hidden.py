from solution import flatten

def test_deep():
    assert flatten([[[[1]]], [2, [3, [4, [5]]]]]) == [1, 2, 3, 4, 5]

def test_empty_lists():
    assert flatten([[], [[]], 1]) == [1]
