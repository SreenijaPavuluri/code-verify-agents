from solution import median

def test_even():
    assert median([4, 1, 3, 2]) == 2.5

def test_single():
    assert median([7]) == 7

def test_input_untouched():
    xs = [5, 1, 4]
    median(xs)
    assert xs == [5, 1, 4]
