from solution import sum_to

def test_zero():
    assert sum_to(0) == 0

def test_one():
    assert sum_to(1) == 1

def test_large():
    assert sum_to(100) == 5050
