def fizzbuzz(n):
    if n % 3 == 0:
        return "Fizz"
    elif n % 5 == 0:
        return "Buzz"
    elif n % 15 == 0:
        return "FizzBuzz"
    return str(n)
