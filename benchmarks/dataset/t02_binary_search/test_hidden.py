from solution import binary_search

def test_single():
    assert binary_search([9], 9) == 0

def test_empty():
    assert binary_search([], 1) == -1

def test_every_index():
    a = list(range(0, 40, 2))
    assert all(binary_search(a, v) == i for i, v in enumerate(a))
