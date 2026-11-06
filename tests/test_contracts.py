import unittest
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from core.contracts import (
    CancellationSignal,
    ProviderCapabilities,
    TaskError,
    TaskEvent,
    TaskRequest,
    TaskResult,
    TaskStatus,
)


class ContractTests(unittest.TestCase):
    def test_ids_link_events_and_results_and_hide_content_in_repr(self):
        first = TaskRequest("private question", "text/plain")
        second = TaskRequest("other", "text/plain")
        self.assertNotEqual(first.task_id, second.task_id)
        event = TaskEvent(first.task_id, TaskStatus.CHUNK, text="private answer")
        result = TaskResult(first.task_id, "private answer")
        self.assertEqual(event.task_id, result.task_id)
        self.assertNotEqual(event.task_id, second.task_id)
        self.assertNotIn("private", repr(first) + repr(event) + repr(result))

    def test_event_validation_and_statuses(self):
        task_id = uuid4()
        for status in TaskStatus:
            error = (
                TaskError("error", "failed") if status is TaskStatus.FAILED else None
            )
            self.assertEqual(TaskEvent(task_id, status, error=error).status, status)
        with self.assertRaises(ValueError):
            TaskEvent(task_id, TaskStatus.FAILED)
        with self.assertRaises(ValueError):
            TaskEvent(task_id, TaskStatus.PROGRESS, progress=1.1)

    def test_cancel_is_thread_safe_and_idempotent(self):
        token = CancellationSignal()
        self.assertFalse(token.is_cancelled())
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: token.cancel(), range(100)))
        self.assertTrue(token.is_cancelled())
        self.assertTrue(ProviderCapabilities(streaming=True).streaming)
