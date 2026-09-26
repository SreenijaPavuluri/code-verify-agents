from solution import is_palindrome

def test_sentence():
    assert is_palindrome("A man, a plan, a canal: Panama")

def test_not():
    assert not is_palindrome("Hello")

def test_empty():
    assert is_palindrome("")
