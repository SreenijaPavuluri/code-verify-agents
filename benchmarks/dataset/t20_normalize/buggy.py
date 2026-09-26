def normalize(xs):
    lo = min(xs)
    hi = max(xs)
    return [x - lo / hi for x in xs]
