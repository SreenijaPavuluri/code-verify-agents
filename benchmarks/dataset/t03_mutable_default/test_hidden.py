from solution import add_tag

def test_given_list():
    xs = ["x"]
    assert add_tag("y", xs) is xs and xs == ["x", "y"]

def test_many_fresh():
    assert [add_tag(i) for i in range(3)] == [[0], [1], [2]]
