def max_of(xs):
    if not xs:
        raise ValueError("empty")
    best = xs[0]
    for x in xs[1:]:
        if x > best:
            best = x
    return best
