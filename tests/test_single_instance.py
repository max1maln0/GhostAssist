import unittest
from uuid import uuid4

from core.single_instance import SingleInstance


class SingleInstanceTests(unittest.TestCase):
    def test_duplicate_activation_and_release(self):
        role = uuid4().hex
        first = SingleInstance(role)
        self.assertTrue(first.primary)
        self.assertTrue(SingleInstance.exists(role))
        second = SingleInstance(role)
        self.assertFalse(second.primary)
        second.activate()
        self.assertTrue(first.activated())
        self.assertFalse(first.activated())
        second.close()
        first.close()
        self.assertFalse(SingleInstance.exists(role))
        replacement = SingleInstance(role)
        try:
            self.assertTrue(replacement.primary)
        finally:
            replacement.close()
