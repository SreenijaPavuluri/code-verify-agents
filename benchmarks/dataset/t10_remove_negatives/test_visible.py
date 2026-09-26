from solution import remove_negatives

def test_adjacent_negatives():
    assert remove_negatives([1, -2, -3, 4]) == [1, 4]
