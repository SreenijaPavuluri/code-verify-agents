def make_multipliers(n):
    return [lambda x, i=i: x * i for i in range(n)]
