from solution import binary_search

def test_found_last():
    assert binary_search([1, 3, 5, 7], 7) == 3

def test_missing():
    assert binary_search([1, 3, 5], 4) == -1
