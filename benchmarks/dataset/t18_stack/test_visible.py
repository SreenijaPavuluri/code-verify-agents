from solution import Stack

def test_peek_top():
    s = Stack()
    s.push(1)
    s.push(2)
    assert s.peek() == 2
