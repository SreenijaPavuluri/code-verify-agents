import unittest
from solution import safe_divide

class HiddenTests(unittest.TestCase):
    def test_type_error(self):
        with self.assertRaises(TypeError):
            safe_divide("a", 2)

    def test_float(self):
        self.assertAlmostEqual(safe_divide(1, 4), 0.25)
