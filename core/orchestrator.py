"""Bounded, GUI-independent task execution with cooperative cancellation."""

from collections import deque
from concurrent.futures import CancelledError, ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock, Timer
from typing import Callable, Protocol

from core.contracts import (
    CancellationSignal,
    TaskError,
    TaskEvent,
    TaskRequest,
    TaskStatus,
)


class Lifecycle(str, Enum):
    WAITING = "waiting"
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TaskFailure(Exception):
    """An explicitly sanitized error intended for presentation."""

    def __init__(self, error: TaskError):
        super().__init__(error.code)
        self.error = error


@dataclass(frozen=True)
class AwaitInput:
    text: str = field(repr=False)


class Handler(Protocol):
    def __call__(
        self,
        request: TaskRequest,
        cancel: CancellationSignal,
        publish: Callable[[TaskStatus, str | None], None],
    ) -> str | AwaitInput: ...


class TaskOrchestrator:
    def __init__(self, event_limit: int = 64):
        if event_limit < 2:
            raise ValueError("event_limit must be at least two")
        self._lock = Lock()
        self._events = deque(maxlen=event_limit)
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._request = None
        self._token = None
        self._state = None
        self._future = None
        self._timer = None
        self._closed = False
        self._phase = 0

    @property
    def state(self):
        with self._lock:
            return self._state

    @property
    def future(self):
        with self._lock:
            return self._future

    @property
    def busy(self):
        with self._lock:
            return self._state in (
                Lifecycle.CREATED,
                Lifecycle.RUNNING,
                Lifecycle.WAITING,
            ) or bool(self._future and not self._future.done())

    def prepare(self, request: TaskRequest):
        with self._lock:
            if self._closed or (self._future and not self._future.done()):
                raise RuntimeError("executor is busy or closed")
            if self._state in (Lifecycle.CREATED, Lifecycle.RUNNING, Lifecycle.WAITING):
                raise RuntimeError("task is already active")
            if self._events:
                raise RuntimeError("previous events must be consumed")
            self._request = request
            self._token = CancellationSignal()
            self._state = Lifecycle.CREATED
            self._events.clear()

    def start(self, handler: Handler, timeout: float):
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        with self._lock:
            if self._closed or self._state not in (
                Lifecycle.CREATED,
                Lifecycle.WAITING,
            ):
                raise RuntimeError("task is not ready")
            if self._future and not self._future.done():
                raise RuntimeError("previous handler has not released resources")
            if self._state is Lifecycle.WAITING and self._events:
                raise RuntimeError("waiting events must be consumed")
            request, token = self._request, self._token
            self._state = Lifecycle.RUNNING
            self._events.append(TaskEvent(request.task_id, TaskStatus.STARTED))
            self._phase += 1
            self._timer = Timer(
                timeout, self._expire, args=(request.task_id, self._phase)
            )
            self._timer.daemon = True
            self._future = self._executor.submit(self._run, request, token, handler)
            self._timer.start()

    def _publish(self, task_id, status, text):
        if status not in (TaskStatus.CHUNK, TaskStatus.PROGRESS):
            raise ValueError("only progress and chunk events can be published")
        with self._lock:
            if self._state is Lifecycle.RUNNING and self._request.task_id == task_id:
                self._events.append(TaskEvent(task_id, status, text=text))

    def _run(self, request, token, handler):
        try:
            result = handler(
                request,
                token,
                lambda status, text=None: self._publish(request.task_id, status, text),
            )
        except TaskFailure as exc:
            self._finish(request.task_id, Lifecycle.FAILED, error=exc.error)
        except Exception:  # noqa: BLE001 - do not leak handler exceptions or secrets
            self._finish(
                request.task_id,
                Lifecycle.FAILED,
                error=TaskError("handler", "Ошибка выполнения задачи."),
            )
        else:
            if isinstance(result, AwaitInput):
                with self._lock:
                    if (
                        self._state is Lifecycle.RUNNING
                        and self._request.task_id == request.task_id
                    ):
                        self._state = Lifecycle.WAITING
                        self._timer.cancel()
                        self._events.append(
                            TaskEvent(
                                request.task_id, TaskStatus.WAITING, text=result.text
                            )
                        )
            else:
                self._finish(request.task_id, Lifecycle.COMPLETED, text=result)

    def _finish(self, task_id, state, *, text=None, error=None):
        with self._lock:
            if self._state is not Lifecycle.RUNNING or self._request.task_id != task_id:
                return False
            self._state = state
            if self._timer:
                self._timer.cancel()
            status = {
                Lifecycle.COMPLETED: TaskStatus.COMPLETED,
                Lifecycle.FAILED: TaskStatus.FAILED,
            }[state]
            self._events.append(TaskEvent(task_id, status, text=text, error=error))
            return True

    def _expire(self, task_id, phase):
        with self._lock:
            if (
                self._state is Lifecycle.RUNNING
                and self._request.task_id == task_id
                and self._phase == phase
            ):
                self._token.cancel()
                self._state = Lifecycle.FAILED
                self._events.clear()
                self._events.append(
                    TaskEvent(
                        task_id,
                        TaskStatus.FAILED,
                        error=TaskError("timeout", "Превышено время обработки."),
                    )
                )

    def cancel(self):
        with self._lock:
            if self._state not in (
                Lifecycle.CREATED,
                Lifecycle.RUNNING,
                Lifecycle.WAITING,
            ):
                return False
            self._token.cancel()
            self._state = Lifecycle.CANCELLED
            if self._timer:
                self._timer.cancel()
            self._events.clear()
            self._events.append(TaskEvent(self._request.task_id, TaskStatus.CANCELLED))
            return True

    def drain(self):
        with self._lock:
            events = list(self._events)
            self._events.clear()
            return events

    def close(self, timeout=0.0):
        with self._lock:
            self._closed = True
        self.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)
        if self._future is not None:
            try:
                self._future.result(timeout=timeout)
            except TimeoutError:
                return False
            except CancelledError:
                pass
        return True
