from solution import count_digits

def test_zero():
    assert count_digits(0) == 1

def test_negative():
    assert count_digits(-9999) == 4

def test_big():
    assert count_digits(10 ** 20) == 21
