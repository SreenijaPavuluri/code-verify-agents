from solution import fizzbuzz

def test_sequence():
    assert [fizzbuzz(i) for i in range(1, 16)] == [
        "1", "2", "Fizz", "4", "Buzz", "Fizz", "7", "8", "Fizz", "Buzz",
        "11", "Fizz", "13", "14", "FizzBuzz"]

def test_30():
    assert fizzbuzz(30) == "FizzBuzz"
