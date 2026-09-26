from solution import merge_sorted

def test_basic():
    assert merge_sorted([1, 4, 9], [2, 3]) == [1, 2, 3, 4, 9]
