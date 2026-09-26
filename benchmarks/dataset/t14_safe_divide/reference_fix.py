def safe_divide(a, b):
    try:
        return a / b
    except ZeroDivisionError:
        return None
