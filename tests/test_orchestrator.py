import threading
import time
import unittest

from core.contracts import TaskRequest, TaskStatus
from core.orchestrator import Lifecycle, TaskOrchestrator


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.orchestrator = TaskOrchestrator(event_limit=4)
        self.addCleanup(self.orchestrator.close)

    def wait_terminal(self):
        deadline = time.monotonic() + 2
        while (
            self.orchestrator.state is Lifecycle.RUNNING and time.monotonic() < deadline
        ):
            time.sleep(0.005)
        self.assertNotEqual(self.orchestrator.state, Lifecycle.RUNNING)

    def test_success_bounded_events_and_next_task(self):
        request = TaskRequest("q", "text/plain")
        self.orchestrator.prepare(request)

        def work(request, token, publish):
            for i in range(100):
                publish(TaskStatus.CHUNK, str(i))
            return "answer"

        self.orchestrator.start(work, 2)
        self.wait_terminal()
        events = self.orchestrator.drain()
        self.assertLessEqual(len(events), 4)
        self.assertEqual(events[-1].status, TaskStatus.COMPLETED)
        self.assertEqual(events[-1].task_id, request.task_id)

    def test_cancel_before_start_and_duplicate_cancel(self):
        self.orchestrator.prepare(TaskRequest("q", "text/plain"))
        self.assertTrue(self.orchestrator.cancel())
        self.assertFalse(self.orchestrator.cancel())
        with self.assertRaises(RuntimeError):
            self.orchestrator.start(lambda *_: "x", 1)

    def test_cancel_blocks_late_events_and_new_work_until_worker_releases(self):
        entered, release = threading.Event(), threading.Event()
        self.orchestrator.prepare(TaskRequest("q", "text/plain"))

        def work(request, token, publish):
            entered.set()
            release.wait(2)
            publish(TaskStatus.CHUNK, "stale")
            return "stale"

        self.orchestrator.start(work, 1)
        self.assertTrue(entered.wait(1))
        self.assertTrue(self.orchestrator.cancel())
        with self.assertRaises(RuntimeError):
            self.orchestrator.prepare(TaskRequest("new", "text/plain"))
        release.set()
        self.orchestrator._future.result(2)
        self.assertEqual(
            [e.status for e in self.orchestrator.drain()], [TaskStatus.CANCELLED]
        )
        self.orchestrator.prepare(TaskRequest("new", "text/plain"))

    def test_timeout_and_handler_error(self):
        self.orchestrator.prepare(TaskRequest("q", "text/plain"))
        self.orchestrator.start(lambda request, token, publish: time.sleep(0.2), 0.03)
        self.wait_terminal()
        self.assertEqual(self.orchestrator.state, Lifecycle.FAILED)
        self.assertEqual(self.orchestrator.drain()[-1].error.code, "timeout")
        self.orchestrator._future.result(1)
        self.orchestrator.prepare(TaskRequest("next", "text/plain"))
        self.orchestrator.start(lambda *_: 1 / 0, 1)
        self.wait_terminal()
        self.assertEqual(self.orchestrator.drain()[-1].error.code, "handler")

    def test_unread_terminal_event_cannot_be_overwritten(self):
        self.orchestrator.prepare(TaskRequest("q", "text/plain"))
        self.orchestrator.start(lambda *_: "answer", 1)
        self.wait_terminal()
        self.orchestrator._future.result(1)
        with self.assertRaisesRegex(RuntimeError, "consumed"):
            self.orchestrator.prepare(TaskRequest("next", "text/plain"))
        self.assertEqual(self.orchestrator.drain()[-1].status, TaskStatus.COMPLETED)
        self.orchestrator.prepare(TaskRequest("next", "text/plain"))

    def test_close_reports_unfinished_worker(self):
        entered, release = threading.Event(), threading.Event()
        self.orchestrator.prepare(TaskRequest("q", "text/plain"))

        def work(*_):
            entered.set()
            release.wait(1)
            return "stale"

        self.orchestrator.start(work, 2)
        self.assertTrue(entered.wait(1))
        self.assertFalse(self.orchestrator.close(timeout=0.01))
        release.set()
        self.orchestrator._future.result(1)
        self.assertTrue(self.orchestrator.close(timeout=0.1))
