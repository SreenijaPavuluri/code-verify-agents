def normalize(xs):
    if not xs:
        return []
    lo, hi = min(xs), max(xs)
    if hi == lo:
        return [0.0] * len(xs)
    return [(x - lo) / (hi - lo) for x in xs]
