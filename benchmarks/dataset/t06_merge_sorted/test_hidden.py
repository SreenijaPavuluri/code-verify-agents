from solution import merge_sorted

def test_empty_left():
    assert merge_sorted([], [1, 2]) == [1, 2]

def test_dupes():
    assert merge_sorted([1, 1, 5], [1, 6, 7, 8]) == [1, 1, 1, 5, 6, 7, 8]

def test_random():
    import random
    rng = random.Random(0)
    a = sorted(rng.randint(0, 50) for _ in range(20))
    b = sorted(rng.randint(0, 50) for _ in range(13))
    assert merge_sorted(a, b) == sorted(a + b)
