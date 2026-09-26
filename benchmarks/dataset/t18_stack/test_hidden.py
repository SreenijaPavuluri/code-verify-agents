import unittest
from solution import Stack

class HiddenTests(unittest.TestCase):
    def test_empty_raises(self):
        with self.assertRaises(IndexError):
            Stack().peek()
        with self.assertRaises(IndexError):
            Stack().pop()

    def test_lifo(self):
        s = Stack()
        for i in range(4):
            s.push(i)
        self.assertEqual([s.pop() for _ in range(4)], [3, 2, 1, 0])
        self.assertEqual(len(s), 0)
