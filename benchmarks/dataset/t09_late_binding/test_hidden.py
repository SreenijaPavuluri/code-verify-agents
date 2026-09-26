from solution import make_multipliers

def test_len():
    assert len(make_multipliers(5)) == 5

def test_each():
    fs = make_multipliers(6)
    assert all(fs[i](7) == 7 * i for i in range(6))
