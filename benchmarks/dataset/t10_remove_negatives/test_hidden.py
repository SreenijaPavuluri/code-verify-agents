from solution import remove_negatives

def test_not_mutated():
    xs = [-1, 2, -3]
    assert remove_negatives(xs) == [2]
    assert xs == [-1, 2, -3]

def test_all_negative():
    assert remove_negatives([-1, -1, -1, -1]) == []
