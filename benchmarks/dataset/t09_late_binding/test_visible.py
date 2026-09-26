from solution import make_multipliers

def test_values():
    fs = make_multipliers(3)
    assert [f(10) for f in fs] == [0, 10, 20]
