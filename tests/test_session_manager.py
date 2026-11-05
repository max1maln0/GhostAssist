import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from core.session_manager import Interaction, SessionManager


class SessionTests(unittest.TestCase):
    def test_limits_and_modes(self):
        manager = SessionManager(depth=2, max_chars=12)
        generation, history = manager.snapshot("TEST")
        self.assertEqual(history, ())
        cancel = threading.Event()
        for number in range(3):
            self.assertTrue(
                manager.add("TEST", str(number), "answer", generation, cancel)
            )
        _, history = manager.snapshot("TEST")
        self.assertEqual(history, (Interaction("2", "answer"),))
        self.assertEqual(manager.snapshot("INTERVIEW")[1], ())

    def test_cancel_reset_close(self):
        manager = SessionManager()
        generation, _ = manager.snapshot("TEST")
        cancel = threading.Event()
        cancel.set()
        self.assertFalse(manager.add("TEST", "q", "a", generation, cancel))
        cancel.clear()
        manager.reset()
        self.assertFalse(manager.add("TEST", "q", "a", generation, cancel))
        generation, _ = manager.snapshot("TEST")
        manager.close()
        self.assertFalse(manager.add("TEST", "q", "a", generation, cancel))
        manager.reset()
        self.assertEqual(manager.snapshot("TEST")[1], ())

    def test_concurrent_updates(self):
        manager = SessionManager(depth=10)
        generation, _ = manager.snapshot("INTERVIEW")
        cancel = threading.Event()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(
                pool.map(
                    lambda n: manager.add(
                        "INTERVIEW", str(n), "ok", generation, cancel
                    ),
                    range(100),
                )
            )
        self.assertTrue(all(results))
        self.assertEqual(len(manager.snapshot("INTERVIEW")[1]), 10)


if __name__ == "__main__":
    unittest.main()
