"""Task transitions are owned by the UI thread."""

from dataclasses import dataclass, field
from enum import Enum, auto
from threading import Event, Lock


class State(Enum):
    IDLE = auto()
    CAPTURING = auto()
    RECORDING = auto()
    PROCESSING = auto()
    REVIEW = auto()
    STREAMING = auto()
    CANCELLING = auto()


@dataclass
class Task:
    state: State
    stop: Event = field(default_factory=Event)
    cancel: Event = field(default_factory=Event)
    _lock: Lock = field(default_factory=Lock)
    _preview: str | None = None

    def publish(self, text: str):
        with self._lock:
            if not self.cancel.is_set():
                self._preview = text

    def preview(self) -> str | None:
        with self._lock:
            result, self._preview = self._preview, None
            return None if self.cancel.is_set() else result


class TaskState:
    def __init__(self):
        self.active: Task | None = None

    @property
    def state(self):
        return self.active.state if self.active else State.IDLE

    def begin(self, state: State) -> Task | None:
        if self.active is None:
            self.active = Task(state)
            return self.active
        return None

    def cancel(self):
        if self.active:
            self.active.cancel.set()
            self.active.state = State.CANCELLING

    def finish(self, task: Task):
        if self.active is task:
            self.active = None
