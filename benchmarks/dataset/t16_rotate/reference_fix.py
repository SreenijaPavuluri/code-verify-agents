def rotate_right(xs, k):
    if not xs:
        return []
    k %= len(xs)
    return xs[-k:] + xs[:-k] if k else list(xs)
