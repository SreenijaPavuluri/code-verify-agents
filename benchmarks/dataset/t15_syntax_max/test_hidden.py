import unittest
from solution import max_of

class HiddenTests(unittest.TestCase):
    def test_empty(self):
        with self.assertRaises(ValueError):
            max_of([])

    def test_negatives(self):
        self.assertEqual(max_of([-5, -2, -9]), -2)
