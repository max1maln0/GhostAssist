"""One lazy daemon worker and one bounded result per channel; Tk polls results."""

from queue import Empty, Queue
from threading import Lock, Thread


class JobRunner:
    def __init__(self):
        self._requests = Queue(maxsize=1)
        self._results = Queue(maxsize=1)
        self._lock = Lock()
        self._closed = False
        self.busy = False
        self.thread = None

    def submit(self, work, done, cleanup=lambda value: None):
        with self._lock:
            if self._closed or self.busy:
                return False
            self.busy = True
            self._requests.put_nowait((work, done, cleanup))
            if self.thread is None:
                self.thread = Thread(target=self._run, daemon=True)
                self.thread.start()
            return True

    def _run(self):
        while True:
            with self._lock:
                if self._closed:
                    return
            try:
                work, done, cleanup = self._requests.get(timeout=0.2)
            except Empty:
                continue
            try:
                value, error = work(), None
            except Exception:  # noqa: BLE001 - never transport exception text/secrets
                value, error = None, "Не удалось выполнить фоновую операцию."
            with self._lock:
                closed = self._closed
                if not closed:
                    self._results.put_nowait((value, error, done, cleanup))
            if closed:
                cleanup(value)
                return

    def poll(self):
        try:
            value, error, done, cleanup = self._results.get_nowait()
        except Empty:
            return
        with self._lock:
            self.busy = False
            closed = self._closed
        if closed:
            cleanup(value)
        else:
            try:
                done(value, error)
            except Exception:
                cleanup(value)
                raise

    def close(self):
        with self._lock:
            self._closed = True
        try:
            value, _, _, cleanup = self._results.get_nowait()
        except Empty:
            return
        cleanup(value)
