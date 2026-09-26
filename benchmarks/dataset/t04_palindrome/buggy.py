def is_palindrome(s):
    cleaned = [c for c in s if c.isalnum()]
    return cleaned == cleaned[::-1]
