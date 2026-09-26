def chunk(xs, size):
    return [xs[i:size] for i in range(0, len(xs), size)]
