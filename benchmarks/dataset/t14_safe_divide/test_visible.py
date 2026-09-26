from solution import safe_divide

def test_zero():
    assert safe_divide(1, 0) is None

def test_normal():
    assert safe_divide(9, 3) == 3
