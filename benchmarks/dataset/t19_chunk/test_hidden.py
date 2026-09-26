from solution import chunk

def test_exact():
    assert chunk(list(range(6)), 3) == [[0, 1, 2], [3, 4, 5]]

def test_empty():
    assert chunk([], 4) == []
