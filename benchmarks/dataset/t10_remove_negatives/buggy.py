def remove_negatives(xs):
    for x in xs:
        if x < 0:
            xs.remove(x)
    return xs
