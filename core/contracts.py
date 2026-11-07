"""Platform-independent task and provider contracts for gradual migration."""

from dataclasses import dataclass, field
from enum import Enum
from threading import Event
from typing import Protocol
from uuid import UUID, uuid4


class TaskStatus(str, Enum):
    WAITING = "waiting"
    STARTED = "started"
    PROGRESS = "progress"
    CHUNK = "chunk"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class TaskError:
    code: str
    message: str
    retryable: bool = False


@dataclass(frozen=True)
class TaskRequest:
    content: bytes | str = field(repr=False)
    media_type: str
    task_id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True)
class TaskResult:
    task_id: UUID
    text: str = field(repr=False)


@dataclass(frozen=True)
class TaskEvent:
    task_id: UUID
    status: TaskStatus
    text: str | None = field(default=None, repr=False)
    progress: float | None = None
    error: TaskError | None = None

    def __post_init__(self):
        if self.progress is not None and not 0 <= self.progress <= 1:
            raise ValueError("progress must be between 0 and 1")
        if (self.status is TaskStatus.FAILED) != (self.error is not None):
            raise ValueError(
                "failed events require an error; other events cannot have one"
            )


@dataclass(frozen=True)
class ProviderCapabilities:
    text: bool = False
    images: bool = False
    audio: bool = False
    streaming: bool = False


class CancellationToken(Protocol):
    def cancel(self) -> None: ...

    def is_cancelled(self) -> bool: ...


class CancellationSignal:
    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def is_set(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout=None) -> bool:
        return self._event.wait(timeout)
